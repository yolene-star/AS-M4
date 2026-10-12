import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts/evaluate_longvale_v2_oracle_pilot.py"
SPEC = importlib.util.spec_from_file_location("evaluate_longvale_v2_oracle_pilot", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def row(condition: str, sample_id: str = "sample-1") -> dict:
    return {
        "sample_id": sample_id,
        "experiment_id": condition,
        "physical_media_id": "video-1",
        "sentence_id": 0,
        "prediction": "A person opens the door.",
    }


def test_official_predictions_preserve_physical_video_and_sentence_id():
    converted = MODULE.official_predictions([row("all")])
    assert converted == [
        {
            "video_id": "video-1",
            "task": "seg_captioning",
            "query_id": 0,
            "answer": "A person opens the door.",
            "info": {"sentence_id": 0},
        }
    ]


def test_matrix_validation_is_fail_closed_on_order_drift():
    rows = {
        "all": [row("all", "a"), row("all", "b")],
        "oracle_topk": [row("oracle_topk", "b"), row("oracle_topk", "a")],
    }
    with pytest.raises(ValueError, match="样本顺序"):
        MODULE.validate_prediction_matrix(
            conditions=["all", "oracle_topk"], rows_by_condition=rows, expected_count=2
        )


def test_repetition_detector_only_flags_strong_repetition():
    assert not MODULE.has_degenerate_repetition("A person opens a door and then walks away.")
    assert MODULE.has_degenerate_repetition("A person opens the wooden door. A person opens the wooden door.")


def test_pilot_gate_requires_every_preregistered_check():
    metrics = {
        "text_only": {"METEOR": 0.10},
        "all": {"METEOR": 0.11},
        "oracle_topk": {"METEOR": 0.14},
        "uniform_topk": {"METEOR": 0.125},
        "energy_topk": {"METEOR": 0.12},
        "onset_topk": {"METEOR": 0.11},
        "eventness_cached_topk": {"METEOR": 0.115},
    }
    metrics.update({f"random_topk_seed_{seed}": {"METEOR": 0.12} for seed in range(5)})
    metrics.update({f"oracle_mismatch_seed_{seed}": {"METEOR": 0.13} for seed in range(5)})
    anomalies = {name: {"empty_output": 0} for name in metrics}

    result = MODULE.evaluate_pilot_gate(metrics, anomalies)
    assert result["status"] == "pass"
    assert result["locked_deterministic_strategy"] == "uniform_topk"

    anomalies["oracle_topk"]["repeated_text"] = 1
    anomalies["all"]["repeated_text"] = 0
    result = MODULE.evaluate_pilot_gate(metrics, anomalies)
    assert result["status"] == "stop"
    assert result["oracle_anomaly_delta_vs_all"]["repeated_text"] == 1

    anomalies["all"]["repeated_text"] = 1
    metrics["all"]["METEOR"] = 0.135
    result = MODULE.evaluate_pilot_gate(metrics, anomalies)
    assert result["status"] == "stop"
    assert result["next_action"] == "stop_selector_route"
    assert result["locked_deterministic_strategy"] is None
