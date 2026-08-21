from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/qualify_m4_ptpa_llp_pilot_download_v2.py"
SPEC = importlib.util.spec_from_file_location("qualify_m4_ptpa_llp_pilot_download_v2", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def test_real_historical_ledger_has_zero_pilot_overlap_and_fails_closed_on_missing_avqa():
    _, pilot, _ = module.verify_inputs()
    ledger, summary = module.build_historical_ledger(pilot)

    assert len(pilot) == 150
    assert len(ledger) == summary["canonical_youtube_union_count"]
    assert summary["pilot_historical_overlap_count"] == 0
    assert summary["pilot_historical_overlap_ids"] == []
    assert summary["source_counts"]["ave_historical_exposure"] == 2500
    assert summary["m4_it_scope"]["row_count"] == 9963
    assert summary["m4_it_scope"]["youtube_alias_available"] is False
    assert summary["coverage_limitations"] == ["avqa_local_physical_id_inventory_missing"]
    assert summary["avqa_scope"]["official_sources"]["expected_physical_video_count"] == 57015
    assert summary["avqa_scope"]["third_party_incomplete_repack_not_accepted_as_full_history"] is True
    assert summary["full_history_closed"] is False


def test_license_boundary_does_not_misapply_repository_gpl_to_youtube_media():
    boundary = module.license_boundary()

    assert boundary["status"] == "boundary_locked_per_item_rights_unverified"
    assert boundary["raw_youtube_media_inherits_repository_license"] is False
    assert boundary["policy"]["metadata_probe_allowed"] is True
    assert boundary["policy"]["media_download_allowed"] is False
    assert boundary["policy"]["redistribution_allowed"] is False
    assert boundary["policy"]["explicit_authorization_lock_required_before_media_download"] is True


def test_probe_plan_allows_metadata_only_and_keeps_every_effect_permission_false():
    _, pilot, _ = module.verify_inputs()
    rows = module.reviewed_probe_rows(pilot)

    assert len(rows) == 150
    assert all(row["metadata_probe_allowed"] is True for row in rows)
    assert all(row["media_download_allowed"] is False for row in rows)
    assert all(row["feature_extraction_allowed"] is False for row in rows)
    assert all(row["effect_access_allowed"] is False for row in rows)
    assert all(row["official_split"] != "test" for row in rows)


def test_probe_summary_detects_missing_duplicate_and_complete_contracts():
    plan = [{"sample_id": "a"}, {"sample_id": "b"}]
    complete = module.summarize_probes(
        plan,
        [
            {"sample_id": "a", "status": "metadata_available", "availability": "public", "license": None},
            {"sample_id": "b", "status": "probe_failed", "availability": None, "license": None},
        ],
    )
    duplicate = module.summarize_probes(
        plan,
        [{"sample_id": "a", "status": "metadata_available"}] * 2,
    )

    assert complete["structural_complete"] is True
    assert complete["qualification_complete"] is False
    assert complete["metadata_available"] == 1
    assert complete["media_download_allowed"] is False
    assert duplicate["structural_complete"] is False
    assert duplicate["qualification_complete"] is False
    assert duplicate["duplicates"] == 1
    assert duplicate["missing_sample_ids"] == ["b"]


def test_post_download_audit_requires_video_audio_duration_and_sha(tmp_path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"fixture")
    row = {"sample_id": "x", "physical_media_id": "abcdefghijk", "start_sec": 2.0, "end_sec": 12.0}
    probe = {
        "streams": [{"codec_type": "video"}, {"codec_type": "audio"}],
        "format": {"duration": "10.2"},
    }
    result = module.audit_downloaded_media(row, media, probe)

    assert result["status"] == "pass"
    assert result["sha256"] == module.sha256(media)
    assert result["feature_extraction_allowed"] is False
    assert result["effect_access_allowed"] is False


def test_plan_only_main_writes_review_and_never_authorizes_download(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--output-root", str(tmp_path)])
    module.main()

    review = json.loads((tmp_path / "llp_pilot_download_review.json").read_text(encoding="utf-8"))
    rows = [
        json.loads(line)
        for line in (tmp_path / "llp_metadata_probe_plan_media_locked.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]
    assert review["status"] == "blocked_before_llp_pilot_download"
    assert review["permissions"]["metadata_probe_allowed"] is True
    assert review["permissions"]["pilot_media_download_allowed"] is False
    assert review["next_stage_allowed"] is False
    assert len(rows) == 150
