from __future__ import annotations

import asyncio
import json
import os
import shlex
import signal
import socket
import time
from collections import deque
from pathlib import Path
from typing import Any, AsyncIterator

import httpx

from ..config import Settings


class EngineConflict(RuntimeError):
    pass


class EngineManager:
    STATES = {"stopped", "starting", "loading", "ready", "stopping", "failed", "external"}

    def __init__(self, config: Settings):
        self.config = config
        self._lock = asyncio.Lock()
        self._process: asyncio.subprocess.Process | None = None
        self._state = "stopped"
        self._model_path: str | None = None
        self._model_name: str | None = None
        self._started_at: float | None = None
        self._error: str | None = None
        self._exit_code: int | None = None
        self._health: dict[str, Any] = {}
        self._logs: deque[dict[str, Any]] = deque(maxlen=5000)
        self._log_seq = 0
        self._task: asyncio.Task | None = None
        self._intentional_stop = False
        self._state_path = config.data_dir / "engine.json"

    async def initialize(self) -> None:
        if self.config.freetoken_mode == "external":
            self._state = "external"
            await self.refresh()
            return
        # Managed mode owns the *service contract*, not a child process.  The
        # FreeToken process lives in the dedicated GPU service and is reached
        # over the Compose network.
        self._write_state({})
        self._state = "stopped"
        await self.refresh()

    def _read_state(self) -> dict[str, Any]:
        try:
            return json.loads(self._state_path.read_text())
        except (OSError, ValueError):
            return {}

    def _write_state(self, value: dict[str, Any]) -> None:
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(value, indent=2))
        tmp.replace(self._state_path)

    def _is_owned_process(self, pid: int, model: str | None) -> bool:
        try:
            args = [part.decode(errors="replace") for part in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if part]
            joined = " ".join(args)
            is_ft = any(Path(arg).name == "ft" for arg in args) or "freetoken" in joined
            return is_ft and "serve" in args and (not model or model in args)
        except OSError:
            return False

    def _port_open(self) -> bool:
        try:
            host = "127.0.0.1" if self.config.freetoken_host in {"0.0.0.0", "::"} else self.config.freetoken_host
            with socket.create_connection((host, self.config.freetoken_port), timeout=.3):
                return True
        except OSError:
            return False

    async def start(self, model_path: Path, options: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.config.freetoken_mode != "managed" or self._state == "external":
            raise EngineConflict("Engine lifecycle is unavailable in external or unowned mode")
        async with self._lock:
            if self._state in {"starting", "loading", "stopping"}:
                raise EngineConflict(f"Engine is currently {self._state}")
            self._state, self._error, self._exit_code = "starting", None, None
            self._intentional_stop = False
            self._model_path, self._model_name = str(model_path), model_path.name
            self._started_at = time.time()
            self._append_log("event", f"Starting FreeToken for {model_path.name}")
            try:
                await self._control_request(
                    "POST",
                    "/load",
                    {"model": str(model_path), "options": options or {}},
                )
            except Exception as exc:
                self._state, self._error = "failed", f"Could not start the FreeToken service: {exc}"
                raise RuntimeError(self._error) from exc
            self._write_state({"model": str(model_path), "startedAt": self._started_at})
        return self.status()

    def _build_command(self, model_path: Path, options: dict[str, Any]) -> list[str]:
        argv = [self.config.freetoken_executable, "serve", "--model", str(model_path), "--host", self.config.freetoken_host, "--port", str(self.config.freetoken_port), "--served-model-name", model_path.name]
        allowed = {
            "maxRunningRequests": "--max-running-requests", "maxOutputTokens": "--max-output-tokens",
            "maxSequenceLength": "--max-seq-len-override", "maxPrefillLength": "--max-prefill-length", "memoryRatio": "--memory-ratio",
            "moeBackend": "--moe-backend", "kvTokens": "--num-tokens", "moeCacheRate": "--moe-cache-rate",
        }
        for key, flag in allowed.items():
            value = options.get(key)
            if value is not None and value != "auto" and value != "":
                argv.extend((flag, str(value)))
        if self.config.freetoken_extra_args:
            argv.extend(shlex.split(self.config.freetoken_extra_args))
        return argv

    async def switch(self, model_path: Path, options: dict[str, Any] | None = None) -> dict[str, Any]:
        await self.stop()
        return await self.start(model_path, options)

    async def stop(self) -> dict[str, Any]:
        if self.config.freetoken_mode != "managed" or self._state == "external":
            raise EngineConflict("This control plane does not own the engine")
        async with self._lock:
            # Kept for safely draining a process adopted by an older image
            # during an upgrade. New managed starts always use the remote
            # supervisor and never populate ``_process``.
            if self._process and self._process.returncode is None:
                proc = self._process
                self._state = "stopping"
                self._intentional_stop = True
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                    await asyncio.wait_for(proc.wait(), timeout=self.config.engine_stop_timeout_seconds)
                except (ProcessLookupError, asyncio.TimeoutError):
                    if proc.returncode is None:
                        os.killpg(proc.pid, signal.SIGKILL)
                self._state, self._process, self._health = "stopped", None, {}
                self._write_state({})
                return self.status()
            self._state = "stopping"
            self._intentional_stop = True
            self._append_log("event", "Stopping FreeToken gracefully")
            try:
                await self._control_request("POST", "/unload")
            except Exception as exc:
                self._state, self._error = "failed", f"Could not stop the FreeToken service: {exc}"
                raise RuntimeError(self._error) from exc
        self._state, self._process, self._health = "stopped", None, {}
        self._write_state({})
        return self.status()

    async def _supervise(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout is not None
        log_task = asyncio.create_task(self._consume_logs(proc.stdout))
        health_task = asyncio.create_task(self._readiness_loop(proc))
        code = await proc.wait()
        await log_task
        health_task.cancel()
        if self._intentional_stop or self._state == "stopping":
            self._state = "stopped"
        else:
            self._state = "failed"
            self._error = self._useful_log_error() or f"FreeToken exited with code {code}"
            self._append_log("error", self._error)
        self._exit_code, self._process = code, None
        self._write_state({})

    async def _watch_adopted(self, pid: int) -> None:
        while self._is_owned_process(pid, self._model_path):
            await self.refresh()
            await asyncio.sleep(1)
        if self._state not in {"stopped", "stopping"}:
            self._state, self._error = "failed", "Previously managed FreeToken process disappeared"
        self._write_state({})

    async def _consume_logs(self, stream: asyncio.StreamReader) -> None:
        async for raw in stream:
            text = raw.decode(errors="replace").rstrip()
            level = "error" if any(x in text.lower() for x in ("error", "traceback", "cuda out of memory")) else ("warning" if "warn" in text.lower() else "info")
            self._append_log(level, text)

    async def _readiness_loop(self, proc: asyncio.subprocess.Process) -> None:
        deadline = time.monotonic() + self.config.engine_ready_timeout_seconds
        while proc.returncode is None and time.monotonic() < deadline:
            await self.refresh()
            if self._state in {"ready", "failed"}:
                return
            await asyncio.sleep(.75)
        if proc.returncode is None and self._state != "ready":
            self._state = "failed"
            self._error = "FreeToken did not become ready before the configured timeout"
            self._append_log("error", self._error)

    async def refresh(self) -> dict[str, Any]:
        try:
            doc = await self._request_json("GET", self.config.engine_url, "/health")
            self._health = doc
            if doc.get("status") == "ok":
                self._state = "ready" if self.config.freetoken_mode == "managed" and self._state != "external" else "external"
                self._model_name = doc.get("model") or self._model_name
                self._error = None
            elif doc.get("status") == "loading":
                self._state = "loading"
            elif doc.get("status") == "stopped":
                self._state = "stopped" if self.config.freetoken_mode == "managed" else "external"
                self._error = None
            elif doc.get("status") == "error":
                self._state, self._error = "failed", doc.get("message") or "FreeToken reported a fatal error"
        except httpx.ConnectError:
            self._health = {}
            if self.config.freetoken_mode == "external":
                self._error = f"Cannot reach the external FreeToken server at {self.config.engine_url}. Verify the address and that FreeToken is running."
            elif self._state not in {"starting", "loading"}:
                self._error = f"Cannot connect to the managed FreeToken service at {self.config.engine_url}. The service may be starting or restarting."
        except httpx.TimeoutException:
            self._health = {}
            if self._state not in {"starting", "loading"}:
                self._error = f"FreeToken at {self.config.engine_url} did not respond in time. It may still be initializing."
        except Exception as exc:
            self._health = {}
            if self.config.freetoken_mode == "external":
                self._error = f"Cannot reach the external FreeToken server at {self.config.engine_url}: {exc}"
            elif self._state not in {"starting", "loading"}:
                self._error = f"Cannot reach the managed FreeToken service at {self.config.engine_url}: {exc}"
        return self.status()

    def status(self) -> dict[str, Any]:
        progress = self._health.get("progress") or {}
        total = progress.get("total_bytes") or 0
        reachable = self._health.get("status") == "ok"
        managed = self.config.freetoken_mode == "managed"
        owned = managed and self._state != "external"
        return {
            "state": self._state, "mode": self.config.freetoken_mode, "owned": owned,
            "model": self._model_name, "modelPath": self._model_path, "pid": None,
            "startedAt": self._started_at, "exitCode": self._exit_code, "error": self._error,
            "phase": self._health.get("phase"), "progress": {"doneBytes": progress.get("done_bytes", 0), "totalBytes": total, "percent": round(progress.get("done_bytes", 0) / total * 100, 1) if total else None},
            "health": self._health,
            "capabilities": {
                "chat": reachable,
                "monitoring": reachable,
                "localModels": managed,
                "downloads": managed,
                "lifecycle": owned,
                "deleteModels": managed,
            },
        }

    def _append_log(self, level: str, message: str) -> None:
        self._log_seq += 1
        self._logs.append({"id": self._log_seq, "timestamp": time.time(), "level": level, "message": message})

    def logs(self, since: int = 0, limit: int = 1000) -> list[dict[str, Any]]:
        return [item for item in self._logs if item["id"] > since][-min(max(limit, 1), 2000):]

    def _useful_log_error(self) -> str | None:
        errors = [x["message"] for x in self._logs if x["level"] == "error"]
        return errors[-1][-1000:] if errors else None

    async def proxy_json(self, method: str, path: str, json_body: Any = None) -> Any:
        return await self._request_json(method, self.config.engine_url, path, json_body, retries=1)

    async def chat_stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        timeout = httpx.Timeout(connect=10, read=None, write=30, pool=30)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", f"{self.config.engine_url}/v1/chat/completions", json={**payload, "stream": True}) as response:
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
        attempts = max(1, retries if retries is not None else self.config.engine_proxy_retries)
        timeout = httpx.Timeout(self.config.engine_connect_timeout_seconds, read=15)
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.request(method, f"{base_url.rstrip('/')}{path}", json=json_body)
                if response.status_code >= 500 and attempt + 1 < attempts:
                    await asyncio.sleep(min(2 ** attempt, 4))
                    continue
                if response.status_code >= 400:
                    raise RuntimeError(response.text[:2000] or f"HTTP {response.status_code}")
                return response.json()
            except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    await asyncio.sleep(min(2 ** attempt, 4))
        raise RuntimeError(str(last_error or "request failed"))

    async def _control_request(self, method: str, path: str, json_body: Any = None) -> Any:
        return await self._request_json(method, self.config.freetoken_control_url, path, json_body)
