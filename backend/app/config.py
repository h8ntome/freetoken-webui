from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_name: str = "FreeToken Web"
    web_port: int = 3000
    freetoken_mode: Literal["managed", "external"] = "managed"
    freetoken_host: str = "127.0.0.1"
    freetoken_port: int = 1919
    freetoken_external_url: str = "http://127.0.0.1:1919"
    freetoken_executable: str = "ft"
    freetoken_extra_args: str = ""
    models_dir: Path = Path("/models")
    allowed_import_dirs: str = ""
    data_dir: Path = Path("/data")
    hf_token: str | None = None
    public_api_base_url: str | None = None
    auth_enabled: bool = True
    admin_username: str = "admin"
    admin_password: str = ""
    secure_cookies: bool = False
    metrics_interval_seconds: float = 2.0
    engine_ready_timeout_seconds: int = 900
    engine_stop_timeout_seconds: int = 20

    @field_validator("models_dir", "data_dir", mode="before")
    @classmethod
    def expand_path(cls, value: str | Path) -> Path:
        return Path(value).expanduser().resolve()

    @property
    def engine_url(self) -> str:
        if self.freetoken_mode == "external":
            return self.freetoken_external_url.rstrip("/")
        connect_host = "127.0.0.1" if self.freetoken_host in {"0.0.0.0", "::"} else self.freetoken_host
        return f"http://{connect_host}:{self.freetoken_port}"

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
