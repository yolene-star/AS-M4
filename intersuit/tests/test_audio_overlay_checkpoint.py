from __future__ import annotations

import json
from pathlib import Path

import torch
from safetensors import safe_open
from safetensors.torch import save_file

from intersuit.harness.audio_overlay_checkpoint import build_overlay_checkpoint


def _write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_build_overlay_keeps_base_weights_and_adds_only_audio(tmp_path):
    base = tmp_path / "base"
    source = tmp_path / "source"
    output = tmp_path / "output"
    base.mkdir()
    source.mkdir()

    base_weights = {
        "model.embed_tokens.weight": torch.arange(12, dtype=torch.float32).reshape(3, 4),
        "lm_head.weight": torch.ones(3, 4),
    }
    save_file(base_weights, str(base / "model-00001-of-00001.safetensors"))
    _write_json(
        base / "model.safetensors.index.json",
        {
            "metadata": {"total_size": sum(t.numel() * t.element_size() for t in base_weights.values())},
            "weight_map": {key: "model-00001-of-00001.safetensors" for key in base_weights},
        },
    )
    _write_json(base / "config.json", {"hidden_size": 4, "model_type": "llava_qwen"})
    (base / "tokenizer.json").write_text("{}", encoding="utf-8")

    source_weights = {
        "model.embed_tokens.weight": torch.full((3, 4), 999.0),
        "model.scene_audio_encoder.audio_projector.weight": torch.eye(4),
        "model.streaming_av_module.fusion.audio_projector.weight": torch.eye(4),
    }
    save_file(source_weights, str(source / "model-00001-of-00001.safetensors"))
    _write_json(
        source / "model.safetensors.index.json",
        {
            "metadata": {"total_size": sum(t.numel() * t.element_size() for t in source_weights.values())},
            "weight_map": {key: "model-00001-of-00001.safetensors" for key in source_weights},
        },
    )
    _write_json(
        source / "config.json",
        {
            "hidden_size": 999,
            "enable_scene_audio": True,
            "scene_audio_encoder_type": "beats",
            "scene_audio_window_mode": "fixed",
            "as_m4_fusion_mode": "beats_simple_residual",
        },
    )

    runtime_lock = tmp_path / "runtime.json"
    evaluation_lock = tmp_path / "evaluation.json"
    _write_json(
        runtime_lock,
        {
            "runtime_contract": {
                "scene_audio_window_mode": "micro_window",
                "dynamic_window_top_k": 8,
                "as_m4_inference_simple_audio_gate": 0.005,
            }
        },
    )
    _write_json(evaluation_lock, {"pt": {"temperature": 1.0, "beta": 0.2}})

    manifest = build_overlay_checkpoint(
        base_model_dir=base,
        audio_checkpoint_dir=source,
        output_dir=output,
        runtime_lock_path=runtime_lock,
        evaluation_lock_path=evaluation_lock,
        model_version="test-overlay-v1",
    )

    index = json.loads((output / "model.safetensors.index.json").read_text())
    config = json.loads((output / "config.json").read_text())
    assert (output / "model-00001-of-00001.safetensors").is_symlink()
    assert (output / "evaluation_config.json").is_symlink()
    assert index["weight_map"]["model.embed_tokens.weight"] == "model-00001-of-00001.safetensors"
    assert index["weight_map"]["model.scene_audio_encoder.audio_projector.weight"] == "audio-branch.safetensors"
    assert config["hidden_size"] == 4
    assert config["scene_audio_encoder_type"] == "beats"
    assert config["scene_audio_window_mode"] == "micro_window"
    assert config["dynamic_window_top_k"] == 8
    assert config["enable_scene_audio"] is True
    assert config["_name_or_path"] == "test-overlay-v1"
    assert manifest["audio_checkpoint"]["selected_weight_count"] == 2
    assert manifest["audio_checkpoint"]["current_code_initialized_weight_count"] > 0

    with safe_open(output / "audio-branch.safetensors", framework="pt", device="cpu") as handle:
        assert handle.metadata() == {"format": "pt"}
        keys = set(handle.keys())
        assert "model.scene_audio_encoder.audio_projector.weight" in keys
        assert "model.streaming_av_module.fusion.audio_projector.weight" in keys
        assert (
            "model.streaming_av_module.question_conditioned_window_selector."
            "audio_projection.weight"
        ) in keys
        assert "model.embed_tokens.weight" not in keys
        assert torch.equal(
            handle.get_tensor("model.scene_audio_encoder.audio_projector.weight"),
            torch.eye(4),
        )
