"""AVE D1 validation cached Eventness 特征与 All=Fixed 审计测试。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest
import torch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "extract_ave_official_d1_validation_eventness.py"
SPEC = importlib.util.spec_from_file_location(
    "extract_ave_official_d1_validation_eventness", MODULE_PATH
)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


class FakeEncoder:
    encoder_name = "fake"

    def encode_windows(self, windows):
        index = torch.arange(windows.shape[0], dtype=torch.float32)
        return torch.stack([index, index.square(), index + 1, index * 0], dim=-1)


def test_normalize_waveform_freezes_ten_windows_with_padding_and_truncation():
    short, short_audit = module.normalize_waveform(torch.zeros(159_500))
    long, long_audit = module.normalize_waveform(torch.zeros(160_125))

    assert short.shape == long.shape == (10, 16_000)
    assert short_audit["padded_sample_count"] == 500
    assert long_audit["truncated_sample_count"] == 125


def test_extract_one_proves_all_fixed_and_saves_eventness(tmp_path, monkeypatch):
    media = tmp_path / "sample.mp4"
    media.write_bytes(b"media")
    media_sha = module.file_sha256(media)
    waveform = torch.zeros(160_000)
    waveform[32_000:48_000] = 1.0
    monkeypatch.setattr(
        module.baseline,
        "decode_audio_mono_16k",
        lambda path: waveform,
    )
    row = {
        "youtube_id": "sample",
        "media_path": str(media),
        "media_sha256": media_sha,
    }

    record = module.extract_one(
        row,
        encoder=FakeEncoder(),
        checkpoint_sha256="a" * 64,
        output_dir=tmp_path / "output",
    )

    assert record.all_fixed_exact is True
    assert record.audit_status == "pass"
    payload = torch.load(record.feature_path, map_location="cpu", weights_only=True)
    assert payload["audio_embedding"].shape == (10, 4)
    assert payload["eventness_cached_scores"].shape == (10,)
    assert payload["metadata"]["causal_ranking"] is False
    assert payload["metadata"]["test_accessed"] is False


def test_saved_feature_rejects_media_sha_change(tmp_path, monkeypatch):
    media = tmp_path / "sample.mp4"
    media.write_bytes(b"media")
    waveform = torch.zeros(160_000)
    monkeypatch.setattr(
        module.baseline,
        "decode_audio_mono_16k",
        lambda path: waveform,
    )
    record = module.extract_one(
        {
            "youtube_id": "sample",
            "media_path": str(media),
            "media_sha256": module.file_sha256(media),
        },
        encoder=FakeEncoder(),
        checkpoint_sha256="b" * 64,
        output_dir=tmp_path / "output",
    )

    with pytest.raises(ValueError, match="身份或 SHA"):
        module.validate_saved_feature(
            Path(record.feature_path),
            youtube_id="sample",
            media_sha256="0" * 64,
            checkpoint_sha256="b" * 64,
        )
