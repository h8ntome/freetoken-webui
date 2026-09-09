"""Small service supervisor for the FreeToken container."""

from __future__ import annotations

import json
import os
import signal
import shlex
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.error import URLError
from urllib.request import urlopen


PORT = int(os.getenv("FREETOKEN_PORT", "1919"))
CONTROL_PORT = int(os.getenv("FREETOKEN_CONTROL_PORT", "1918"))
HOST = os.getenv("FREETOKEN_HOST", "0.0.0.0")
EXTRA_ARGS = os.getenv("FREETOKEN_EXTRA_ARGS", "")

process: subprocess.Popen[bytes] | None = None
model: str | None = None
state_lock = Lock()


def health() -> tuple[int, dict]:
    if process is None or process.poll() is not None:
        if process is not None and process.poll() not in (None, 0):
            return 200, {
                "status": "error",
                "message": f"FreeToken exited with code {process.returncode}",
                "model": Path(model).name if model else None,
            }
        return 200, {"status": "stopped", "model": Path(model).name if model else None}
    try:
        with urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2) as response:
            return response.status, json.loads(response.read())
    except (OSError, ValueError, URLError):
        return 200, {"status": "loading", "model": Path(model).name if model else None}


def stop_process() -> None:
    global process
    if process is None or process.poll() is not None:
        process = None
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=int(os.getenv("ENGINE_STOP_TIMEOUT_SECONDS", "20")))
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    process = None


def start_process(payload: dict) -> dict:
    global process, model
    requested = str(payload.get("model") or "").strip()
    if not requested:
        raise ValueError("A shared model path is required")
    requested_path = Path(requested).resolve(strict=False)
    models_root = Path("/models").resolve()
    if not requested_path.is_relative_to(models_root):
        raise ValueError("The model path must remain inside the shared /models volume")
    if not requested_path.is_dir():
        raise ValueError("The selected model directory does not exist")
    options = payload.get("options") or {}
    with state_lock:
        stop_process()
        argv = [
            "ft", "serve", "--model", str(requested_path), "--host", HOST, "--port", str(PORT),
            "--served-model-name", requested_path.name,
        ]
        flags = {
            "maxRunningRequests": "--max-running-requests",
            "maxOutputTokens": "--max-output-tokens",
            "maxSequenceLength": "--max-seq-len-override",
            "maxPrefillLength": "--max-prefill-length",
            "memoryRatio": "--memory-ratio",
            "moeBackend": "--moe-backend",
            "kvTokens": "--num-tokens",
            "moeCacheRate": "--moe-cache-rate",
        }
        for key, flag in flags.items():
            value = options.get(key)
            if value not in (None, "", "auto"):
                argv.extend((flag, str(value)))
        if EXTRA_ARGS:
            argv.extend(shlex.split(EXTRA_ARGS))
        process = subprocess.Popen(argv, start_new_session=True)
        model = str(requested_path)
    return {"status": "starting", "model": Path(requested).name}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_: object) -> None:
        return

    def send_json(self, status: int, value: dict) -> None:
        raw = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        if self.path != "/healthz":
            self.send_json(404, {"detail": "Not found"})
            return
        status, value = health()
        self.send_json(status, value)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/load":
                value = start_process(payload)
            elif self.path == "/unload":
                with state_lock:
                    stop_process()
                value = {"status": "stopped"}
            elif self.path == "/restart":
                value = start_process({"model": model, "options": payload.get("options", {})})
            else:
                self.send_json(404, {"detail": "Not found"})
                return
            self.send_json(202, value)
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            self.send_json(400, {"detail": str(exc)})


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", CONTROL_PORT), Handler)
    try:
        server.serve_forever()
    finally:
        with state_lock:
            stop_process()
        server.server_close()


if __name__ == "__main__":
    main()
