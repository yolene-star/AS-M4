from __future__ import annotations

from collections import Counter
import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/qualify_m4_ptpa_llp_metadata_v2.py"
SPEC = importlib.util.spec_from_file_location("qualify_m4_ptpa_llp_metadata_v2", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def test_real_metadata_contract_and_known_history_counts():
    lock, _ = module.verify_responsibility_lock()
    audit, clean = module.audit_metadata(lock, module.known_ave_history_ids())

    assert audit["official_rows"] == {"train": 10000, "validation": 649, "test": 1200}
    assert audit["official_split_physical_overlap"] == {
        "train_validation": 0,
        "train_test": 0,
        "validation_test": 0,
    }
    assert audit["known_ave_history_overlap"] == {"train": 189, "validation": 13, "test": 17}
    assert {split: len(rows) for split, rows in clean.items()} == {
        "train": 9811,
        "validation": 636,
        "test": 1183,
    }
    assert audit["dense_annotation_coverage"]["validation_test_union_covered"] == 1849


def test_decimal_clip_boundaries_preserve_physical_id_and_interval():
    filename = "AP0061o0Nvk_101.8_111.8"

    assert module.physical_id(filename) == "AP0061o0Nvk"
    assert module.clip_interval(filename) == (101.8, 111.8)


def test_balanced_pilot_is_exact_and_deterministic():
    lock, _ = module.verify_responsibility_lock()
    _, clean = module.audit_metadata(lock, module.known_ave_history_ids())

    first = module.select_balanced_pilot(
        clean["validation"], split="validation", target_count=50, minimum_per_label=2
    )
    second = module.select_balanced_pilot(
        list(reversed(clean["validation"])),
        split="validation",
        target_count=50,
        minimum_per_label=2,
    )
    assert [row["filename"] for row in first] == [row["filename"] for row in second]
    assert len(first) == 50
    counts = Counter(label for row in first for label in module.labels(row))
    assert len(counts) == 25
    assert min(counts.values()) >= 2


def test_build_outputs_excludes_test_and_remains_fail_closed():
    review, pilot, summary = module.build_outputs()

    assert len(pilot) == 150
    assert Counter(row["official_split"] for row in pilot) == {"train": 100, "validation": 50}
    assert all(row["official_split"] != "test" for row in pilot)
    assert all(row["download_allowed"] is False for row in pilot)
    assert all(row["effect_access_allowed"] is False for row in pilot)
    assert summary["full_historical_overlap"] == 0
    assert review["metadata_audit"]["full_historical_overlap"] == {
        "train": 343,
        "validation": 21,
        "test": 46,
    }
    assert review["metadata_audit"]["full_historical_clean_rows"] == {
        "train": 9468,
        "validation": 615,
        "test": 1137,
    }
    assert review["metadata_audit"]["full_historical_ledger"]["avqa_source_youtube_id_count"] == 51157
    assert review["status"] == "blocked_before_llp_pilot_download"
    assert review["permissions"]["pilot_manifest_frozen"] is True
    assert review["permissions"]["pilot_download_allowed"] is False
    assert review["permissions"]["test_media_access_allowed"] is False
    assert review["permissions"]["gpu_execution_allowed"] is False
    assert review["permissions"]["training_allowed"] is False
    assert review["next_stage_allowed"] is False


def test_main_writes_review_and_locked_plan(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--output-root", str(tmp_path)])
    module.main()

    review = json.loads((tmp_path / "llp_metadata_review.json").read_text(encoding="utf-8"))
    plan_rows = [
        json.loads(line)
        for line in (tmp_path / "llp_pilot_plan_locked_not_allowed.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]
    assert review["status"] == "blocked_before_llp_pilot_download"
    assert len(plan_rows) == 150
    assert (tmp_path / "LLP_METADATA_REVIEW_ZH.md").is_file()
