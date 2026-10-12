from __future__ import annotations

import json

import pytest
import torch

from intersuit.train.audio_confidence_precompute import (
    FrozenEncoderBundle,
    audit_feature_manifest,
    build_feature_pack_for_row,
    precompute_feature_manifest,
)


def _row(record_id: str, *, modality: str = "audio", relevance_rank=4):
    return {
        "record_id": record_id,
        "pair_group_id": "pair-1",
        "physical_video_id": "video-1",
        "question_id": "question-1",
        "condition": "clean_correct",
        "modality": modality,
        "question": "What can be heard?",
        "router_target": 0 if modality == "visual" else 1,
        "quality_rank": 4,
        "relevance_rank": relevance_rank,
        "sync_rank": None,
        "first_stage_training": True,
        "target_audio_sample_rate_hz": 8,
        "target_stream_window_count": 3,
        "window_sec": 2.0,
        "hop_sec": 1.0,
    }


def _encoders(hidden=4):
    def audio(windows, timestamps):
        return windows.mean(dim=-1, keepdim=True).expand(-1, hidden)

    def video(row, timestamps):
        return torch.arange(timestamps.shape[0] * hidden).view(timestamps.shape[0], hidden)

    def question(text):
        return torch.arange(hidden)

    return FrozenEncoderBundle(
        audio_encoder=audio,
        video_encoder=video,
        question_encoder=question,
        hidden_size=hidden,
        audio_sample_rate=8,
        source_fingerprints={"audio": "a" * 64, "video": "b" * 64, "question": "c" * 64},
    )


def test_build_pack_uses_full_windows_and_independent_supervision_masks():
    waveform = torch.linspace(-1, 1, 32)  # 4 秒，2 秒窗/1 秒 hop => 3 个完整窗口。
    pack, metadata = build_feature_pack_for_row(
        _row("audio-1"), waveform, _encoders()
    )
    assert pack.audio.shape == (1, 3, 4)
    assert pack.quality_mask.all()
    assert pack.relevance_mask.all()
    assert metadata["timestamps"] == [[0.0, 2.0], [1.0, 3.0], [2.0, 4.0]]

    visual, _ = build_feature_pack_for_row(
        _row("visual-1", modality="visual", relevance_rank=None), waveform, _encoders()
    )
    assert visual.quality_mask.all()
    assert not visual.relevance_mask.any()


def test_precompute_manifest_and_independent_audit(tmp_path):
    rows = [_row("audio-1"), _row("visual-1", modality="visual", relevance_rank=None)]
    manifest_path = precompute_feature_manifest(
        rows,
        lambda row: torch.linspace(-1, 1, 32),
        _encoders(),
        tmp_path,
        p2_manifest_sha256="d" * 64,
    )
    result = audit_feature_manifest(manifest_path)
    assert result["status"] == "PASS_P3_FROZEN_FEATURE_AUDIT"
    assert result["record_count"] == 2
    assert result["quality_supervised_windows"] == 6
    assert result["relevance_supervised_windows"] == 3

    manifest = json.loads(manifest_path.read_text())
    pack_path = tmp_path / manifest["records"][0]["feature_path"]
    pack_path.write_bytes(pack_path.read_bytes() + b"drift")
    with pytest.raises(ValueError, match="SHA"):
        audit_feature_manifest(manifest_path)


def test_precompute_rejects_shifted_and_bad_encoder_fingerprint():
    row = _row("shifted")
    row["condition"] = "shift_plus_1s"
    row["first_stage_training"] = False
    with pytest.raises(ValueError, match="shifted"):
        build_feature_pack_for_row(row, torch.ones(32), _encoders())

    bad = _encoders()
    object.__setattr__(bad, "source_fingerprints", {"audio": "bad", "video": "b" * 64, "question": "c" * 64})
    with pytest.raises(ValueError, match="SHA-256"):
        build_feature_pack_for_row(_row("bad"), torch.ones(32), bad)
