"""AVE D1 validation cached Eventness 特征与 All=Fixed 审计测试。"""

from __future__ import annotations

import importlib.util
import json
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


def test_resume_only_requires_original_extraction_summary(tmp_path):
    manifest_text = '{"youtube_id":"sample"}\n'
    (tmp_path / "validation_eventness_feature_manifest.jsonl").write_text(
        manifest_text,
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="原始 extraction summary"):
        module.validate_resume_only_source(
            tmp_path,
            manifest_text=manifest_text,
        )


def test_resume_only_rejects_summary_that_itself_extracted_nothing(tmp_path):
    manifest_text = '{"youtube_id":"sample"}\n'
    (tmp_path / "validation_eventness_feature_manifest.jsonl").write_text(
        manifest_text,
        encoding="utf-8",
    )
    (tmp_path / "summary.json").write_text(
        json.dumps(
            {
                "execution_mode": "resume_only",
                "newly_extracted_count": 0,
                "outputs": {
                    "manifest": {
                        "sha256": module.sha256_text(manifest_text),
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="不能证明特征生成环境"):
        module.validate_resume_only_source(
            tmp_path,
            manifest_text=manifest_text,
        )


def test_resume_only_preserves_original_summary_and_writes_separate_audit(tmp_path):
    manifest_text = '{"youtube_id":"sample"}\n'
    manifest_path = tmp_path / "validation_eventness_feature_manifest.jsonl"
    manifest_path.write_text(manifest_text, encoding="utf-8")
    original_summary = {
        "execution_mode": "extraction",
        "newly_extracted_count": 1,
        "resumed_count": 0,
        "outputs": {
            "manifest": {
                "sha256": module.sha256_text(manifest_text),
            }
        },
    }
    summary_path = tmp_path / "summary.json"
    original_bytes = (json.dumps(original_summary) + "\n").encode()
    summary_path.write_bytes(original_bytes)

    source = module.validate_resume_only_source(
        tmp_path,
        manifest_text=manifest_text,
    )
    module.write_resume_validation_summary(
        tmp_path,
        source_summary=source,
        current_process={
            "requested_device": "cuda:0",
            "visible_cuda_device_count": 0,
        },
        sample_count=1,
    )

    assert summary_path.read_bytes() == original_bytes
    audit = json.loads(
        (tmp_path / "resume_validation_summary.json").read_text(encoding="utf-8")
    )
    assert audit["execution_mode"] == "resume_only"
    assert audit["asset_generation_environment_source"] == "preserved_summary"
    assert audit["current_process"]["visible_cuda_device_count"] == 0


@pytest.mark.parametrize(
    "environment",
    [
        {
            "requested_device": "cuda:0",
            "cuda_visible_devices": None,
            "visible_cuda_device_count": 1,
        },
        {
            "requested_device": "cuda:0",
            "cuda_visible_devices": "0,2",
            "visible_cuda_device_count": 2,
        },
    ],
)
def test_fresh_cuda_extraction_requires_explicit_single_gpu(environment):
    with pytest.raises(ValueError, match="CUDA_VISIBLE_DEVICES|只可见一张 GPU"):
        module.assert_fresh_extraction_environment(environment)


def test_fresh_cuda_extraction_accepts_explicit_single_gpu():
    module.assert_fresh_extraction_environment(
        {
            "requested_device": "cuda:0",
            "cuda_visible_devices": "1",
            "visible_cuda_device_count": 1,
        }
    )
