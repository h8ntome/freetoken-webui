import json
from pathlib import Path

import pytest

from app.config import Settings
from app.services.library import ModelLibrary, safe_model_path


def config(tmp_path: Path) -> Settings:
    models = tmp_path / "models"
    data = tmp_path / "data"
    models.mkdir()
    data.mkdir()
    return Settings(models_dir=models, data_dir=data, auth_enabled=False)


def make_model(path: Path) -> None:
    path.mkdir()
    (path / "config.json").write_text(json.dumps({"architectures": ["Qwen3ForCausalLM"], "quantization_config": {"quant_method": "fp8"}}))
    (path / "model.safetensors").write_bytes(b"weights")


def test_discovers_only_complete_models(tmp_path: Path):
    cfg = config(tmp_path)
    make_model(cfg.models_dir / "Qwen3-30B-A3B-FP8")
    (cfg.models_dir / "partial").mkdir()
    models = ModelLibrary(cfg).scan()
    assert [m["name"] for m in models] == ["partial", "Qwen3-30B-A3B-FP8"]
    complete = next(m for m in models if m["name"].startswith("Qwen"))
    assert complete["status"] == "downloaded"
    assert complete["compatibility"] == "likely"
    assert complete["quantization"] == "fp8"
    assert next(m for m in models if m["name"] == "partial")["status"] == "failed"


@pytest.mark.parametrize("model_id", ["../outside", "/etc", "ok/../../../etc", "\x00bad"])
def test_path_traversal_is_rejected(tmp_path: Path, model_id: str):
    with pytest.raises(ValueError):
        safe_model_path(config(tmp_path), model_id, must_exist=False)


def test_delete_never_follows_symlink(tmp_path: Path):
    cfg = config(tmp_path)
    outside = tmp_path / "outside"
    make_model(outside)
    (cfg.models_dir / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="linked model"):
        ModelLibrary(cfg).delete("linked")
    assert outside.exists()


def test_index_with_missing_shard_is_incomplete(tmp_path: Path):
    cfg = config(tmp_path)
    path = cfg.models_dir / "broken"
    path.mkdir()
    (path / "config.json").write_text('{"model_type":"qwen3"}')
    (path / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {"x": "missing.safetensors"}}))
    model = ModelLibrary(cfg).scan()[0]
    assert model["status"] == "failed"
    assert "Missing 1" in model["issue"]
