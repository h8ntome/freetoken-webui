from __future__ import annotations

import json
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


class DownloadCancelled(Exception):
    pass


class DownloadManager:
    def __init__(self, config: Settings, database: Database):
        self.config = config
        self.db = database
        self._cancel: dict[str, threading.Event] = {}
        self._active_repositories: dict[str, str] = {}
        self._lock = threading.Lock()

    def start(self, repo_id: str, revision: str = "main") -> dict[str, Any]:
        if not repo_id or repo_id.count("/") != 1 or any(x in repo_id for x in ("..", "\\", "\x00")):
            raise ValueError("Expected a Hugging Face repository like publisher/model")
        destination = safe_model_path(self.config, repo_id.replace("/", "--"), must_exist=False)
        if destination.is_dir() and _complete(destination)[0]:
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
        destination = safe_model_path(self.config, repo_id.replace("/", "--"), must_exist=False)
        try:
            self.db.update_job(job_id, state="running", progress={"phase": "reading-metadata", "percent": 0})
            api = HfApi(token=self.config.hf_token)
            info = api.model_info(repo_id, revision=revision, files_metadata=True)

            # Pre-download compatibility check.
            tags = [t for t in (info.tags or [])]
            siblings_list = list(info.siblings) if info.siblings else []
            compat = classify_search_result(repo_id, info.pipeline_tag, tags, siblings_list)
            if compat["compatibility"] == "unsupported":
                reason = compat["unsupportedReason"] or "This model does not appear to use a format currently supported by FreeToken."
                raise RuntimeError(reason)

            files = [s for s in info.siblings if not s.rfilename.startswith((".git", ".cache/"))]
            safetensors = [s for s in files if s.rfilename.endswith(".safetensors")]
            if not safetensors:
                raise RuntimeError("Repository has no safetensors weights that the current FreeToken release can load")
            excluded = (".bin", ".h5", ".msgpack", ".onnx", ".ot", ".gguf")
            files = [s for s in files if not s.rfilename.lower().endswith(excluded)]
            total = sum(int(getattr(s, "size", 0) or 0) for s in files)
            free = shutil.disk_usage(self.config.models_dir).free
            if total and free < total + min(5 * 1024**3, int(total * 0.05)):
                raise RuntimeError(f"Insufficient disk space: need {total:,} bytes plus safety margin; {free:,} bytes free")
            destination.mkdir(parents=True, exist_ok=True)
            state = {"downloaded": 0, "total": total, "started": time.monotonic(), "file": ""}
            for sibling in files:
                if cancel.is_set():
                    raise DownloadCancelled()
                state["file"] = sibling.rfilename
                expected = int(getattr(sibling, "size", 0) or 0)
                self._download_file(job_id, repo_id, revision, destination, sibling.rfilename, expected, total, state, cancel)
                state["downloaded"] += expected
            (destination / ".freetoken-web.json").write_text(json.dumps({"repo_id": repo_id, "revision": revision, "commit": info.sha}, indent=2))
            self.db.update_job(job_id, state="completed", progress={"phase": "complete", "downloadedBytes": total, "totalBytes": total, "percent": 100})
        except DownloadCancelled:
            self.db.update_job(job_id, state="cancelled", progress={"phase": "cancelled"})
        except Exception as exc:
            self.db.update_job(job_id, state="failed", error=_hugging_face_error(exc), progress={"phase": "failed"})
        finally:
            with self._lock:
                self._cancel.pop(job_id, None)
                self._active_repositories.pop(job_id, None)

    def _download_file(self, job_id: str, repo_id: str, revision: str, destination: Path, filename: str, expected: int, total: int, state: dict[str, Any], cancel: threading.Event) -> None:
        final = (destination / filename).resolve(strict=False)
        if not final.is_relative_to(destination):
            raise RuntimeError("Repository contains an unsafe file path")
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.is_file() and (not expected or final.stat().st_size == expected):
            return
        partial = final.with_name(final.name + ".part")
        existing = partial.stat().st_size if partial.exists() else 0
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
                    raise RuntimeError(_hugging_face_status(response.status_code, filename))
                if existing and response.status_code == 200:
                    existing = 0
                mode = "ab" if existing and response.status_code == 206 else "wb"
                seen = existing
                with partial.open(mode) as handle:
                    for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                        if cancel.is_set():
                            raise DownloadCancelled()
                        handle.write(chunk)
                        seen += len(chunk)
                        now = time.monotonic()
                        if now - last_update >= .25:
                            last_update = now
                            done = state["downloaded"] + seen
                            elapsed = max(.001, now - state["started"])
                            speed = done / elapsed
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
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None) or getattr(exc, "status_code", None)
    if status:
        return _hugging_face_status(int(status), "the requested repository")
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return "Hugging Face did not respond in time. Check network access and retry the download."
    if isinstance(exc, (httpx.ConnectError, httpx.NetworkError, OSError)):
        return "The Web UI could not reach Hugging Face. Check DNS and outbound network access."
    text = str(exc).lower()
    if "gated" in text or "private" in text or "401" in text or "403" in text:
        return "This repository is gated or private. Accept its terms and configure a valid HF_TOKEN."
    return "The Hugging Face download failed unexpectedly. Retry the job and check the Web UI logs."
