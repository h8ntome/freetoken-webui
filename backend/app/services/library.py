from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from ..config import Settings


KNOWN_GOOD = [
    {"repo": "deepseek-ai/DeepSeek-V4-Flash-0731", "family": "DeepSeek-V4", "notes": "Requires the inference/config.json subdirectory."},
    {"repo": "RedHatAI/GLM-5.3-Flash-NVFP4", "family": "GLM-5.3-Flash"},
    {"repo": "nvidia/GLM-5.2-NVFP4", "family": "GLM-5.2"},
    {"repo": "nvidia/GLM-4.7-NVFP4", "family": "GLM-4.7"},
    {"repo": "Qwen/Qwen3.8-Flash-Next-FP8", "family": "Qwen3.8-Flash-Next", "notes": "Pins an approximately 47.7 GiB PLE table in host RAM."},
    {"repo": "RadixArk/Qwen3.8-Flash-Next-NVFP4", "family": "Qwen3.8-Flash-Next", "notes": "Pins an approximately 47.7 GiB PLE table in host RAM."},
    {"repo": "Qwen/Qwen3.6-35B-A3B", "family": "Qwen3.6 MoE"},
    {"repo": "Qwen/Qwen3.6-35B-A3B-FP8", "family": "Qwen3.6 MoE"},
    {"repo": "nvidia/Qwen3.6-35B-A3B-NVFP4", "family": "Qwen3.6 MoE"},
    {"repo": "Qwen/Qwen3.5-35B-A3B", "family": "Qwen3.5 MoE"},
    {"repo": "Qwen/Qwen3.5-35B-A3B-FP8", "family": "Qwen3.5 MoE"},
    {"repo": "Qwen/Qwen3.8-27B", "family": "Qwen3.8 dense"},
    {"repo": "Qwen/Qwen3.8-27B-FP8", "family": "Qwen3.8 dense"},
    {"repo": "RadixArk/Qwen3.8-27B-NVFP4", "family": "Qwen3.8 dense"},
    {"repo": "Qwen/Qwen3.6-27B", "family": "Qwen3.6 dense"},
    {"repo": "Qwen/Qwen3.6-27B-FP8", "family": "Qwen3.6 dense"},
    {"repo": "nvidia/Qwen3.6-27B-NVFP4", "family": "Qwen3.6 dense"},
    {"repo": "Qwen/Qwen3-30B-A3B", "family": "Qwen3 MoE"},
    {"repo": "openai/gpt-oss-120b", "family": "gpt-oss"},
    {"repo": "openai/gpt-oss-20b", "family": "gpt-oss"},
    {"repo": "google/gemma-4-26B-A4B-it", "family": "Gemma-4"},
    {"repo": "nvidia/Gemma-4-26B-A4B-NVFP4", "family": "Gemma-4"},
    {"repo": "google/gemma-4-12B-it", "family": "Gemma-4"},
    {"repo": "nvidia/Gemma-4-31B-IT-NVFP4", "family": "Gemma-4"},
    {"repo": "nvidia/MiniMax-M2.5-NVFP4", "family": "MiniMax-M2.5"},
    {"repo": "meta-models/Muse-Glimmer-30B", "family": "Muse-Glimmer"},
    {"repo": "RedHatAI/Muse-Glimmer-30B-NVFP4", "family": "Muse-Glimmer"},
]


def _safe_resolve(path: Path, roots: tuple[Path, ...]) -> Path:
    resolved = path.resolve(strict=False)
    if not any(resolved == root or resolved.is_relative_to(root) for root in roots):
        raise ValueError("Path is outside configured model directories")
    return resolved


def safe_model_path(config: Settings, model_id: str, *, must_exist: bool = True) -> Path:
    if not model_id or "\x00" in model_id:
        raise ValueError("Invalid model id")
    path = Path(os.path.abspath(config.models_dir / model_id))
    if not (path == config.models_dir or path.is_relative_to(config.models_dir)):
        raise ValueError("Path is outside configured model directories")
    if must_exist and not path.exists():
        raise FileNotFoundError("Model does not exist")
    if must_exist:
        _safe_resolve(path.resolve(strict=True), config.import_roots)
    return path


def _dir_size(path: Path) -> int:
    total = 0
    for root, dirs, files in os.walk(path, followlinks=False):
        dirs[:] = [d for d in dirs if d not in {".cache", ".git"}]
        for name in files:
            try:
                candidate = Path(root) / name
                if not candidate.is_symlink():
                    total += candidate.stat().st_size
            except OSError:
                pass
    return total


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open() as handle:
            value = json.load(handle)
            return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _complete(path: Path) -> tuple[bool, str | None]:
    config = _read_json(path / "config.json")
    if not config and not list(path.glob("*.gguf")):
        return False, "Missing config.json or GGUF file"
    indexes = list(path.glob("*.safetensors.index.json"))
    if indexes:
        index = _read_json(indexes[0])
        missing = [name for name in set((index.get("weight_map") or {}).values()) if not (path / name).is_file()]
        if missing:
            return False, f"Missing {len(missing)} weight shard(s)"
    elif not list(path.glob("*.safetensors")) and not list(path.glob("*.gguf")):
        return False, "No model weight files found"
    return True, None


def _compatibility(repo: str | None, architecture: str) -> str:
    if repo and any(x["repo"].lower() == repo.lower() for x in KNOWN_GOOD):
        return "verified"
    marker = architecture.lower()
    supported = ("deepseek", "qwen", "gptoss", "gpt_oss", "gemma4", "glm", "minimax", "muse")
    return "likely" if any(item in marker for item in supported) else "unknown"


class ModelLibrary:
    def __init__(self, config: Settings):
        self.config = config

    def scan(self, active_path: str | None = None) -> list[dict[str, Any]]:
        models: list[dict[str, Any]] = []
        if not self.config.models_dir.exists():
            return models
        for path in sorted(self.config.models_dir.iterdir(), key=lambda item: item.name.lower()):
            if path.name.startswith(".") or not path.is_dir():
                continue
            complete, issue = _complete(path)
            config = _read_json(path / "config.json")
            text = config.get("text_config") if isinstance(config.get("text_config"), dict) else config
            archs = text.get("architectures") or config.get("architectures") or []
            architecture = str(archs[0] if archs else text.get("model_type") or "Unknown")
            repo = None
            metadata = _read_json(path / ".freetoken-web.json")
            if metadata:
                repo = metadata.get("repo_id")
            model_id = path.relative_to(self.config.models_dir).as_posix()
            models.append({
                "id": model_id,
                "name": path.name,
                "path": str(path),
                "repository": repo,
                "architecture": architecture,
                "quantization": (config.get("quantization_config") or {}).get("quant_method") or _quant_from_name(path.name),
                "parameterCount": _parameter_count(text, path.name),
                "activeParameterCount": _active_parameter_count(text),
                "sizeBytes": _dir_size(path),
                "status": "running" if active_path and Path(active_path).resolve(strict=False) == path.resolve(strict=False) else ("downloaded" if complete else "failed"),
                "compatibility": _compatibility(repo, architecture),
                "issue": issue,
                "modifiedAt": path.stat().st_mtime,
            })
        return models

    def delete(self, model_id: str) -> int:
        lexical = self.config.models_dir / model_id
        if lexical.is_symlink():
            raise ValueError("Refusing to delete a linked model; remove the registration manually")
        path = safe_model_path(self.config, model_id)
        size = _dir_size(path)
        _safe_resolve(path, (self.config.models_dir,))
        shutil.rmtree(path)
        return size

    def register(self, source: str) -> dict[str, Any]:
        target = _safe_resolve(Path(source), self.config.import_roots)
        if not target.is_dir():
            raise ValueError("Import path is not a directory")
        complete, issue = _complete(target)
        if not complete:
            raise ValueError(issue or "Model is incomplete")
        link = safe_model_path(self.config, target.name, must_exist=False)
        if link.exists():
            raise ValueError("A model with this name is already registered")
        link.symlink_to(target, target_is_directory=True)
        return {"id": link.name, "path": str(target)}


def _quant_from_name(name: str) -> str | None:
    upper = name.upper()
    for marker in ("NVFP4", "MXFP4", "FP8", "BF16", "Q8", "Q6", "Q5", "Q4"):
        if marker in upper:
            return marker
    return None


def _parameter_count(config: dict, name: str) -> str | None:
    for key in ("num_parameters", "total_params"):
        if config.get(key):
            return str(config[key])
    import re
    match = re.search(r"(?:^|[-_])(\d+(?:\.\d+)?)B(?:[-_]|$)", name, re.I)
    return f"{match.group(1)}B" if match else None


def _active_parameter_count(config: dict) -> str | None:
    active = config.get("num_experts_per_tok")
    total = config.get("num_local_experts") or config.get("num_experts")
    if active and total:
        return f"{active}/{total} experts per token"
    return None


def catalog() -> list[dict[str, Any]]:
    return [{**item, "compatibility": "verified", "huggingFaceUrl": f"https://huggingface.co/{item['repo']}"} for item in KNOWN_GOOD]
