from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from ..config import Settings


# Exact architectures in the published upstream freetoken==0.1.2 wheel.
# Main has additional architectures not yet in that wheel; do not advertise them.
SUPPORTED_ARCHITECTURES = {
    "DeepseekV4ForCausalLM",
    "Gemma4ForCausalLM",
    "Gemma4ForConditionalGeneration",
    "Gemma4GGUFForCausalLM",
    "Gemma4UnifiedForCausalLM",
    "Gemma4UnifiedForConditionalGeneration",
    "Glm4MoeForCausalLM",
    "GlmMoeDsaForCausalLM",
    "GptOssForCausalLM",
    "LlamaForCausalLM",
    "MiniMaxM2ForCausalLM",
    "MiniMaxM3SparseForCausalLM",
    "MiniMaxM3SparseForConditionalGeneration",
    "Mistral3ForConditionalGeneration",
    "MistralForCausalLM",
    "MuseGlimmerForConditionalGeneration",
    "Qwen2ForCausalLM",
    "Qwen3ForCausalLM",
    "Qwen3MoeForCausalLM",
    "Qwen3_5ForConditionalGeneration",
    "Qwen3_5MoeForConditionalGeneration",
}
SUPPORTED_PIPELINE_TAGS = {"text-generation", "image-text-to-text"}

# Repositories confirmed to work with a specific FreeToken release.
KNOWN_GOOD = [
    {"repo": "deepseek-ai/DeepSeek-V4-Flash-0731", "family": "DeepSeek-V4", "notes": "Requires the inference/config.json subdirectory."},
    {"repo": "nvidia/GLM-5.2-NVFP4", "family": "GLM-5.2"},
    {"repo": "nvidia/GLM-4.7-NVFP4", "family": "GLM-4.7"},
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
    {"repo": "nvidia/Gemma-4-31B-IT-NVFP4", "family": "Gemma-4"},
    {"repo": "nvidia/MiniMax-M2.5-NVFP4", "family": "MiniMax-M2.5"},
    {"repo": "meta-models/Muse-Glimmer-30B", "family": "Muse-Glimmer"},
    {"repo": "RedHatAI/Muse-Glimmer-30B-NVFP4", "family": "Muse-Glimmer"},
]

_VERIFIED_REPOS = {item["repo"].lower() for item in KNOWN_GOOD}


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
    if path == config.models_dir:
        raise ValueError("Model id must name a checkpoint, not the model storage root")
    _safe_resolve(path, config.import_roots if must_exist else (config.models_dir,))
    if not must_exist and path.is_symlink():
        raise ValueError("Refusing to download through a linked model directory")
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
    config = _read_json(path / "config.json") or _read_json(path / "inference" / "config.json")
    if not config:
        return False, "Missing config.json"
    indexes = list(path.glob("*.safetensors.index.json"))
    if indexes:
        index = _read_json(indexes[0])
        weights = index.get("weight_map")
        if not isinstance(weights, dict) or not weights:
            return False, "Invalid or empty safetensors index"
        for name in weights.values():
            if not isinstance(name, str) or not (path / name).resolve().is_relative_to(path.resolve()):
                return False, "Unsafe weight shard path in index"
        missing = [name for name in set(weights.values()) if not (path / name).is_file() or (path / name).stat().st_size == 0]
        if missing:
            return False, f"Missing {len(missing)} weight shard(s)"
    elif not list(path.glob("*.safetensors")) and not list(path.glob("*.ftw")):
        return False, "No model weight files found"
    if any(f.stat().st_size == 0 for f in path.glob("*.safetensors")):
        return False, "Empty model weight file"
    return True, None


def _compatibility(repo: str | None, architecture: str) -> str:
    if repo and repo.lower() in _VERIFIED_REPOS:
        return "verified"
    return "likely" if architecture in SUPPORTED_ARCHITECTURES else "unknown"


def classify_search_result(repo_id: str, pipeline_tag: str | None, tags: list[str],
                           siblings: list[Any] | None, config: dict | None = None) -> dict[str, Any]:
    has_weights = any(getattr(s, "rfilename", s if isinstance(s, str) else "").endswith(".safetensors") for s in (siblings or [])) or "safetensors" in tags
    architecture = ((config or {}).get("architectures") or [None])[0]
    if repo_id.lower() in _VERIFIED_REPOS:
        has_weights = True
        level, reason = "verified", None
    elif pipeline_tag and pipeline_tag not in SUPPORTED_PIPELINE_TAGS:
        level, reason = "unsupported", f"Pipeline {pipeline_tag} is not supported for text generation."
    elif not has_weights:
        level, reason = "unsupported", "No safetensors weights found. This downloader supports HF safetensors checkpoints."
    elif architecture in SUPPORTED_ARCHITECTURES:
        level, reason = "likely", "Architecture is registered upstream; this exact checkpoint and quantization are not verified."
    else:
        level, reason = "unknown", "Not in the upstream verified catalog. Architecture and quantization compatibility are unverified."
    return {"compatibility": level, "hasSafetensors": has_weights, "architecture": architecture, "unsupportedReason": reason}


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
            try:
                _safe_resolve(path, self.config.import_roots)
            except ValueError:
                continue
            complete, issue = _complete(path)
            if (path / ".download.json").exists():
                complete, issue = False, "Download incomplete; resume from download history"
            config = _read_json(path / "config.json") or _read_json(path / "inference" / "config.json")
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
