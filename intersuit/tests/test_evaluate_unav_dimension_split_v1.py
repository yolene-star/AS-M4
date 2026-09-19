"""UnAV 分维度统一 evaluator 的 CPU 单元测试。"""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/evaluate_unav_dimension_split_v1.py"
SPEC = importlib.util.spec_from_file_location("evaluate_unav_dimension_split_v1", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def test_aggregate_rows_recomputes_all_required_metrics():
    rows = [
        {"event_coverage": 0.5, "first_hit_rank": 2, "positive_window_recall": 0.25, "mean_best_tiou": 0.4, "selected_window_count": 4},
        {"event_coverage": 0.0, "first_hit_rank": None, "positive_window_recall": 0.0, "mean_best_tiou": 0.0, "selected_window_count": 4},
    ]
    result = module.aggregate_rows(rows)
    assert result["sample_count"] == 2
    assert result["event_coverage"] == 0.25
    assert result["window_hit_at_k"] == 0.5
    assert result["positive_window_recall"] == 0.125
    assert result["mean_best_tiou"] == 0.2
    assert result["mean_first_hit_rank_when_hit"] == 2
    assert result["mean_selected_window_count"] == 4


def test_aggregate_rows_without_hits_keeps_nan_rank():
    rows = [{"event_coverage": 0.0, "first_hit_rank": None, "positive_window_recall": 0.0, "mean_best_tiou": 0.0, "selected_window_count": 8}]
    assert math.isnan(module.aggregate_rows(rows)["mean_first_hit_rank_when_hit"])


def test_average_metrics_preserves_sample_count():
    items = [
        {"sample_count": 2, "event_coverage": 0.2, "window_hit_at_k": 0.5},
        {"sample_count": 2, "event_coverage": 0.4, "window_hit_at_k": 1.0},
    ]
    result = module.average_metrics(items)
    assert result["sample_count"] == 2
    assert result["event_coverage"] == pytest.approx(0.3)
    assert result["window_hit_at_k"] == 0.75


def test_empty_rows_fail_closed():
    with pytest.raises(ValueError, match="逐样本记录为空"):
        module.aggregate_rows([])
