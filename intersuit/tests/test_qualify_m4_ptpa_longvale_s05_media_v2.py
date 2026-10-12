from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/qualify_m4_ptpa_longvale_s05_media_v2.py"
SPEC = importlib.util.spec_from_file_location("qualify_m4_ptpa_longvale_s05_media_v2", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def test_real_part19_plan_uses_all_eligible_videos_and_one_event_each():
    _, _, archive_item = module.verify_inputs()
    annotations = module.parse_training_annotations(module.ANNOTATIONS)
    rows, mismatch, summary = module.build_sealed_plan(archive_item, annotations)

    assert len(rows) == 186
    assert len({row["video_id"] for row in rows}) == 186
    assert len({row["sample_id"] for row in rows}) == 186
    assert summary["archive_video_count"] == 198
    assert summary["excluded_exposed_count"] == 6
    assert summary["excluded_reserved_count"] == 6
    assert summary["selected_physical_video_count"] == 186
    assert summary["events_per_video"] == 1
    assert summary["content_or_model_based_selection"] is False
    assert all(row["event_start_sec"] < row["event_end_sec"] <= row["duration_sec"] for row in rows)
    assert all(row["model_inference_allowed"] is False for row in rows)
    assert all(row["effect_access_allowed"] is False for row in rows)
    assert len(mismatch) == 5


def test_event_selection_is_deterministic_and_order_independent():
    events = [
        {"event_index": 0, "start_sec": 0.0, "end_sec": 1.0, "caption": "a"},
        {"event_index": 1, "start_sec": 1.0, "end_sec": 2.0, "caption": "b"},
    ]
    first = module.select_event("video", events)
    second = module.select_event("video", list(reversed(events)))

    assert first == second


def test_mismatch_mappings_are_derangements_on_same_186_videos():
    _, _, archive_item = module.verify_inputs()
    annotations = module.parse_training_annotations(module.ANNOTATIONS)
    rows, mismatch, _ = module.build_sealed_plan(archive_item, annotations)
    ids = {row["video_id"] for row in rows}

    for mapping in mismatch.values():
        assert set(mapping) == ids
        assert set(mapping.values()) == ids
        assert all(source != target for source, target in mapping.items())


def test_extract_member_validates_size_and_crc(tmp_path):
    archive = tmp_path / "fixture.zip"
    payload = b"not-a-real-video-but-valid-zip-member"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("video_train_7240/fixture.mp4", payload)
    with zipfile.ZipFile(archive, "r") as zf:
        info = zf.getinfo("video_train_7240/fixture.mp4")
        row = {
            "video_id": "fixture",
            "media": {
                "member": info.filename,
                "uncompressed_size": info.file_size,
                "compressed_size": info.compress_size,
                "crc32": info.CRC,
                "compression_method": info.compress_type,
            },
        }
        output, status = module.extract_member(zf, row, tmp_path / "media")
    assert status == "extracted"
    assert output.read_bytes() == payload
    assert module.crc32_file(output) == info.CRC


def test_media_qualified_review_still_forbids_effect_access():
    plan = {
        "archive_video_count": 198,
        "excluded_exposed_count": 6,
        "excluded_reserved_count": 6,
        "selected_physical_video_count": 186,
    }
    audit_rows = [
        {"video_id": str(index), "status": "pass", "bytes": 1}
        for index in range(186)
    ]
    review = module.build_review(plan, {}, "plan-sha", "mismatch-sha", audit_rows, plan_only=False)

    assert review["status"] == "longvale_media_qualified_effect_locked"
    assert review["longvale_media_blocker_closed"] is True
    assert review["permissions"]["sealed_manifest_qualified"] is True
    assert review["permissions"]["feature_extraction_allowed"] is False
    assert review["permissions"]["model_inference_allowed"] is False
    assert review["permissions"]["confirm_effect_access_allowed"] is False
    assert review["permissions"]["gpu_execution_allowed"] is False
    assert review["permissions"]["training_allowed"] is False
    assert review["next_stage_allowed"] is False


def test_plan_only_main_writes_sealed_effect_locked_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [str(SCRIPT), "--output-root", str(tmp_path), "--plan-only"],
    )
    module.main()

    rows = [
        json.loads(line)
        for line in (tmp_path / "longvale_confirm_sealed_effect_locked.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]
    review = json.loads((tmp_path / "longvale_s05_media_review.json").read_text(encoding="utf-8"))
    assert len(rows) == 186
    assert review["status"] == "sealed_plan_ready_media_not_audited"
    assert review["permissions"]["confirm_effect_access_allowed"] is False
    assert review["next_stage_allowed"] is False
