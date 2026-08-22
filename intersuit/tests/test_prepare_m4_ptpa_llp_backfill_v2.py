from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/prepare_m4_ptpa_llp_backfill_v2.py"
SPEC = importlib.util.spec_from_file_location("prepare_m4_ptpa_llp_backfill_v2", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def test_real_backfill_is_deterministic_same_split_and_effect_locked():
    first, summary = module.build_outputs()
    second, second_summary = module.build_outputs()

    assert first == second
    assert len(first) == 11
    assert summary["qualified_original_media"] == 139
    assert summary["failure_class_counts"] == {
        "private": 6,
        "unavailable": 4,
        "age_restricted": 1,
    }
    assert summary["backfill_counts"] == {"train": 7, "validation": 4}
    assert summary["historical_overlap"] == 0
    assert summary["original_pilot_overlap"] == 0
    assert summary["content_or_effect_based_selection"] is False
    assert summary["download_allowed"] is False
    assert summary["effect_access_allowed"] is False
    assert second_summary["backfill_counts"] == summary["backfill_counts"]


def test_main_writes_locked_backfill_plan(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--output-root", str(tmp_path)])
    module.main()

    assert (tmp_path / "llp_pilot_backfill11_plan_locked.jsonl").read_text().count("\n") == 11
    assert (tmp_path / "llp_pilot_backfill11_review.json").is_file()


def test_real_round2_replacement_excludes_original_and_first_backfill():
    root = module.MEDIA_ROOT / "backfill_v2"
    rows, summary = module.build_replacement_outputs(
        root / "llp_pilot_backfill11_plan_locked.jsonl",
        module.MEDIA_ROOT / "backfill11_download_results.jsonl",
        "43f9f93bfbeea74e7ea4f4defd380538c8ed0180ebd8950f0777264fc7b40a7c",
        [],
    )

    assert len(rows) == 3
    assert summary["replacement_counts"] == {"train": 2, "validation": 1}
    assert summary["failure_class_counts"] == {"unavailable": 3}
    assert summary["historical_overlap"] == 0
    assert summary["prior_plan_overlap"] == 0
    assert summary["content_or_effect_based_selection"] is False


def test_coverage_repair_restores_label_minima_without_history_overlap():
    rows, summary = module.build_coverage_repair_outputs()

    assert len(rows) == 11
    assert summary["repair_counts"] == {"train": 7, "validation": 4}
    assert summary["projected_min_per_label"] == {"train": 4, "validation": 2}
    assert summary["projected_below_required"] == {"train": {}, "validation": {}}
    assert summary["historical_overlap"] == 0
    assert summary["original_pilot_overlap"] == 0
    assert summary["known_failed_backfill_overlap"] == 0
    assert summary["known_failed_candidate_count"] >= 4
    assert summary["content_or_effect_based_selection"] is False
