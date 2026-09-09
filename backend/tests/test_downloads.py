import json
from pathlib import Path

import pytest

from app.config import Settings
from app.database import Database
from app.services.downloads import DownloadManager


def test_duplicate_complete_download_is_rejected(tmp_path: Path):
    models, data = tmp_path / "models", tmp_path / "data"
    models.mkdir(); data.mkdir()
    checkpoint = models / "owner--model"
    checkpoint.mkdir()
    (checkpoint / "config.json").write_text(json.dumps({"model_type": "qwen3"}))
    (checkpoint / "model.safetensors").write_bytes(b"weights")
    db = Database(data / "state.sqlite")
    db.initialize()
    manager = DownloadManager(Settings(models_dir=models, data_dir=data, auth_enabled=False), db)

    with pytest.raises(ValueError, match="already downloaded"):
        manager.start("owner/model")
