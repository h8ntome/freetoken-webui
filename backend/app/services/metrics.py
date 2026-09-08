from __future__ import annotations

import asyncio
import shutil
import time
from collections import deque
from typing import Any

import psutil

from ..config import Settings
from .engine import EngineManager


class MetricsService:
    def __init__(self, config: Settings, engine: EngineManager):
        self.config, self.engine = config, engine
        self.history: deque[dict[str, Any]] = deque(maxlen=180)
        self._last = 0.0
        self._cached: dict[str, Any] = {}

    async def read(self) -> dict[str, Any]:
        now = time.time()
        if now - self._last < self.config.metrics_interval_seconds and self._cached:
            return self._cached
        vm, disk = psutil.virtual_memory(), shutil.disk_usage(self.config.models_dir)
        system = {
            "cpu": {"percent": psutil.cpu_percent(interval=None), "cores": psutil.cpu_count(logical=True), "load": list(psutil.getloadavg()) if hasattr(psutil, "getloadavg") else []},
            "ram": {"usedBytes": vm.used, "availableBytes": vm.available, "totalBytes": vm.total, "percent": vm.percent},
            "storage": {"usedBytes": disk.used, "freeBytes": disk.free, "totalBytes": disk.total, "percent": round(disk.used / disk.total * 100, 1)},
            "gpus": self._gpus(),
        }
        runtime: dict[str, Any] = {}
        if self.engine.status()["state"] in {"ready", "external"}:
            try:
                runtime = await self.engine.proxy_json("GET", "/v1/stats")
            except Exception:
                runtime = {}
        point = {"timestamp": now, "cpu": system["cpu"]["percent"], "ram": system["ram"]["percent"], "gpu": system["gpus"][0]["utilization"] if system["gpus"] else None, "vram": system["gpus"][0]["memoryPercent"] if system["gpus"] else None, "decodeTps": (runtime.get("throughput") or {}).get("decode_tps", 0), "latency": (runtime.get("requests") or {}).get("p95_ms", 0)}
        self.history.append(point)
        self._cached = {"timestamp": now, "system": system, "runtime": runtime, "history": list(self.history)}
        self._last = now
        return self._cached

    def _gpus(self) -> list[dict[str, Any]]:
        try:
            import pynvml
            pynvml.nvmlInit()
            result = []
            for idx in range(pynvml.nvmlDeviceGetCount()):
                handle = pynvml.nvmlDeviceGetHandleByIndex(idx)
                mem, util = pynvml.nvmlDeviceGetMemoryInfo(handle), pynvml.nvmlDeviceGetUtilizationRates(handle)
                result.append({"index": idx, "name": pynvml.nvmlDeviceGetName(handle), "uuid": pynvml.nvmlDeviceGetUUID(handle), "utilization": util.gpu, "memoryUsedBytes": mem.used, "memoryTotalBytes": mem.total, "memoryPercent": round(mem.used / mem.total * 100, 1), "temperatureC": pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU), "powerWatts": round(pynvml.nvmlDeviceGetPowerUsage(handle) / 1000, 1)})
            return result
        except Exception:
            return []

