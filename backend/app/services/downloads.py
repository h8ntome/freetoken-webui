from __future__ import annotations

import json
import logging
import re
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

import httpx
from huggingface_hub import HfApi, hf_hub_url

from ..config import Settings
from ..database import Database
from .library import _complete, classify_search_result, safe_model_path


logger = logging.getLogger(__name__)


class RetryableDownload(RuntimeError):
    pass


class DownloadCancelled(Exception):
    pass


class DownloadManager:
    def __init__(self, config: Settings, database: Database):
        self.config = config
        self.db = database
        self._cancel: dict[str, threading.Event] = {}
        self._active_repositories: dict[str, str] = {}
        self._lock = threading.Lock()
        self.on_log = lambda level, message: None

    def active(self, model_id: str) -> bool:
        with self._lock:
            return model_id in [repo.replace("/", "--") for repo in self._active_repositories.values()]

    def start(self, repo_id: str, revision: str = "main") -> dict[str, Any]:
        if not repo_id or repo_id.count("/") != 1 or any(x in repo_id for x in ("..", "\\", "\x00")):
            raise ValueError("Expected a Hugging Face repository like publisher/model")
        destination = safe_model_path(self.config, repo_id.replace("/", "--"), must_exist=False)
        if destination.is_dir() and not (destination / ".download.json").exists() and _complete(destination)[0]:
            raise ValueError("This model is already downloaded. Rescan the library if it is not visible.")
        with self._lock:
            if repo_id in self._active_repositories.values():
                raise ValueError("A download for this model is already in progress")
            job = self.db.create_job("download", {"repoId": repo_id, "revision": revision})
            cancel = threading.Event()
            self._cancel[job["id"]] = cancel
            self._active_repositories[job["id"]] = repo_id
        threading.Thread(target=self._run, args=(job["id"], repo_id, revision, cancel), daemon=True, name=f"download-{job['id'][:8]}").start()
        return job

    def cancel(self, job_id: str) -> None:
        with self._lock:
            event = self._cancel.get(job_id)
        if not event:
            raise ValueError("Download is not active")
        event.set()
        self.db.update_job(job_id, state="cancelling")

    def _run(self, job_id: str, repo_id: str, revision: str, cancel: threading.Event) -> None:
        try:
            destination = safe_model_path(self.config, repo_id.replace("/", "--"), must_exist=False)
            self.db.update_job(job_id, state="running", progress={"phase": "reading-metadata", "percent": 0})
            api = HfApi(token=self.config.hf_token or False)
            info = api.model_info(repo_id, revision=revision, files_metadata=True)
            if cancel.is_set():
                raise DownloadCancelled()
            # Resolve moving refs once so a download never mixes checkpoint revisions.
            revision = info.sha
            manifest = destination / ".download.json"
            if manifest.exists():
                previous = json.loads(manifest.read_text())
                if previous.get("commit") != revision:
                    raise RuntimeError("Repository revision changed since the partial download. Delete the incomplete checkpoint explicitly before downloading the new revision.")

            # Pre-download compatibility check.
            tags = [t for t in (info.tags or [])]
            siblings_list = list(info.siblings) if info.siblings else []
            compat = classify_search_result(repo_id, info.pipeline_tag, tags, siblings_list, getattr(info, "config", None))
            if compat["compatibility"] not in {"verified", "likely"}:
                reason = compat["unsupportedReason"] or "This model does not appear to use a format currently supported by FreeToken."
                raise RuntimeError(reason)

            files = [s for s in info.siblings if not s.rfilename.startswith((".git", ".cache/"))
                     and ("/" not in s.rfilename or s.rfilename.startswith("inference/"))]
            safetensors = [s for s in files if s.rfilename.endswith(".safetensors")]
            if not safetensors:
                raise RuntimeError("Repository has no safetensors weights that the current FreeToken release can load")
            excluded = (".bin", ".h5", ".msgpack", ".onnx", ".ot", ".gguf")
            files = [s for s in files if not s.rfilename.lower().endswith(excluded)]
            total = sum(int(getattr(s, "size", 0) or 0) for s in files)
            remaining = total
            for sibling in files:
                target = destination / sibling.rfilename
                for candidate in (target, target.with_name(target.name + ".part")):
                    if candidate.is_file() and not candidate.is_symlink():
                        remaining -= min(candidate.stat().st_size, int(getattr(sibling, "size", 0) or 0))
                        break
            free = shutil.disk_usage(self.config.models_dir).free
            if total and free < remaining + min(5 * 1024**3, int(total * 0.05)):
                raise RuntimeError(f"Insufficient disk space: need {remaining:,} bytes plus safety margin; {free:,} bytes free")
            destination.mkdir(parents=True, exist_ok=True)
            manifest.write_text(json.dumps({"commit": revision}))
            logger.info("Download %s: %s@%s, %s files, %s bytes", job_id, repo_id, revision, len(files), total)
            state = {"transferred": 0, "downloaded": 0, "total": total, "started": time.monotonic(), "file": ""}
            for sibling in files:
                if cancel.is_set():
                    raise DownloadCancelled()
                state["file"] = sibling.rfilename
                expected = int(getattr(sibling, "size", 0) or 0)
                for attempt in range(3):
                    try:
                        self._download_file(job_id, repo_id, revision, destination, sibling.rfilename, expected, total, state, cancel)
                        break
                    except (httpx.TransportError, RetryableDownload) as exc:
                        if attempt == 2:
                            raise
                        logger.warning("Download %s file %s retry %s: %s", job_id, sibling.rfilename, attempt + 1, exc)
                        self.db.update_job(job_id, progress={"phase": "retrying", "file": sibling.rfilename, "message": str(exc)})
                        if cancel.wait(2 ** attempt):
                            raise DownloadCancelled()
                state["downloaded"] += expected
            complete, issue = _complete(destination)
            if not complete:
                raise RuntimeError(f"Downloaded checkpoint is incomplete: {issue}")
            if cancel.is_set():
                raise DownloadCancelled()
            (destination / ".freetoken-web.json").write_text(json.dumps({"repo_id": repo_id, "revision": revision, "commit": info.sha}, indent=2))
            manifest.unlink(missing_ok=True)
            self.on_log("info", f"Download {repo_id} completed")
            self.db.update_job(job_id, state="completed", progress={"phase": "complete", "downloadedBytes": total, "totalBytes": total, "percent": 100})
        except DownloadCancelled:
            self.db.update_job(job_id, state="cancelled", progress={"phase": "cancelled"})
        except Exception as exc:
            message = _hugging_face_error(exc)
            if self.config.hf_token:
                message = message.replace(self.config.hf_token, "[redacted]")
            logger.error("Download %s %s@%s failed: %s", job_id, repo_id, revision, message, exc_info=not bool(self.config.hf_token))
            self.on_log("error", f"Download {repo_id} ({job_id}): {message}")
            self.db.update_job(job_id, state="failed", error=message, progress={"phase": "failed"})
        finally:
            with self._lock:
                self._cancel.pop(job_id, None)
                self._active_repositories.pop(job_id, None)

    def _download_file(self, job_id: str, repo_id: str, revision: str, destination: Path, filename: str, expected: int, total: int, state: dict[str, Any], cancel: threading.Event) -> None:
        lexical = destination / filename
        if lexical.is_symlink() or lexical.with_name(lexical.name + ".part").is_symlink():
            raise RuntimeError("Refusing to write through a symlink")
        final = lexical.resolve(strict=False)
        if not final.is_relative_to(destination):
            raise RuntimeError("Repository contains an unsafe file path")
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.is_file() and (not expected or final.stat().st_size == expected):
            return
        partial = final.with_name(final.name + ".part")
        existing = partial.stat().st_size if partial.exists() else 0
        if expected and existing == expected:
            partial.replace(final)
            return
        if expected and existing > expected:
            raise RuntimeError(f"Partial file {filename} exceeds its expected size; remove the incomplete checkpoint before retrying")
        headers = {"Authorization": f"Bearer {self.config.hf_token}"} if self.config.hf_token else {}
        if existing:
            headers["Range"] = f"bytes={existing}-"
        url = hf_hub_url(repo_id, filename, revision=revision)
        timeout = httpx.Timeout(connect=30, read=60, write=60, pool=60)
        last_update = 0.0
        with httpx.Client(follow_redirects=True, timeout=timeout, headers=headers) as client:
            with client.stream("GET", url) as response:
                if response.status_code not in (200, 206):
                    response.read()
                    message = _hugging_face_status(response.status_code, filename) + " " + response.text[:500]
                    raise (RetryableDownload if response.status_code == 429 or response.status_code >= 500 else RuntimeError)(message)
                if existing and response.status_code == 200:
                    existing = 0
                if response.status_code == 206 and not response.headers.get("content-range", "").startswith(f"bytes {existing}-"):
                    raise RuntimeError(f"Invalid resume range for {filename}: {response.headers.get('content-range')}")
                mode = "ab" if existing and response.status_code == 206 else "wb"
                seen = existing
                with partial.open(mode) as handle:
                    for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                        if cancel.is_set():
                            raise DownloadCancelled()
                        handle.write(chunk)
                        seen += len(chunk)
                        state["transferred"] += len(chunk)
                        now = time.monotonic()
                        if now - last_update >= .25:
                            last_update = now
                            done = state["downloaded"] + seen
                            elapsed = max(.001, now - state["started"])
                            speed = state["transferred"] / elapsed
                            self.db.update_job(job_id, progress={"phase": "downloading", "file": filename, "downloadedBytes": done, "totalBytes": total, "percent": round(done / total * 100, 1) if total else None, "speedBytesPerSecond": round(speed), "etaSeconds": round((total - done) / speed) if total and speed else None})
                    handle.flush()
                    os.fsync(handle.fileno())
        if expected and partial.stat().st_size != expected:
            raise RuntimeError(f"Size mismatch for {filename}: expected {expected:,} bytes, received {partial.stat().st_size:,}")
        partial.replace(final)


def _hugging_face_status(status: int, filename: str) -> str:
    if status in {401, 403}:
        return (
            f"Hugging Face denied {filename}. This repository may be gated or private; "
            "accept its terms and configure a valid HF_TOKEN."
        )
    if status == 404:
        return f"Hugging Face could not find {filename} at the requested revision."
    if status == 429:
        return "Hugging Face rate limit reached. Retry this download later."
    if status >= 500:
        return "Hugging Face is temporarily unavailable. Retry the download shortly."
    return f"Hugging Face returned HTTP {status} for {filename}."


def _hugging_face_error(exc: Exception) -> str:
    # Preserve the actual failure, but never echo Hub tokens or signed CDN query strings.
    detail = re.sub(r"hf_[A-Za-z0-9]+", "[redacted]", str(exc))
    detail = re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?[redacted]", detail)
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None) or getattr(exc, "status_code", None)
    hint = ""
    if status:
        hint = _hugging_face_status(int(status), "the requested repository") + " "
    elif isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        hint = "Hugging Face did not respond in time. "
    elif isinstance(exc, httpx.NetworkError):
        hint = "Cannot reach Hugging Face; check DNS and outbound access. "
    return f"{hint}{type(exc).__name__}: {detail}"[:3000]
