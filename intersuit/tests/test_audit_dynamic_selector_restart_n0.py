"""独立重启 N0 元数据动态范围审计的 CPU 单元测试。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "audit_dynamic_selector_restart_n0.py"
SPEC = importlib.util.spec_from_file_location("audit_dynamic_selector_restart_n0", MODULE_PATH)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def _row(duration: float, start: float, end: float):
    return module.EventRow("fixture", "train", "video-a", duration, start, end, "event")


def test_window_counts_preserve_partial_tail_and_positive_overlap():
    assert module.window_counts(_row(10.5, 9.8, 10.4)) == (11, 2)
    assert module.window_counts(_row(10.0, 2.0, 5.0)) == (10, 3)


def test_theoretical_random_hit_matches_combinatorial_definition():
    assert module.theoretical_random_hit(10, 5, 4) == pytest.approx(
        1 - (5 * 4 * 3 * 2) / (10 * 9 * 8 * 7)
    )
    assert module.theoretical_random_hit(10, 8, 4) == 1.0
    assert module.theoretical_random_hit(10, 5, 0) == 0.0


def test_event_metrics_ratio_budget_uses_ceil():
    metrics = module.event_metrics(_row(10.5, 1.0, 2.0))
    assert metrics["valid_window_count"] == 11
    assert metrics["budget_ratio_10"] == 2
    assert metrics["budget_ratio_25"] == 3


def test_invalid_event_boundary_is_rejected():
    with pytest.raises(ValueError, match="边界非法"):
        module._validated_event("fixture", "train", "video-a", 10.0, 8.0, 11.0, "event")


def test_frame_rounding_tolerance_clamps_small_overrun_only():
    row = module._validated_event("fixture", "train", "video-a", 10.0, 9.0, 10.02, "event")
    assert row.event_end == 10.0
    with pytest.raises(ValueError, match="边界非法"):
        module._validated_event("fixture", "train", "video-a", 10.0, 9.0, 10.026, "event")


def test_longvale_eval_parallel_schema_requires_equal_lengths():
    pairs = module._longvale_segment_pairs(
        {"timestamps": [[1.0, 2.0], [3.0, 4.0]], "sentences": ["one", "two"]},
        "eval",
        "video-a",
    )
    assert pairs == [([1.0, 2.0], "one"), ([3.0, 4.0], "two")]
    with pytest.raises(ValueError, match="数量不一致"):
        module._longvale_segment_pairs(
            {"timestamps": ["1.0_2.0"], "sentences": []},
            "eval",
            "video-a",
        )


def test_longvale_timestamp_parser_supports_only_frozen_formats():
    assert module._parse_longvale_timestamp("1.0_2.0") == (1.0, 2.0)
    assert module._parse_longvale_timestamp([1.0, 2.0]) == (1.0, 2.0)
    with pytest.raises(ValueError, match="timestamp 非法"):
        module._parse_longvale_timestamp("1.0-2.0")


def test_longvale_data_quality_records_small_overrun(tmp_path):
    path = tmp_path / "longvale.json"
    path.write_text(
        '{"video-a": {"duration": "10.0", "segments": {"9.0_10.02": "event"}}}',
        encoding="utf-8",
    )
    quality = module.longvale_data_quality(path, "training")
    assert quality["clamped_event_count"] == 1
    assert quality["maximum_overrun_sec"] == pytest.approx(0.02)


def test_exposure_matching_accepts_exact_path_stem_but_not_substrings(tmp_path):
    path = tmp_path / "history.json"
    path.write_text(
        '{"exact": "video-a", "path": "/tmp/video-b.mp4", "other": "prefix-video-c"}',
        encoding="utf-8",
    )
    result = module.collect_exact_exposures([path], {"video-a", "video-b", "video-c"})
    assert set(result) == {"video-a", "video-b"}


def test_qualification_fails_when_high_event_ratio_quartile_is_saturated():
    rows = []
    for index in range(100):
        duration = 100.0
        event_end = 5.0 if index < 75 else 90.0
        rows.append(
            module.EventRow("fixture", "train", f"video-{index}", duration, 0.0, event_end, "event")
        )
    result = module.qualification("fixture", {"train": module.summarize(rows), "test": module.summarize(rows)}, 0)
    assert result["checks"]["overall_25pct_headroom_at_least_15pp"] is True
    assert result["checks"]["all_event_ratio_quartiles_headroom_at_least_10pp"] is False
    assert result["status"] == "n0_failed_data_or_metric_incompatible"
