from pathlib import Path
import asyncio
import sys

import pytest

from app.config import Settings
from app.services.engine import EngineManager


def manager(tmp_path: Path) -> EngineManager:
    models, data = tmp_path / "models", tmp_path / "data"
    models.mkdir(); data.mkdir()
    return EngineManager(Settings(models_dir=models, data_dir=data, auth_enabled=False, freetoken_executable="ft"))


def test_command_is_argv_and_only_allows_known_options(tmp_path: Path):
    mgr = manager(tmp_path)
    model = tmp_path / "models" / "model;touch PWNED"
    argv = mgr._build_command(model, {"memoryRatio": .8, "moeBackend": "hybrid", "evil": "$(id)"})
    assert argv[:4] == ["ft", "serve", "--model", str(model)]
    assert "--memory-ratio" in argv and "0.8" in argv
    assert "--moe-strategy" in argv and "hybrid" in argv
    assert "$(id)" not in argv
    assert model.name in argv


@pytest.mark.asyncio
async def test_double_start_is_locked(tmp_path: Path, monkeypatch):
    mgr = manager(tmp_path)
    mgr._state = "loading"
    with pytest.raises(Exception, match="currently loading"):
        await mgr.start(tmp_path / "models" / "x")


def test_log_ring_is_bounded(tmp_path: Path):
    mgr = manager(tmp_path)
    for i in range(5100):
        mgr._append_log("info", str(i))
    assert len(mgr.logs(limit=2000)) == 2000
    assert mgr.logs(limit=1)[0]["message"] == "5099"


def test_external_mode_advertises_no_local_management(tmp_path: Path):
    models, data = tmp_path / "models", tmp_path / "data"
    models.mkdir(); data.mkdir()
    mgr = EngineManager(Settings(models_dir=models, data_dir=data, auth_enabled=False, freetoken_mode="external"))
    status = mgr.status()
    assert status["owned"] is False
    assert status["capabilities"]["lifecycle"] is False
    assert status["capabilities"]["downloads"] is False
    assert status["capabilities"]["deleteModels"] is False


@pytest.mark.asyncio
async def test_crash_is_detected_and_state_lock_is_released(tmp_path: Path, monkeypatch):
    mgr = manager(tmp_path)
    proc = await asyncio.create_subprocess_exec(sys.executable, "-c", "raise SystemExit(7)", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True)
    mgr._process, mgr._state, mgr._model_path = proc, "loading", "/models/test"
    async def no_readiness(_):
        return None
    monkeypatch.setattr(mgr, "_readiness_loop", no_readiness)
    await mgr._supervise(proc)
    assert mgr.status()["state"] == "failed"
    assert mgr.status()["exitCode"] == 7
    assert mgr._process is None


@pytest.mark.asyncio
async def test_owned_process_shutdown_is_graceful(tmp_path: Path, monkeypatch):
    mgr = manager(tmp_path)
    proc = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time; time.sleep(30)", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True)
    mgr._process, mgr._state, mgr._model_path = proc, "ready", "/models/test"
    monkeypatch.setattr(mgr, "_is_owned_process", lambda pid, model: proc.returncode is None)
    supervise = asyncio.create_task(mgr._supervise(proc))
    await mgr.stop()
    await supervise
    assert mgr.status()["state"] == "stopped"
    assert mgr.status()["exitCode"] is not None
