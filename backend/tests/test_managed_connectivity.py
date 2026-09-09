from pathlib import Path

import httpx

from app.config import Settings
from app.services.downloads import _hugging_face_error
from app.services.engine import EngineManager


def test_managed_engine_uses_service_dns_url(tmp_path: Path):
    settings = Settings(
        models_dir=tmp_path / "models",
        data_dir=tmp_path / "data",
        auth_enabled=False,
        freetoken_mode="managed",
    )
    assert settings.engine_url == "http://freetoken:1919"
    assert settings.freetoken_control_url == "http://freetoken:1918"


def test_custom_managed_url_is_preserved(tmp_path: Path):
    settings = Settings(
        models_dir=tmp_path / "models",
        data_dir=tmp_path / "data",
        auth_enabled=False,
        freetoken_url="http://runtime:2919/",
    )
    assert settings.engine_url == "http://runtime:2919"


def test_external_mode_still_uses_external_url(tmp_path: Path):
    settings = Settings(
        models_dir=tmp_path / "models",
        data_dir=tmp_path / "data",
        auth_enabled=False,
        freetoken_mode="external",
        freetoken_external_url="https://runtime.example.test/api/",
    )
    assert settings.engine_url == "https://runtime.example.test/api"


def test_managed_status_never_reports_a_local_pid(tmp_path: Path):
    settings = Settings(models_dir=tmp_path / "models", data_dir=tmp_path / "data", auth_enabled=False)
    status = EngineManager(settings).status()
    assert status["pid"] is None
    assert status["owned"] is True


def test_hugging_face_errors_are_actionable():
    denied = httpx.HTTPStatusError("denied", request=httpx.Request("GET", "https://huggingface.co"), response=httpx.Response(403))
    limited = httpx.HTTPStatusError("limited", request=httpx.Request("GET", "https://huggingface.co"), response=httpx.Response(429))
    assert "gated or private" in _hugging_face_error(denied)
    assert "rate limit" in _hugging_face_error(limited)
    assert "did not respond" in _hugging_face_error(httpx.ReadTimeout("timeout"))
