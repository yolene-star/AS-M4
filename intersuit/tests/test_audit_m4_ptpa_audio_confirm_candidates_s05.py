from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/audit_m4_ptpa_audio_confirm_candidates_s05.py"
SPEC = importlib.util.spec_from_file_location("audit_confirm_candidates_s05", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def ave_row(video_id: str, label: str) -> dict:
    return {"youtube_id": video_id, "annotations": [{"event_label": label}]}


def media_row(video_id: str) -> dict:
    return {
        "youtube_id": video_id,
        "status": "present",
        "media_sha256": "sha",
        "media_size_bytes": 1,
    }


def test_ave_requires_capacity_for_two_disjoint_cohorts():
    rows = [ave_row(f"a-{index}", "A") for index in range(10)]
    rows += [ave_row(f"b-{index}", "B") for index in range(9)]
    audit = module.audit_ave(
        rows,
        [media_row(row["youtube_id"]) for row in rows],
        ["A", "B"],
        set(),
        set(),
        set(),
    )
    assert audit["required_per_label_for_disjoint_cohorts"] == 10
    assert audit["insufficient_labels"] == {"B": 9}
    assert audit["candidate_capacity_passed"] is False
    assert audit["sealed_manifest_generation_allowed"] is False


def test_ave_rejects_any_historical_or_effect_overlap():
    rows = [ave_row(f"a-{index}", "A") for index in range(10)]
    media = [media_row(row["youtube_id"]) for row in rows]
    audit = module.audit_ave(rows, media, ["A"], {"a-0"}, set(), set())
    assert audit["historical_overlap_count"] == 1
    assert audit["candidate_capacity_passed"] is False


def test_longvale_keeps_reserved_confirm_out_of_candidates(tmp_path: Path):
    annotations = {f"v-{index}": {} for index in range(205)}
    archive_index = {
        "archive_count": 1,
        "archives": [
            {
                "archive": "raw/archive.zip",
                "archive_size": 10,
                "videos": [{"video_id": key} for key in annotations],
            }
        ],
    }
    exposed = [{"video_id": "v-0"}]
    reserved = [{"video_id": "v-1"}, {"video_id": "v-2"}]
    audit = module.audit_longvale(
        annotations, archive_index, exposed, reserved, set(), tmp_path
    )
    assert audit["metadata_candidate_video_count"] == 202
    assert audit["metadata_capacity_passed"] is True
    assert audit["media_capacity_passed"] is False
    assert audit["reserved_confirm_reuse_allowed"] is False
    assert audit["sealed_manifest_generation_allowed"] is False


def test_review_is_fail_closed_if_either_domain_is_blocked():
    ave = {"candidate_capacity_passed": False}
    longvale = {"metadata_capacity_passed": True, "media_capacity_passed": False}
    review = module.build_review(ave, longvale, {"input": "sha"})
    assert review["status"] == "blocked_before_confirm_freeze"
    assert review["next_stage_allowed"] is False
    assert review["permissions"]["confirm_manifest_generation_allowed"] is False
    assert review["permissions"]["gpu_execution_allowed"] is False
    assert review["permissions"]["training_allowed"] is False
