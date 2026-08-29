from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path
import struct
import wave

import pytest
import torch


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "intersuit"
    / "data_processing"
    / "valor32k_confidence.py"
)
SPEC = importlib.util.spec_from_file_location("valor32k_confidence_data", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def _write_wav(path: Path, frequency: float, *, sample_rate: int = 8000, duration: float = 0.1) -> None:
    samples = [
        int(12000 * math.sin(2 * math.pi * frequency * index / sample_rate))
        for index in range(round(sample_rate * duration))
    ]
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(b"".join(struct.pack("<h", sample) for sample in samples))


def _row(path: Path, condition: str, recipe: dict, *, first_stage: bool = True) -> dict:
    return {
        "record_id": f"record-{condition}",
        "pair_group_id": "pair-1",
        "condition": condition,
        "first_stage_training": first_stage,
        "audio_source_relative_media_path": path.name,
        "audio_source_sha256": module.sha256_file(path),
        "target_audio_sample_rate_hz": 8000,
        "target_audio_channels": 1,
        "target_audio_duration_sec": 0.1,
        "target_stream_window_count": 1,
        "corruption_recipe": recipe,
        "corruption_seed": 12345,
        "router_target": 1,
        "quality_rank": 4,
        "relevance_rank": 1,
        "sync_rank": None,
    }


def test_virtual_noise_is_deterministic_and_hits_requested_snr(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    _write_wav(source, 440.0)
    materializer = module.VirtualAudioMaterializer(tmp_path)
    clean_row = _row(source, "clean_correct", {"type": "identity"})
    noisy_row = _row(
        source,
        "noisy_white_10db",
        {"type": "additive_noise", "noise_type": "white", "snr_db": 10},
    )

    clean = materializer.materialize(clean_row)
    first = materializer.materialize(noisy_row)
    second = materializer.materialize(noisy_row)
    noise = first - clean
    measured_snr = 20 * torch.log10(clean.square().mean().sqrt() / noise.square().mean().sqrt())

    assert torch.equal(first, second)
    assert tuple(first.shape) == (1, 800)
    assert measured_snr.item() == pytest.approx(10.0, abs=0.05)


def test_silence_and_mismatch_share_target_shape(tmp_path: Path) -> None:
    correct = tmp_path / "correct.wav"
    mismatch = tmp_path / "mismatch.wav"
    _write_wav(correct, 220.0, duration=0.1)
    _write_wav(mismatch, 660.0, duration=0.05)
    materializer = module.VirtualAudioMaterializer(tmp_path)
    silence = materializer.materialize(
        _row(correct, "silence", {"type": "zeros_like_source"})
    )
    mismatch_row = _row(
        mismatch,
        "mismatch",
        {
            "type": "cross_video_audio",
            "normalization": {
                "sample_rate_hz": 8000,
                "channels": 1,
                "duration_sec": 0.1,
                "stream_window_count": 1,
                "length_policy": "trim_or_zero_pad_to_target",
            },
        },
    )
    mismatched = materializer.materialize(mismatch_row)

    assert torch.equal(silence, torch.zeros(1, 800))
    assert tuple(mismatched.shape) == (1, 800)
    assert torch.equal(mismatched[:, 400:], torch.zeros(1, 400))


def test_pink_and_dataset_background_are_finite(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    background = tmp_path / "background.wav"
    _write_wav(source, 220.0)
    _write_wav(background, 880.0)
    materializer = module.VirtualAudioMaterializer(tmp_path)
    pink = _row(
        source,
        "noisy_pink_0db",
        {"type": "additive_noise", "noise_type": "pink", "snr_db": 0},
    )
    dataset_noise = _row(
        source,
        "noisy_same_dataset_background_20db",
        {
            "type": "additive_noise",
            "noise_type": "same_dataset_background",
            "snr_db": 20,
            "background_relative_media_path": background.name,
            "background_sha256": module.sha256_file(background),
        },
    )

    assert torch.isfinite(materializer.materialize(pink)).all()
    assert torch.isfinite(materializer.materialize(dataset_noise)).all()


def test_second_stage_and_path_escape_fail_closed(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    _write_wav(source, 440.0)
    materializer = module.VirtualAudioMaterializer(tmp_path)
    shifted = _row(
        source,
        "shift_+1.0s",
        {"type": "circular_time_shift", "shift_sec": 1.0},
        first_stage=False,
    )
    with pytest.raises(ValueError, match="二阶段"):
        materializer.materialize(shifted)
    assert tuple(materializer.materialize(shifted, allow_second_stage=True).shape) == (1, 800)

    escaped = dict(_row(source, "clean_correct", {"type": "identity"}))
    escaped["audio_source_relative_media_path"] = "../source.wav"
    with pytest.raises(ValueError, match="越出根目录"):
        materializer.materialize(escaped)


def test_dataset_locks_manifest_sha_and_filters_shifted(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    _write_wav(source, 440.0)
    clean = _row(source, "clean_correct", {"type": "identity"})
    shifted = _row(
        source,
        "shift_-1.0s",
        {"type": "circular_time_shift", "shift_sec": -1.0},
        first_stage=False,
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps([clean, shifted]), encoding="utf-8")
    expected_sha = module.sha256_file(manifest)

    dataset = module.Valor32kConfidenceP2Dataset(
        manifest, tmp_path, expected_manifest_sha256=expected_sha
    )
    assert len(dataset) == 1
    assert dataset[0]["condition"] == "clean_correct"
    with pytest.raises(ValueError, match="manifest SHA"):
        module.Valor32kConfidenceP2Dataset(
            manifest, tmp_path, expected_manifest_sha256="0" * 64
        )
