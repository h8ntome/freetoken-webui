from __future__ import annotations

import asyncio
import json
import logging
import shlex
import time
from collections import deque
from pathlib import Path
from typing import Any, AsyncIterator

import httpx

from ..config import Settings
from .library import _complete

logger = logging.getLogger(__name__)


class EngineConflict(RuntimeError):
    pass


class EngineManager:
    """Thin HTTP client for upstream ft daemon; inference goes directly to ft serve."""

    def __init__(self, config: Settings):
        self.config = config
        self._lock = asyncio.Lock()
        self._state = "stopped"
        self._model_path = self._model_name = self._started_at = self._error = self._exit_code = None
        self._health: dict[str, Any] = {}
        self._logs: deque[dict[str, Any]] = deque(maxlen=5000)
        self._log_seq = int(time.time() * 1000)
        self._log_task: asyncio.Task | None = None
        self._daemon_reachable = False
        self._runtime_error: str | None = None

    @property
    def control_headers(self) -> dict[str, str]:
        token = self.config.freetoken_daemon_token
        return {"X-FT-Token": token} if token else {}

    @property
    def inference_headers(self) -> dict[str, str]:
        token = self.config.freetoken_api_key
        return {"Authorization": f"Bearer {token}"} if token else {}

    async def initialize(self) -> None:
        await self.refresh()
        if self.config.freetoken_mode == "managed":
            self._log_task = asyncio.create_task(self._follow_logs())

    async def close(self) -> None:
        if self._log_task:
            self._log_task.cancel()
            await asyncio.gather(self._log_task, return_exceptions=True)

    def _build_args(self, model_path: Path, options: dict[str, Any]) -> list[str]:
        allowed = {
            "gpu": "--gpu", "maxRunningRequests": "--max-running-requests",
            "maxOutputTokens": "--max-output-tokens", "maxSequenceLength": "--max-seq-len-override",
            "maxPrefillLength": "--max-prefill-length", "memoryRatio": "--memory-ratio",
            "moeBackend": "--moe-backend", "kvTokens": "--num-tokens", "moeCacheRate": "--moe-cache-rate",
        }
        if set(options) - allowed.keys():
            raise ValueError("Unsupported engine options: " + ", ".join(sorted(set(options) - allowed.keys())))
        args = ["--host", "0.0.0.0", "--served-model-name", model_path.name]
        for key, flag in allowed.items():
            value = options.get(key)
            if value is not None and value != "auto" and value != "":
                args.extend([flag, str(value)])
        return args + shlex.split(self.config.freetoken_extra_args)

    async def _load(self, model_path: Path, options: dict[str, Any] | None, switch: bool) -> dict[str, Any]:
        if self.config.freetoken_mode != "managed":
            raise EngineConflict("Engine lifecycle is unavailable in external mode")
        if (model_path / ".download.json").exists():
            raise ValueError("Download is incomplete; resume it before loading")
        complete, issue = _complete(model_path)
        if not complete:
            raise ValueError(issue or "Checkpoint is incomplete")
        args = self._build_args(model_path, options or {})
        # Mapping is explicit when the daemon sees a different shared-volume mount path.
        remote = self.config.freetoken_models_dir / model_path.relative_to(self.config.models_dir)
        async with self._lock:
            if self._state in {"starting", "loading", "stopping"}:
                raise EngineConflict(f"Engine is currently {self._state}")
            try:
                await self._control_request("POST", "/engine/switch" if switch else "/engine/start",
                                            {"model": str(remote), "port": self.config.freetoken_port,
                                             "args": args})
            except Exception as exc:
                self._append_log("error", f"Model load failed: {exc}")
                await self.refresh()
                raise
            self._state, self._error = "starting", None
            self._model_path, self._model_name = str(model_path), model_path.name
            self._started_at = time.time()
            self._append_log("event", f"Upstream daemon accepted model {remote}")
        return await self.refresh()

    async def start(self, model_path: Path, options: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._load(model_path, options, False)

    async def switch(self, model_path: Path, options: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._load(model_path, options, True)

    async def stop(self) -> dict[str, Any]:
        if self.config.freetoken_mode != "managed":
            raise EngineConflict("Engine lifecycle is unavailable in external mode")
        async with self._lock:
            try:
                await self._control_request("POST", "/engine/stop", {})
            except Exception as exc:
                self._append_log("error", f"Model stop failed: {exc}")
                await self.refresh()
                raise
        return await self.refresh()

    async def refresh(self) -> dict[str, Any]:
        managed = self.config.freetoken_mode == "managed"
        try:
            if managed:
                st = await self._control_request("GET", "/engine/status")
                self._daemon_reachable = True
                remote = st.get("model")
                self._model_name = Path(remote).name if remote else None
                self._model_path = None
                if remote and st.get("running"):
                    try:
                        self._model_path = str(self.config.models_dir / Path(remote).relative_to(self.config.freetoken_models_dir))
                    except ValueError:
                        pass
                self._exit_code = st.get("lastExitCode")
                self._started_at = time.time() - st.get("uptimeS", 0) if st.get("running") else None
                if not st.get("running"):
                    self._health = {}
                    intentionally_stopped = st.get("lastExitReason") == "stopped"
                    self._state = "starting" if st.get("starting") else "stopping" if st.get("stopping") else "failed" if self._exit_code not in (None, 0) and not intentionally_stopped else "stopped"
                    self._error = f"FreeToken exited with code {self._exit_code} ({st.get('lastExitReason')}). {self._runtime_error or 'See engine logs.'}" if self._state == "failed" else None
                    return self.status()
            doc = await self._request_json("GET", self.config.engine_url, "/health", retries=1)
            self._health = doc
            self._error = None
            self._model_name = doc.get("model") or self._model_name
            state = doc.get("status")
            self._state = ("ready" if managed else "external") if state == "ok" else "loading" if state == "loading" else "stopped" if state == "stopped" else "failed"
            if self._state == "failed":
                self._error = doc.get("message") or f"FreeToken health: {doc}"
        except Exception as exc:
            self._health = {}
            self._error = str(exc)
            self._state = "failed"
            if managed and 'st' in locals() and st.get("running"):
                self._state = "loading"
                if (st.get("uptimeS") or 0) > self.config.engine_ready_timeout_seconds:
                    self._state = "failed"
                    self._error = f"Model did not become ready within {self.config.engine_ready_timeout_seconds}s: {exc}"
            elif managed:
                self._daemon_reachable = False
        return self.status()

    def status(self) -> dict[str, Any]:
        progress = self._health.get("progress") or {}
        total = progress.get("total_bytes", progress.get("totalBytes", 0)) or 0
        done = progress.get("done_bytes", progress.get("doneBytes", 0)) or 0
        reachable = self._health.get("status") == "ok"
        managed = self.config.freetoken_mode == "managed"
        return {
            "state": self._state, "mode": self.config.freetoken_mode, "owned": managed,
            "model": self._model_name, "modelPath": self._model_path, "pid": None,
            "startedAt": self._started_at, "exitCode": self._exit_code, "error": self._error,
            "phase": self._health.get("phase"), "progress": {"doneBytes": done, "totalBytes": total, "percent": round(done / total * 100, 1) if total else None},
            "health": self._health, "daemonReachable": self._daemon_reachable,
            "capabilities": {"chat": reachable, "monitoring": reachable, "localModels": managed,
                             "downloads": managed, "lifecycle": managed, "deleteModels": managed},
        }

    def _append_log(self, level: str, message: str) -> None:
        self._log_seq += 1
        self._logs.append({"id": self._log_seq, "timestamp": time.time(), "level": level, "message": message})
        getattr(logger, level if level in {"info", "warning", "error"} else "info")("%s", message)

    def logs(self, since: int = 0, limit: int = 1000) -> list[dict[str, Any]]:
        return [item for item in self._logs if item["id"] > since][-min(max(limit, 1), 2000):]

    async def _follow_logs(self) -> None:
        # Upstream ring cursors reset when the daemon restarts. Replay on reconnect.
        while True:
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(10, read=30)) as client:
                    async with client.stream("GET", f"{self.config.freetoken_control_url}/engine/logs", headers=self.control_headers) as response:
                        response.raise_for_status()
                        async for line in response.aiter_lines():
                            if line.startswith("data:"):
                                rec = json.loads(line[5:])
                                message = rec.get("text") or json.dumps(rec)
                                level = "error" if any(x in message.lower() for x in ("error", "traceback", "out of memory")) else "info"
                                if "serve started" in message:
                                    self._runtime_error = None
                                elif level == "error":
                                    self._runtime_error = message
                                self._append_log(level, message)
            except Exception as exc:
                self._append_log("warning", f"Engine log connection interrupted: {exc}; reconnecting in 5s")
                await asyncio.sleep(5)

    async def proxy_json(self, method: str, path: str, json_body: Any = None) -> Any:
        return await self._request_json(method, self.config.engine_url, path, json_body, retries=1)

    async def chat_stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        timeout = httpx.Timeout(connect=10, read=None, write=30, pool=30)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", f"{self.config.engine_url}/v1/chat/completions", json={**payload, "stream": True}, headers=self.inference_headers) as response:
                    if response.status_code >= 400:
                        body = await response.aread()
                        text = body.decode(errors="replace")[:2000]
                        if response.status_code == 404:
                            raise RuntimeError("FreeToken does not have a model loaded. Load a model from the Models page before starting a chat.")
                        if "out of memory" in text.lower() or "oom" in text.lower():
                            raise RuntimeError("FreeToken ran out of GPU memory while processing the request. Try a smaller model or reduce the context length.")
                        raise RuntimeError(text or f"FreeToken returned HTTP {response.status_code}")
                    async for chunk in response.aiter_raw():
                        yield chunk
        except httpx.ConnectError:
            raise RuntimeError("Cannot connect to FreeToken. The inference engine may not be running. Check the engine status on the Dashboard.")
        except httpx.TimeoutException:
            raise RuntimeError("The request to FreeToken timed out. The engine may be overloaded or still initializing.")

    async def _request_json(
        self,
        method: str,
        base_url: str,
        path: str,
        json_body: Any = None,
        retries: int | None = None,
    ) -> Any:
        attempts = max(1, retries if retries is not None else self.config.engine_proxy_retries) if method == "GET" else 1
        timeout = httpx.Timeout(self.config.engine_connect_timeout_seconds, read=self.config.engine_stop_timeout_seconds + 60 if method != "GET" else 15)
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.request(method, f"{base_url.rstrip('/')}{path}", json=json_body, headers=self.control_headers if base_url == self.config.freetoken_control_url else self.inference_headers)
                if response.status_code >= 500 and attempt + 1 < attempts:
                    await asyncio.sleep(min(2 ** attempt, 4))
                    continue
                if response.status_code >= 400:
                    raise RuntimeError(f"{method} {path}: HTTP {response.status_code}: {response.text[:2000]}")
                return response.json()
            except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    await asyncio.sleep(min(2 ** attempt, 4))
        raise RuntimeError(f"{method} {path}: {type(last_error).__name__}: {last_error}") from last_error

    async def _control_request(self, method: str, path: str, json_body: Any = None) -> Any:
        return await self._request_json(method, self.config.freetoken_control_url, path, json_body)
