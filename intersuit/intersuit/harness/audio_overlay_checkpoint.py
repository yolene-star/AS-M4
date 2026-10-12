"""构建“官方 M4 基座 + 场景音频权重 overlay”的组合 checkpoint。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable

import torch
from safetensors import safe_open
from safetensors.torch import save_file

from intersuit.model.streaming_av.builder import build_streaming_av_module


AUDIO_WEIGHT_PREFIXES = (
    "model.scene_audio_encoder.",
    "model.streaming_av_module.",
)

AUDIO_CONFIG_PREFIXES = (
    "as_m4_",
    "audio_",
    "dynamic_window_",
    "question_conditioned_",
    "scene_audio_",
    "streaming_av_",
)

AUDIO_CONFIG_EXACT_KEYS = {
    "debug_audio_residual_scale",
    "enable_scene_audio",
    "enable_temporal_offset_gru_diagnostic",
    "force_audio_gate",
    "max_av_offset_sec",
    "num_audio_events",
}

RUNTIME_MODEL_KEYS = {
    "as_m4_fusion_mode",
    "as_m4_inference_simple_audio_gate",
    "audio_delta_ratio_cap",
    "debug_audio_residual_scale",
    "dynamic_window_top_k",
    "scene_audio_window_mode",
}

ANCILLARY_FILES = (
    "added_tokens.json",
    "generation_config.json",
    "merges.txt",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.json",
)

CURRENT_CODE_INIT_SEED = 20260810


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"JSON 必须是 object：{path}")
    return payload


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def select_audio_weight_map(weight_map: dict[str, str]) -> dict[str, str]:
    return {
        key: shard
        for key, shard in weight_map.items()
        if key.startswith(AUDIO_WEIGHT_PREFIXES)
    }


def select_audio_config(config: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in config.items()
        if key in AUDIO_CONFIG_EXACT_KEYS or key.startswith(AUDIO_CONFIG_PREFIXES)
    }


def extract_audio_tensors(
    checkpoint_dir: Path,
    audio_weight_map: dict[str, str],
) -> tuple[dict[str, Any], int]:
    tensors: dict[str, Any] = {}
    total_size = 0
    shard_to_keys: dict[str, list[str]] = {}
    for key, shard in audio_weight_map.items():
        shard_to_keys.setdefault(shard, []).append(key)

    for shard, keys in sorted(shard_to_keys.items()):
        shard_path = checkpoint_dir / shard
        if not shard_path.is_file():
            raise FileNotFoundError(f"音频 checkpoint shard 不存在：{shard_path}")
        with safe_open(shard_path, framework="pt", device="cpu") as handle:
            available = set(handle.keys())
            for key in sorted(keys):
                if key not in available:
                    raise KeyError(f"索引中的音频权重不在 shard 中：{key} -> {shard}")
                tensor = handle.get_tensor(key)
                tensors[key] = tensor
                total_size += tensor.numel() * tensor.element_size()
    return tensors, total_size


def complete_current_streaming_state(
    audio_tensors: dict[str, torch.Tensor],
    merged_config: dict[str, Any],
) -> list[str]:
    projector_key = "model.scene_audio_encoder.audio_projector.weight"
    if merged_config.get("scene_audio_selector_dim") is None:
        projector = audio_tensors.get(projector_key)
        selector_dim = (
            int(projector.shape[1])
            if projector is not None and projector.ndim == 2
            else int(merged_config["hidden_size"])
        )
        merged_config["scene_audio_selector_dim"] = selector_dim

    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(CURRENT_CODE_INIT_SEED)
        current_module = build_streaming_av_module(SimpleNamespace(**merged_config))
    current_state = current_module.state_dict()
    initialized_keys: list[str] = []
    for local_key, current_tensor in current_state.items():
        full_key = f"model.streaming_av_module.{local_key}"
        source_tensor = audio_tensors.get(full_key)
        if source_tensor is not None:
            if source_tensor.shape != current_tensor.shape:
                raise ValueError(
                    f"音频 checkpoint 与当前代码 shape 不兼容：{full_key} "
                    f"source={tuple(source_tensor.shape)} current={tuple(current_tensor.shape)}"
                )
            continue
        audio_tensors[full_key] = current_tensor.detach().cpu().contiguous()
        initialized_keys.append(full_key)
    return sorted(initialized_keys)


def relative_symlink(source: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"拒绝覆盖已有文件：{destination}")
    destination.symlink_to(os.path.relpath(source, start=destination.parent))


def validate_required_audio_keys(keys: Iterable[str]) -> None:
    key_set = set(keys)
    required_prefixes = (
        "model.scene_audio_encoder.audio_projector.",
        "model.streaming_av_module.fusion.audio_projector.",
    )
    missing = [
        prefix for prefix in required_prefixes if not any(key.startswith(prefix) for key in key_set)
    ]
    if missing:
        raise ValueError(f"音频 checkpoint 缺少必要权重前缀：{missing}")


def build_overlay_checkpoint(
    *,
    base_model_dir: Path,
    audio_checkpoint_dir: Path,
    output_dir: Path,
    runtime_lock_path: Path,
    evaluation_lock_path: Path,
    model_version: str,
) -> dict[str, Any]:
    base_model_dir = base_model_dir.resolve()
    audio_checkpoint_dir = audio_checkpoint_dir.resolve()
    output_dir = output_dir.resolve()
    runtime_lock_path = runtime_lock_path.resolve()
    evaluation_lock_path = evaluation_lock_path.resolve()

    if output_dir.exists():
        raise FileExistsError(f"拒绝覆盖已有模型版本：{output_dir}")

    base_index_path = base_model_dir / "model.safetensors.index.json"
    audio_index_path = audio_checkpoint_dir / "model.safetensors.index.json"
    base_config_path = base_model_dir / "config.json"
    audio_config_path = audio_checkpoint_dir / "config.json"
    for path in (
        base_index_path,
        audio_index_path,
        base_config_path,
        audio_config_path,
        runtime_lock_path,
        evaluation_lock_path,
    ):
        if not path.is_file():
            raise FileNotFoundError(path)

    base_index = load_json(base_index_path)
    audio_index = load_json(audio_index_path)
    base_weight_map = dict(base_index.get("weight_map") or {})
    source_weight_map = dict(audio_index.get("weight_map") or {})
    if not base_weight_map or not source_weight_map:
        raise ValueError("checkpoint index 缺少非空 weight_map")

    audio_weight_map = select_audio_weight_map(source_weight_map)
    if not audio_weight_map:
        raise ValueError("音频 checkpoint 中未找到场景音频权重")
    validate_required_audio_keys(audio_weight_map)
    collisions = sorted(set(base_weight_map).intersection(audio_weight_map))
    if collisions:
        raise ValueError(f"官方 M4 基座已包含同名音频权重，拒绝静默覆盖：{collisions[:5]}")

    base_config = load_json(base_config_path)
    audio_config = load_json(audio_config_path)
    runtime_lock = load_json(runtime_lock_path)
    runtime_contract = dict(runtime_lock.get("runtime_contract") or {})
    merged_config = dict(base_config)
    merged_config.update(select_audio_config(audio_config))
    merged_config.update(
        {
            key: value
            for key, value in runtime_contract.items()
            if key in RUNTIME_MODEL_KEYS
        }
    )
    merged_config["enable_scene_audio"] = True
    merged_config["_name_or_path"] = model_version
    merged_config["model_version"] = model_version
    merged_config["audio_overlay_source"] = str(audio_checkpoint_dir)
    merged_config["audio_overlay_qualification"] = "diagnostic_only"

    audio_tensors, _ = extract_audio_tensors(audio_checkpoint_dir, audio_weight_map)
    initialized_current_code_keys = complete_current_streaming_state(
        audio_tensors,
        merged_config,
    )
    audio_total_size = sum(
        tensor.numel() * tensor.element_size() for tensor in audio_tensors.values()
    )

    output_dir.mkdir(parents=True)
    try:
        base_shards = sorted(set(base_weight_map.values()))
        for shard in base_shards:
            source = base_model_dir / shard
            if not source.is_file():
                raise FileNotFoundError(f"官方 M4 shard 不存在：{source}")
            relative_symlink(source, output_dir / shard)

        for filename in ANCILLARY_FILES:
            source = base_model_dir / filename
            if source.is_file():
                relative_symlink(source, output_dir / filename)

        overlay_filename = "audio-branch.safetensors"
        # Transformers 把该文件作为 checkpoint shard 加载时要求存在
        # ``metadata["format"]``。只写固定、路径无关的单项 metadata，
        # 既满足加载协议，也保持跨输出目录的字节级可复现性。
        save_file(
            audio_tensors,
            str(output_dir / overlay_filename),
            metadata={"format": "pt"},
        )

        combined_weight_map = dict(base_weight_map)
        combined_weight_map.update({key: overlay_filename for key in audio_tensors})
        base_total_size = int((base_index.get("metadata") or {}).get("total_size", 0))
        combined_index = {
            "metadata": {
                "total_size": base_total_size + audio_total_size,
                "base_total_size": base_total_size,
                "audio_overlay_total_size": audio_total_size,
                "model_version": model_version,
            },
            "weight_map": combined_weight_map,
        }
        write_json(output_dir / "model.safetensors.index.json", combined_index)

        write_json(output_dir / "config.json", merged_config)

        relative_symlink(evaluation_lock_path, output_dir / "evaluation_config.json")

        overlay_path = output_dir / overlay_filename
        manifest = {
            "format_version": 1,
            "model_version": model_version,
            "qualification": "diagnostic_only",
            "base_model": {
                "path": str(base_model_dir),
                "config_sha256": sha256_file(base_config_path),
                "index_sha256": sha256_file(base_index_path),
                "weight_count": len(base_weight_map),
            },
            "audio_checkpoint": {
                "path": str(audio_checkpoint_dir),
                "config_sha256": sha256_file(audio_config_path),
                "index_sha256": sha256_file(audio_index_path),
                "selected_weight_count": len(audio_weight_map),
                "selected_weight_prefixes": list(AUDIO_WEIGHT_PREFIXES),
                "current_code_initialized_weight_count": len(
                    initialized_current_code_keys
                ),
                "current_code_initialized_weight_keys": initialized_current_code_keys,
                "current_code_init_seed": CURRENT_CODE_INIT_SEED,
            },
            "runtime_lock": {
                "path": str(runtime_lock_path),
                "sha256": sha256_file(runtime_lock_path),
            },
            "evaluation_lock": {
                "path": str(evaluation_lock_path),
                "sha256": sha256_file(evaluation_lock_path),
            },
            "output": {
                "path": str(output_dir),
                "audio_overlay_file": overlay_filename,
                "audio_overlay_sha256": sha256_file(overlay_path),
                "audio_overlay_total_size": audio_total_size,
                "audio_overlay_weight_count": len(audio_tensors),
                "combined_weight_count": len(combined_weight_map),
                "base_shards_are_relative_symlinks": True,
            },
            "known_limitations_zh": [
                "最新音频 checkpoint 未通过 dev760 正确音频选择性门禁。",
                "selector v2 oracle pilot 已触发 stop gate，禁止宣称正式音频增强。",
                "PT/PA 最优参数属于评测配置，不改变模型权重。",
            ],
        }
        write_json(output_dir / "model_version_manifest.json", manifest)

        (output_dir / "README.md").write_text(
            "# M4 PT/PA Best + Audio Branch v1\n\n"
            "该目录以官方公开 M4 为基座，通过相对软链接复用四个基座 shard，"
            "并只叠加最新诊断 checkpoint 的场景音频模块权重。\n\n"
            "- 关闭 `enable_scene_audio` 时应回到官方 M4 路径；\n"
            "- PT/PA 最优参数见 `evaluation_config.json`；\n"
            "- 当前音频权重资格为 `diagnostic_only`，不得宣称正式优于原模型；\n"
            "- 外部冻结 BEATs 权重仍从项目内独立 checkpoint 加载。\n",
            encoding="utf-8",
        )
    except Exception:
        # 输出目录此前不存在，失败时只清理由本函数创建的轻量文件/软链接。
        for child in output_dir.iterdir():
            child.unlink()
        output_dir.rmdir()
        raise

    return manifest
