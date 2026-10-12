"""LLP S1 冻结 BEATs 特征准备的 CPU 合同测试。"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest
import torch
from scipy.io import wavfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/extract_m4_ptpa_llp_s1_beats_features.py"
SPEC = importlib.util.spec_from_file_location("extract_m4_ptpa_llp_s1_beats_features", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


class FakeEncoder:
    encoder_name = "test_stats"
    checkpoint_sha256 = "test"

    def encode_windows(self, windows: torch.Tensor) -> torch.Tensor:
        return torch.stack(
            [windows.mean(dim=1), windows.square().mean(dim=1).sqrt()], dim=1
        )


def write_lock(path: Path, allowed: bool = True) -> None:
    payload = {
        "datasets": {
            "llp": {
                "pre_download_qualification": {
                    "feature_extraction_allowed": allowed,
                    "official_protocol_downloader": {
                        "license_review": {
                            "feature_extraction_allowed": allowed,
                            "training_allowed": False,
                            "redistribution_allowed": False,
                            "effect_access_allowed": False,
                            "user_scope_authorization": {
                                "scope": "local_server_internal_noncommercial_research",
                                "gpu_feature_extraction_allowed": True,
                            },
                        }
                    }
                },
            }
        },
        "permissions": {
            "llp_s1_internal_noncommercial_feature_preparation_allowed": allowed,
            "llp_s1_single_gpu_feature_extraction_allowed": True,
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_validate_scope_is_fail_closed(tmp_path):
    lock = tmp_path / "lock.json"
    write_lock(lock, allowed=False)
    with pytest.raises(PermissionError, match="未授权"):
        module.validate_scope(lock)


def test_gpu_environment_requires_physical_gpu2(monkeypatch):
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "M4")
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")
    with pytest.raises(RuntimeError, match="物理 GPU 2"):
        module.validate_environment("cuda:0")


def test_cpu_fake_pipeline_writes_wav_windows_and_feature_contract(monkeypatch, tmp_path):
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "M4")
    media = tmp_path / "video.mp4"
    media.write_bytes(b"qualified media")
    manifest = tmp_path / "manifest.jsonl"
    row = {
        "sample_id": "llp:train:abcdefghijk_0_10",
        "physical_media_id": "abcdefghijk",
        "official_split": "train",
        "event_labels": ["Speech"],
        "start_sec": 0.0,
        "end_sec": 10.0,
        "media_path": str(media),
        "media_sha256": module.sha256(media),
        "media_qualified": True,
        "effect_access_allowed": False,
    }
    manifest.write_text(json.dumps(row) + "\n", encoding="utf-8")
    checkpoint = tmp_path / "beats.pt"
    checkpoint.write_bytes(b"checkpoint")
    beats_source = tmp_path / "beats/BEATs.py"
    beats_source.parent.mkdir()
    beats_source.write_text("# test source\n", encoding="utf-8")
    lock = tmp_path / "lock.json"
    write_lock(lock)
    monkeypatch.setattr(module, "EXPECTED_MANIFEST_SHA256", module.sha256(manifest))
    monkeypatch.setattr(module, "EXPECTED_BEATS_SHA256", module.sha256(checkpoint))

    def fake_decode(_media, wav_path, _ffmpeg):
        wav_path.parent.mkdir(parents=True, exist_ok=True)
        # 容器多出的 4000 个采样必须按官方 10 秒边界裁掉。
        wavfile.write(wav_path, module.SAMPLE_RATE, np.arange(164_000, dtype=np.int16))

    monkeypatch.setattr(module, "decode_wav", fake_decode)
    output = tmp_path / "output"
    args = argparse.Namespace(
        manifest=manifest,
        responsibility_lock=lock,
        output=output,
        beats_checkpoint=checkpoint,
        beats_code_root=tmp_path,
        ffmpeg="ffmpeg",
        device="cpu",
        limit=None,
        resume=False,
    )
    result = module.run(args, encoder=FakeEncoder())

    assert result["summary"]["status"] == "PASS_FEATURE_PREPARATION_ONLY"
    assert result["summary"]["rows"] == 1
    assert result["summary"]["total_windows"] == 10
    feature = torch.load(
        output / "features/abcdefghijk.pt", map_location="cpu", weights_only=True
    )
    assert feature["audio_embedding"].shape == (10, 2)
    assert feature["valid_sample_count"].tolist() == [16_000] * 10
    assert feature["metadata"]["frozen_encoder"] is True
    assert feature["metadata"]["training_performed"] is False
    rate, audio = wavfile.read(output / "audio_16k_mono/abcdefghijk.wav")
    assert rate == 16_000
    assert audio.ndim == 1
    assert (output / "REVIEW_ZH.md").is_file()


def test_match_official_duration_pads_short_decode():
    waveform = torch.ones(15_000)
    matched = module.match_official_duration(waveform, 1.0)
    assert matched.shape == (16_000,)
    assert matched[:15_000].sum().item() == 15_000
    assert matched[15_000:].sum().item() == 0
