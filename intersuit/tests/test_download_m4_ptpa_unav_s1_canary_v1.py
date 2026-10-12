"""UnAV S1 canary2 官方兼容下载与媒体合同测试。"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/download_m4_ptpa_unav_s1_canary_v1.py"
SPEC = importlib.util.spec_from_file_location("download_m4_ptpa_unav_s1_canary_v1", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def test_real_contract_closes_canary2_after_failed_download():
    contract = json.loads(
        (ROOT / "configs/m4_ptpa_unav_s1_reconstruction_v1.json").read_text()
    )
    plan = module.read_jsonl(
        ROOT / "datasets/UNAV_S1_V1/qualification_v1/unav_s1_media_canary20_plan_locked.jsonl"
    )
    probe = json.loads(
        (ROOT / "datasets/UNAV_S1_V1/media_probe_v1/canary2_compat_probe.json").read_text()
    )
    with pytest.raises(PermissionError, match="下载状态未授权"):
        module.validate_inputs(contract, plan, probe)


def test_scoped_contract_authorizes_only_canary2():
    contract = json.loads(
        (ROOT / "configs/m4_ptpa_unav_s1_reconstruction_v1.json").read_text()
    )
    contract = deepcopy(contract)
    contract["status"] = "canary2_media_download_authorized_effect_locked"
    contract["permissions"]["canary2_train_validation_media_download_allowed"] = True
    plan = module.read_jsonl(
        ROOT / "datasets/UNAV_S1_V1/qualification_v1/unav_s1_media_canary20_plan_locked.jsonl"
    )
    probe = json.loads(
        (ROOT / "datasets/UNAV_S1_V1/media_probe_v1/canary2_compat_probe.json").read_text()
    )
    selected = module.validate_inputs(contract, plan, probe)
    assert len(selected) == 2
    assert {row["official_split"] for row in selected} == {"train", "validation"}
    assert all(row["official_split"] != "test" for row in selected)


def test_download_one_uses_split_streams_then_official_ffmpeg_trim(monkeypatch, tmp_path):
    calls = []
    row = {
        "physical_media_id": "abcdefghijk",
        "official_split": "train",
        "source_url": "https://www.youtube.com/watch?v=abcdefghijk",
        "source_start_sec": 10.0,
        "source_end_sec": 20.0,
    }

    def fake_run(command, timeout):
        calls.append(command)
        if "--merge-output-format" in command:
            (tmp_path / "abcdefghijk_full_video.mp4").write_bytes(b"full")
        else:
            (tmp_path / "abcdefghijk.partial.mp4").write_bytes(b"trimmed")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(module, "run_command", fake_run)
    monkeypatch.setattr(
        module,
        "ffprobe",
        lambda path, executable: {
            "duration_sec": 10.0,
            "video_codec": "h264",
            "audio_codec": "aac",
            "audio_sample_rate": 44100,
            "audio_channels": 2,
        },
    )
    result = module.download_one(
        row,
        tmp_path,
        Path("/bin/true"),
        Path("/bin/true"),
        Path("/bin/true"),
        Path("/bin/true"),
        30,
    )
    assert result["status"] == "passed"
    assert module.SOURCE_FORMAT in calls[0]
    assert calls[1][1:5] == ["-ss", "10.0", "-i", str(tmp_path / "abcdefghijk_full_video.mp4")]
    assert "libx264" in calls[1]
    assert "aac" in calls[1]
    assert not any("cookie" in value.lower() for command in calls for value in command)
    assert not (tmp_path / "abcdefghijk_full_video.mp4").exists()
