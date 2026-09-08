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
from .library import safe_model_path


class DownloadCancelled(Exception):
    pass


class DownloadManager:
    def __init__(self, config: Settings, database: Database):
        self.config = config
        self.db = database
        self._cancel: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def start(self, repo_id: str, revision: str = "main") -> dict[str, Any]:
        if not repo_id or repo_id.count("/") != 1 or any(x in repo_id for x in ("..", "\\", "\x00")):
            raise ValueError("Expected a Hugging Face repository like publisher/model")
        job = self.db.create_job("download", {"repoId": repo_id, "revision": revision})
        cancel = threading.Event()
        with self._lock:
            self._cancel[job["id"]] = cancel
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
            files = [s for s in info.siblings if not s.rfilename.startswith((".git", ".cache/"))]
            safetensors = [s for s in files if s.rfilename.endswith(".safetensors")]
            gguf = [s for s in files if s.rfilename.lower().endswith(".gguf")]
            if not safetensors and not gguf:
                raise RuntimeError("Repository has no safetensors or GGUF weights that FreeToken can load")
            if not safetensors and len(gguf) > 1:
                raise RuntimeError("Repository contains multiple GGUF variants; download a specific compatible checkpoint instead")
            if safetensors:
                excluded = (".bin", ".h5", ".msgpack", ".onnx", ".ot", ".gguf")
                files = [s for s in files if not s.rfilename.lower().endswith(excluded)]
            else:
                selected = gguf[0].rfilename
                files = [s for s in files if not s.rfilename.lower().endswith(".gguf") or s.rfilename == selected]
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
            message = str(exc)
            if "401" in message or "gated" in message.lower():
                message = "This repository is gated or private. Accept its terms and configure a valid HF_TOKEN."
            self.db.update_job(job_id, state="failed", error=message, progress={"phase": "failed"})
        finally:
            with self._lock:
                self._cancel.pop(job_id, None)

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
                    raise RuntimeError(f"Hugging Face returned HTTP {response.status_code} for {filename}")
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
