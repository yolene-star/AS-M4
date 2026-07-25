from __future__ import annotations

import pytest
import numpy as np

from intersuit.harness.dynamic_selector_v2_data import (
    build_unav_video_label_samples,
    frozen_label_mapping,
    merge_intervals,
    window_target_mask,
)


def test_unav_groups_all_same_label_occurrences_into_one_query_target():
    rows = [
        {"video_id": "v1", "duration": 10.0, "timestamp": [1.0, 2.0], "event_label": "bell"},
        {"video_id": "v1", "duration": 10.0, "timestamp": [7.0, 8.0], "event_label": "bell"},
        {"video_id": "v1", "duration": 10.0, "timestamp": [4.0, 5.0], "event_label": "door"},
    ]
    mapping = frozen_label_mapping(row["event_label"] for row in rows)

    samples = build_unav_video_label_samples(rows, split="train", label_mapping=mapping)

    assert len(samples) == 2
    bell = next(sample for sample in samples if sample.event_label == "bell")
    assert bell.annotation_count == 2
    assert bell.target_intervals == ((1.0, 2.0), (7.0, 8.0))
    assert bell.union_duration == 2.0
    mask = window_target_mask(
        [[float(index), float(index + 1)] for index in range(10)],
        bell.target_intervals,
    )
    assert [index for index, positive in enumerate(mask) if positive] == [1, 7]


def test_unav_merges_overlapping_occurrences_without_double_counting_duration():
    assert merge_intervals([(1.0, 3.0), (2.0, 4.0), (6.0, 7.0)]) == (
        (1.0, 4.0),
        (6.0, 7.0),
    )


def test_unav_exclusion_and_frozen_train_label_mapping_fail_closed():
    rows = [
        {"video_id": "drop", "duration": 10.0, "timestamp": [1.0, 2.0], "event_label": "bell"},
        {"video_id": "keep", "duration": 10.0, "timestamp": [2.0, 3.0], "event_label": "bell"},
    ]
    samples = build_unav_video_label_samples(
        rows,
        split="validation",
        label_mapping={"bell": 0},
        excluded_video_ids={"drop"},
    )
    assert [sample.video_id for sample in samples] == ["keep"]
    with pytest.raises(ValueError, match="frozen train mapping"):
        build_unav_video_label_samples(
            [{"video_id": "v", "duration": 5.0, "timestamp": [1.0, 2.0], "event_label": "new"}],
            split="test",
            label_mapping={"bell": 0},
        )


def test_unav_accepts_numpy_timestamp_values_emitted_by_parquet():
    samples = build_unav_video_label_samples(
        [
            {
                "video_id": "v",
                "duration": 5.0,
                "timestamp": np.asarray([1.0, 2.0]),
                "event_label": "bell",
            }
        ],
        split="train",
        label_mapping={"bell": 0},
    )
    assert samples[0].target_intervals == ((1.0, 2.0),)
