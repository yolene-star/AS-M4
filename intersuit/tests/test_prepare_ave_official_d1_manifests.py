"""AVE 官方 D1 物理视频清单冻结器的 CPU 单元测试。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "prepare_ave_official_d1_manifests.py"
SPEC = importlib.util.spec_from_file_location("prepare_ave_official_d1_manifests", MODULE_PATH)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def _annotation(index: int, youtube_id: str) -> dict:
    return {
        "annotation_index": index,
        "event_label": f"event-{index}",
        "youtube_id": youtube_id,
        "quality": "good",
        "event_start_sec": 1 if index % 2 else 0,
        "event_end_sec": 9 if index % 2 else 10,
        "has_local_boundary": bool(index % 2),
    }


def test_validate_orders_requires_exact_partition():
    module.validate_orders(
        {"train": [0, 1], "validation": [2], "test": [3]},
        annotation_count=4,
    )
    with pytest.raises(ValueError, match="精确覆盖"):
        module.validate_orders(
            {"train": [0], "validation": [1], "test": [1]},
            annotation_count=3,
        )


def test_cross_split_duplicates_and_historical_exposure_are_excluded_from_all_candidates():
    annotations = [
        _annotation(0, "AAAAAAAAAAA"),
        _annotation(1, "BBBBBBBBBBB"),
        _annotation(2, "BBBBBBBBBBB"),
        _annotation(3, "CCCCCCCCCCC"),
        _annotation(4, "DDDDDDDDDDD"),
    ]
    orders = {
        "train": [0, 1],
        "validation": [2, 3],
        "test": [4],
    }
    raw, physical = module.build_split_rows(annotations, orders)
    exposures = {
        "CCCCCCCCCCC": {
            "youtube_id": "CCCCCCCCCCC",
            "categories": ["historical_harness"],
            "source_paths": ["/tmp/evidence.json"],
        }
    }

    candidates, excluded, cross_split = module.freeze_manifests(
        raw,
        physical,
        exposures,
    )

    assert [row["youtube_id"] for row in candidates["train"]] == ["AAAAAAAAAAA"]
    assert candidates["validation"] == []
    assert [row["youtube_id"] for row in candidates["test"]] == ["DDDDDDDDDDD"]
    assert [row["youtube_id"] for row in cross_split] == ["BBBBBBBBBBB"]
    reasons = {row["youtube_id"]: row["exclusion_reasons"] for row in excluded}
    assert reasons["BBBBBBBBBBB"] == ["cross_split_physical_duplicate"]
    assert reasons["CCCCCCCCCCC"] == ["historically_touched"]


def test_historical_inventory_intersects_only_official_ids(tmp_path):
    datasets_root = tmp_path / "datasets"
    ave_hf = datasets_root / "AVE_HF_LOCAL"
    (ave_hf / "videos").mkdir(parents=True)
    (ave_hf / "videos" / "AAAAAAAAAAA.mp4").write_bytes(b"")
    (ave_hf / "manifest.jsonl").write_text(
        json.dumps({"youtube_id": "BBBBBBBBBBB"}) + "\n",
        encoding="utf-8",
    )
    stage2 = datasets_root / "AS_M4_STAGE2"
    stage2.mkdir()
    (stage2 / "train_manifest.json").write_text(
        json.dumps([{"video_path": "/data/CCCCCCCCCCC.mp4"}, {"youtube_id": "not-official"}]),
        encoding="utf-8",
    )
    artifacts = tmp_path / "artifacts"
    artifact_dir = artifacts / "ave_hf_scorer"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "rows.json").write_text(
        json.dumps({"audio_donor_youtube_id": "DDDDDDDDDDD"}),
        encoding="utf-8",
    )
    m4_it = tmp_path / "m4-it.json"
    m4_it.write_text(json.dumps([{"image": "EEEEEEEEEEE.jpg"}]), encoding="utf-8")

    exposures, failures = module.collect_historical_exposures(
        {"AAAAAAAAAAA", "BBBBBBBBBBB", "CCCCCCCCCCC", "DDDDDDDDDDD"},
        datasets_root,
        artifacts,
        m4_it,
    )

    assert failures == []
    assert set(exposures) == {
        "AAAAAAAAAAA",
        "BBBBBBBBBBB",
        "CCCCCCCCCCC",
        "DDDDDDDDDDD",
    }
    assert "ave_hf_local_assets" in exposures["AAAAAAAAAAA"]["categories"]
    assert "stage2" in exposures["CCCCCCCCCCC"]["categories"]
    assert "historical_harness" in exposures["DDDDDDDDDDD"]["categories"]
    counts = module._category_counts(exposures.values())
    assert counts["m4_it"] == 0
