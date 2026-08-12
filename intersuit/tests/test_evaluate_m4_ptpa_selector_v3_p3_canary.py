from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "evaluate_m4_ptpa_selector_v3_p3_canary.py"
SPEC = importlib.util.spec_from_file_location("evaluate_m4_ptpa_selector_v3_p3_canary", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def test_official_predictions_use_local_one_event_index_zero():
    rows = [{"physical_media_id": "sha256:a", "prediction": "caption", "sentence_id": 38}]

    converted = module.official_predictions(rows)

    assert converted[0]["video_id"] == "sha256:a"
    assert converted[0]["info"]["sentence_id"] == 0


def test_official_predictions_follow_annotation_dictionary_order():
    rows = [
        {"physical_media_id": "video-b", "prediction": "b"},
        {"physical_media_id": "video-a", "prediction": "a"},
    ]

    converted = module.official_predictions(rows, ["video-a", "video-b"])

    assert [row["video_id"] for row in converted] == ["video-a", "video-b"]
    assert [row["answer"] for row in converted] == ["a", "b"]


def test_official_predictions_reject_annotation_set_drift():
    rows = [{"physical_media_id": "video-a", "prediction": "a"}]

    with pytest.raises(ValueError, match="集合漂移"):
        module.official_predictions(rows, ["video-b"])


def test_metric_summary_uses_five_seed_means_and_all_core_comparisons():
    metrics = {
        "behavior_off": {"METEOR": 0.10},
        "fixed_all": {"METEOR": 0.11},
        "current_micro_top8": {"METEOR": 0.12},
        "oracle_top8": {"METEOR": 0.15},
    }
    metrics.update({f"random_top8_seed_{seed}": {"METEOR": 0.10} for seed in range(5)})
    metrics.update({f"oracle_mismatch_seed_{seed}": {"METEOR": 0.09} for seed in range(5)})

    summary = module.summarize_metrics(metrics)

    assert summary["random_mean"] == 0.10
    assert summary["mismatch_mean"] == 0.09
    assert summary["oracle_minus_fixed_all"] == pytest.approx(0.04)
    assert summary["oracle_minus_current_micro_top8"] == pytest.approx(0.03)
    assert summary["oracle_minus_random_mean"] == pytest.approx(0.05)


def test_anomaly_counts_tracks_max_length_without_eos():
    rows = [{
        "prediction": "valid caption",
        "generation_debug": {"tokens": {
            "new_token_count": 128,
            "eos_token_id": 2,
            "last_20_new_token_ids": [10, 11],
            "first_token_is_eos": False,
        }},
    }]

    counts = module.anomaly_counts(rows, 128)

    assert counts["max_length_without_eos"] == 1
    assert counts["empty_output"] == 0
    assert counts["nonfinite_output"] == 0
