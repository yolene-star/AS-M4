from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/download_m4_ptpa_llp_pilot_v2.py"
SPEC = importlib.util.spec_from_file_location("download_m4_ptpa_llp_pilot_v2", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def test_real_pilot_builds_official_compatible_canary_and_full_plans(tmp_path):
    _, rows = module.verify_plan_inputs()
    canary = module.build_jobs(rows, tmp_path, "canary2")
    full = module.build_jobs(rows, tmp_path, "pilot150")

    assert len(canary) == 2
    assert len(full) == 150
    assert len({row["physical_media_id"] for row in full}) == 150
    assert all(Path(row["output_path"]).name == f"{row['physical_media_id']}.mp4" for row in full)
    assert all(row["expected_duration_sec"] == 10.0 for row in full)
    assert all(row["official_protocol"]["download_format"] == "best" for row in full)
    assert all(row["official_protocol"]["video_codec"] == "libx264" for row in full)
    assert all(row["official_protocol"]["audio_codec"] == "aac" for row in full)
    assert all(row["media_download_allowed"] is False for row in full)


def test_authorization_requires_rotated_credentials_and_fail_closed_permissions(tmp_path):
    lock = {
        "status": "llp_pilot_media_download_authorized",
        "scope": "canary2",
        "row_count": 2,
        "pilot_sha256": module.EXPECTED_PILOT_SHA256,
        "credential_mode": "rotated_cookie",
        "credentials_rotated_after_chat_exposure": False,
        "internal_noncommercial_research_only": True,
        "redistribution_allowed": False,
        "feature_extraction_allowed": False,
        "effect_access_allowed": False,
    }
    path = tmp_path / "authorization.json"
    path.write_text(json.dumps(lock), encoding="utf-8")
    with pytest.raises(ValueError, match="未证明凭据轮换"):
        module.verify_authorization(path, "canary2")

    lock["credentials_rotated_after_chat_exposure"] = True
    path.write_text(json.dumps(lock), encoding="utf-8")
    assert module.verify_authorization(path, "canary2")["row_count"] == 2


def test_anonymous_authorization_forbids_compromised_credentials(tmp_path):
    lock = {
        "status": "llp_pilot_media_download_authorized",
        "scope": "canary2",
        "row_count": 2,
        "pilot_sha256": module.EXPECTED_PILOT_SHA256,
        "credential_mode": "anonymous",
        "compromised_credentials_used": False,
        "internal_noncommercial_research_only": True,
        "redistribution_allowed": False,
        "feature_extraction_allowed": False,
        "effect_access_allowed": False,
    }
    path = tmp_path / "anonymous.json"
    path.write_text(json.dumps(lock), encoding="utf-8")
    assert module.verify_authorization(path, "canary2")["credential_mode"] == "anonymous"

    lock["compromised_credentials_used"] = True
    path.write_text(json.dumps(lock), encoding="utf-8")
    with pytest.raises(ValueError, match="泄露凭据"):
        module.verify_authorization(path, "canary2")


def test_media_validation_requires_official_codecs_audio_and_duration(tmp_path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"fixture")
    valid = {
        "streams": [
            {"codec_type": "video", "codec_name": "h264"},
            {"codec_type": "audio", "codec_name": "aac", "sample_rate": "44100", "channels": 2},
        ],
        "format": {"duration": "10.028"},
    }
    invalid = {
        "streams": [{"codec_type": "video", "codec_name": "vp9"}],
        "format": {"duration": "10.0"},
    }

    passed = module.validate_media(media, valid)
    failed = module.validate_media(media, invalid)
    assert passed["status"] == "pass"
    assert passed["sha256"] == module.sha256(media)
    assert failed["status"] == "fail"
    assert failed["checks"]["has_audio_stream"] is False


def test_plan_only_main_writes_locked_canary_without_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [str(SCRIPT), "--output-root", str(tmp_path), "--scope", "canary2"],
    )
    module.main()

    review = json.loads((tmp_path / "canary2_download_review.json").read_text(encoding="utf-8"))
    plan = [
        json.loads(line)
        for line in (tmp_path / "canary2_download_plan_locked.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert review["status"] == "plan_ready_execution_locked"
    assert review["permissions"]["media_download_allowed"] is False
    assert review["next_stage_allowed"] is False
    assert len(plan) == 2


def test_failed_canary_summary_never_unlocks_media_or_next_stage():
    results = [{"status": "fail"}, {"status": "fail"}]
    summary = {
        "passed": sum(row["status"] == "pass" for row in results),
        "failed": sum(row["status"] != "pass" for row in results),
        "media_qualification_passed": all(row["status"] == "pass" for row in results),
        "next_stage_allowed": False,
        "media_download_allowed": False,
    }
    assert summary == {
        "passed": 0,
        "failed": 2,
        "media_qualification_passed": False,
        "next_stage_allowed": False,
        "media_download_allowed": False,
    }
