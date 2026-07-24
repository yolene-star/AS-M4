"""AVE D1 cached Eventness 指标脚本测试。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest
import torch


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/evaluate_ave_official_d1_validation_eventness.py"
)
spec = importlib.util.spec_from_file_location("ave_d1_eventness_metrics_tested", SCRIPT)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
assert spec.loader is not None
spec.loader.exec_module(module)


def sample_row(*, youtube_id: str = "sample", local: bool = True) -> dict:
    return {
        "youtube_id": youtube_id,
        "has_local_boundary": local,
        "annotations": [
            {
                "event_start_sec": 2.0,
                "event_end_sec": 3.0,
            }
        ],
    }


def test_eventness_uses_same_frozen_ranking_for_all_budgets():
    scores = torch.tensor([0.0, 0.1, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2])
    records, metrics = module.evaluate_eventness(
        [sample_row()],
        {"sample": scores},
    )

    assert len(records) == 3
    assert [record["budget"] for record in records] == [2, 4, 8]
    assert all(record["strategy"] == "eventness_cached_topk" for record in records)
    assert records[0]["selected_ranked_indices"] == [2, 3]
    assert records[1]["selected_ranked_indices"][:2] == [2, 3]
    assert (
        metrics["local_boundary_primary"][
            "eventness_cached_topk|K=4|seed=None"
        ]["window_hit_at_k"]
        == pytest.approx(1.0)
    )


def test_eventness_primary_excludes_nonlocal_sample():
    rows = [
        sample_row(youtube_id="local", local=True),
        sample_row(youtube_id="nonlocal", local=False),
    ]
    _, metrics = module.evaluate_eventness(
        rows,
        {
            "local": torch.arange(10, dtype=torch.float32),
            "nonlocal": torch.arange(10, dtype=torch.float32),
        },
    )

    value = metrics["all_physical_diagnostic"][
        "eventness_cached_topk|K=2|seed=None"
    ]
    assert value["sample_count"] == 2
    assert metrics["local_boundary_primary"][
        "eventness_cached_topk|K=2|seed=None"
    ]["sample_count"] == 1
