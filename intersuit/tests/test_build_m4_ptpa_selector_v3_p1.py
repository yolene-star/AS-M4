from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest
import torch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_m4_ptpa_selector_v3_p1.py"
SPEC = importlib.util.spec_from_file_location("build_m4_ptpa_selector_v3_p1", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def test_freeze_one_sparse_event_is_stable_and_one_per_video():
    rows = [
        {"video_id": "a", "sample_id": "a:0", "is_sparse": True},
        {"video_id": "a", "sample_id": "a:1", "is_sparse": True},
        {"video_id": "a", "sample_id": "a:2", "is_sparse": False},
        {"video_id": "b", "sample_id": "b:0", "is_sparse": True},
    ]

    first = module.freeze_one_sparse_event_per_video(rows, 2)
    replay = module.freeze_one_sparse_event_per_video(list(reversed(rows)), 2)

    assert first == replay
    assert [row["video_id"] for row in first] == ["a", "b"]
    assert len({row["sample_id"] for row in first}) == 2


def test_interval_metrics_and_first_hit_rank_use_temporal_overlap():
    timestamps = [[0.0, 1.0], [2.0, 3.0], [4.0, 5.0]]
    metrics = module.interval_metrics(timestamps, (2.25, 2.75))
    trace = {
        "selection_scores": [0.9, 0.2, 0.8],
        "selected_timestamps": timestamps,
        "selected_source_indices": [0, 2, 4],
    }

    assert metrics["event_coverage"] == 1.0
    assert metrics["positive_recall"] == 1.0
    assert metrics["best_tiou"] == pytest.approx(0.5)
    assert module.first_hit_rank(trace, (2.25, 2.75)) == 3


def test_v3_select_uses_unified_contract_for_oracle_and_seeded_random():
    row = {
        "sample_id": "sample",
        "event_start_sec": 2.1,
        "event_end_sec": 2.9,
    }
    payload = {
        "timestamps": torch.tensor([[0.0, 1.0], [1.0, 2.0], [2.0, 3.0], [3.0, 4.0]]),
        "energy_scores": torch.tensor([0.1, 0.2, 0.3, 0.4]),
        "eventness_cached_scores": torch.tensor([0.4, 0.3, 0.2, 0.1]),
    }

    oracle = module.select("oracle_topk", row, payload, 1)
    random_first = module.select("random_topk", row, payload, 2, seed=17)
    random_replay = module.select("random_topk", row, payload, 2, seed=17)

    assert oracle.selected_source_indices.tolist() == [2]
    assert oracle.policy_metadata["uses_ground_truth"] is True
    assert torch.equal(random_first.selected_source_indices, random_replay.selected_source_indices)


def test_bootstrap_delta_is_reproducible_and_positive():
    first = module.bootstrap_delta([1.0] * 8, [0.25] * 8)
    replay = module.bootstrap_delta([1.0] * 8, [0.25] * 8)

    assert first == replay
    assert first["observed_delta"] == pytest.approx(0.75)
    assert first["ci95_low"] > 0
