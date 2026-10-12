from __future__ import annotations

import pytest

from intersuit.harness.dynamic_selector_v2_localization import (
    all_indices,
    evaluate_selection,
    fixed_window_timestamps,
    oracle_topk_indices,
    random_topk_indices,
    uniform_topk_indices,
    validate_s1_manifest_records,
)


def test_fixed_windows_clamp_partial_tail_to_media_duration():
    assert fixed_window_timestamps(2.25) == [[0.0, 1.0], [1.0, 2.0], [2.0, 2.25]]


def test_uniform_topk_is_deterministic_and_respects_k():
    assert uniform_topk_indices(10, 4) == [0, 3, 6, 9]
    assert uniform_topk_indices(3, 8) == [0, 1, 2]


def test_random_topk_is_sample_stable_and_seed_sensitive():
    first = random_topk_indices(20, 8, seed=1, sample_id="sample")
    assert first == random_topk_indices(20, 8, seed=1, sample_id="sample")
    assert first != random_topk_indices(20, 8, seed=2, sample_id="sample")
    assert len(first) == len(set(first)) == 8


def test_oracle_maximizes_nonoverlap_window_coverage_with_earlier_tie_break():
    timestamps = fixed_window_timestamps(10.0)
    selected = oracle_topk_indices(timestamps, [[2.2, 3.8], [7.0, 7.5]], 2)
    assert selected == [2, 3]
    metrics = evaluate_selection(timestamps, [[2.2, 3.8], [7.0, 7.5]], selected)
    assert metrics.event_coverage == pytest.approx(1.6 / 2.1)
    assert metrics.first_hit_rank == 1


def test_all_selection_has_exact_full_coverage_and_positive_recall():
    timestamps = fixed_window_timestamps(5.2)
    metrics = evaluate_selection(timestamps, [[0.2, 1.4], [4.9, 5.2]], all_indices(len(timestamps)))
    assert metrics.event_coverage == pytest.approx(1.0)
    assert metrics.positive_window_recall == pytest.approx(1.0)


def test_selection_rejects_duplicate_and_out_of_range_indices():
    timestamps = fixed_window_timestamps(3.0)
    with pytest.raises(ValueError, match="unique"):
        evaluate_selection(timestamps, [[1.0, 2.0]], [1, 1])
    with pytest.raises(ValueError, match="out of range"):
        evaluate_selection(timestamps, [[1.0, 2.0]], [3])


def _manifest_record(*, sample_id="sample", split="validation"):
    return {
        "sample_id": sample_id,
        "split": split,
        "video_id": "video",
        "duration": 10.0,
        "event_label": "event",
        "target_intervals": [[1.0, 2.0]],
        "union_duration_ratio": 0.1,
    }


def test_s1_manifest_validation_accepts_train_and_validation_only():
    assert validate_s1_manifest_records([_manifest_record(split="train")]) == "train"
    assert validate_s1_manifest_records([_manifest_record(split="validation")]) == "validation"


def test_s1_manifest_validation_refuses_test_before_gate():
    with pytest.raises(ValueError, match="refuses test"):
        validate_s1_manifest_records([_manifest_record(split="test")])


def test_s1_manifest_validation_rejects_duplicate_ids_and_mixed_splits():
    with pytest.raises(ValueError, match="duplicate sample_id"):
        validate_s1_manifest_records([_manifest_record(), _manifest_record()])
    with pytest.raises(ValueError, match="exactly one split"):
        validate_s1_manifest_records(
            [_manifest_record(sample_id="a", split="train"), _manifest_record(sample_id="b")]
        )
