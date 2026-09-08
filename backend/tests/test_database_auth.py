import time
from pathlib import Path

import pytest
from fastapi import HTTPException, Response

from app.config import Settings
from app.database import Database
from app.security import AuthService


def test_interrupted_jobs_are_reconciled(tmp_path: Path):
    db = Database(tmp_path / "state.sqlite")
    db.initialize()
    job = db.create_job("download", {"repoId": "x/y"})
    db.update_job(job["id"], state="running")
    db.initialize()
    assert db.job(job["id"])["state"] == "failed"


def test_auth_hashes_password_and_sessions(tmp_path: Path):
    db = Database(tmp_path / "state.sqlite")
    db.initialize()
    service = AuthService(Settings(models_dir=tmp_path/"m", data_dir=tmp_path/"d", admin_password="correct horse"), db)
    assert "correct horse" not in service.password_hash
    response = Response()
    principal = service.login("admin", "correct horse", response)
    assert principal.csrf_token
    assert db.one("SELECT * FROM sessions") is not None
    with pytest.raises(HTTPException):
        service.login("admin", "wrong", Response())

