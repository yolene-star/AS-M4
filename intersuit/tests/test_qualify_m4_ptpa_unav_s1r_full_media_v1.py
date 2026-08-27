"""UnAV S1-R全量train/validation媒体资格测试。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/qualify_m4_ptpa_unav_s1r_full_media_v1.py"
SPEC = importlib.util.spec_from_file_location("qualify_m4_ptpa_unav_s1r_full_media_v1", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_build_selection_uses_only_eligible_train_validation(tmp_path):
    ledger = [
        {"physical_media_id": "train_a", "official_split": "train", "eligible": True, "duration_sec": 2.0, "event_count": 1},
        {"physical_media_id": "val_a", "official_split": "validation", "eligible": True, "duration_sec": 3.0, "event_count": 2},
        {"physical_media_id": "test_a", "official_split": "test", "eligible": True, "duration_sec": 4.0, "event_count": 1},
        {"physical_media_id": "train_old", "official_split": "train", "eligible": False, "duration_sec": 2.0, "event_count": 1},
    ]
    members = [
        {
            "physical_media_id": row["physical_media_id"],
            "official_split": row["official_split"],
            "metadata_present": True,
            "eligible_after_known_history": row["eligible"],
            "member_name": f"root/{row['physical_media_id']}.mp4",
            "media_size_bytes": 10 if row["physical_media_id"] == "train_a" else 20,
        }
        for row in ledger
    ]
    ledger_path = tmp_path / "ledger.jsonl"
    member_path = tmp_path / "members.jsonl"
    write_jsonl(ledger_path, ledger)
    write_jsonl(member_path, members)
    scope = {
        "requested": 2,
        "train": 1,
        "validation": 1,
        "expected_media_bytes": 30,
        "expected_duration_sec": 5.0,
        "expected_event_count": 3,
    }

    selected = module.build_selection(ledger_path, member_path, scope)

    assert [row["plan"]["physical_media_id"] for row in selected] == ["train_a", "val_a"]
    assert all(row["plan"]["official_split"] != "test" for row in selected)


def test_cached_passed_result_revalidates_media_sha(tmp_path):
    media = tmp_path / "video.mp4"
    media.write_bytes(b"media")
    result_path = tmp_path / "video.json"
    result_path.write_text(
        json.dumps(
            {
                "passed": True,
                "size": media.stat().st_size,
                "sha256": module.sha256(media),
            }
        )
    )

    result = module.cached_passed_result(
        result_path, media, module.sha256(media), media.stat().st_size
    )

    assert result is not None and result["passed"] is True
