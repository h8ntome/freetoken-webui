from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_name: str = "FreeToken WebUI"
    web_port: int = 3000
    freetoken_mode: Literal["managed", "external"] = "managed"
    # The managed runtime is a separate Compose service.  Keep the URL
    # configurable so the control plane also works with a separately deployed
    # FreeToken service.
    freetoken_url: str = "http://freetoken:1919"
    freetoken_control_url: str = "http://freetoken:1900"
    freetoken_port: int = 1919
    freetoken_external_url: str = "http://127.0.0.1:1919"
    freetoken_daemon_token: str | None = None
    freetoken_api_key: str | None = None
    freetoken_models_dir: Path = Path("/models")
    freetoken_extra_args: str = ""
    models_dir: Path = Path("./models")
    allowed_import_dirs: str = ""
    data_dir: Path = Path("./data")
    hf_token: str | None = None
    public_api_base_url: str | None = None
    auth_enabled: bool = False
    admin_username: str = "admin"
    admin_password: str = ""
    secure_cookies: bool = False
    metrics_interval_seconds: float = 2.0
    engine_ready_timeout_seconds: int = 900
    engine_stop_timeout_seconds: int = 20
    engine_connect_timeout_seconds: float = 5.0
    engine_proxy_retries: int = 3

    @field_validator("hf_token", "freetoken_daemon_token", "freetoken_api_key", mode="before")
    @classmethod
    def normalize_token(cls, value):
        return str(value).strip() or None if value is not None else None

    @field_validator("models_dir", "data_dir", mode="before")
    @classmethod
    def expand_path(cls, value: str | Path) -> Path:
        return Path(value).expanduser().resolve()

    @property
    def engine_url(self) -> str:
        if self.freetoken_mode == "external":
            return self.freetoken_external_url.rstrip("/")
        return self.freetoken_url.rstrip("/")

    @property
    def import_roots(self) -> tuple[Path, ...]:
        roots = [self.models_dir]
        roots.extend(Path(item.strip()).expanduser().resolve() for item in self.allowed_import_dirs.split(",") if item.strip())
        return tuple(dict.fromkeys(roots))

    def prepare(self) -> None:
        if self.auth_enabled and not self.admin_password:
            raise RuntimeError("AUTH_ENABLED=true requires ADMIN_PASSWORD")
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "logs").mkdir(parents=True, exist_ok=True)


settings = Settings()
