"""D0 独立 selector contract harness 的 CPU 单元测试。"""

from __future__ import annotations

import pytest
import torch

from intersuit.harness.dynamic_selector_contract import (
    FIXED_CONTROL_SEEDS,
    audit_legacy_multiscale_output,
    build_five_seed_mismatch_controls,
    build_five_seed_random_controls,
    oracle_topk_indices,
    stable_topk_indices,
)
from intersuit.model.streaming_av.dynamic_window_selector import DynamicWindowSelector


def test_stable_topk_rejects_zero_and_uses_earlier_tie_break():
    scores = torch.tensor([0.5, 0.5, 0.1])
    timestamps = torch.tensor([[2.0, 3.0], [0.0, 1.0], [1.0, 2.0]])
    source_indices = torch.tensor([2, 0, 1])
    mask = torch.ones(3, dtype=torch.bool)

    with pytest.raises(ValueError, match="positive"):
        stable_topk_indices(scores, timestamps, source_indices, mask, k=0)
    assert stable_topk_indices(scores, timestamps, source_indices, mask, k=1) == [0]
    assert stable_topk_indices(scores, timestamps, source_indices, mask, k=9) == [0, 1, 2]


def test_random_and_mismatch_controls_use_all_five_fixed_seeds_and_reproduce():
    random_first = build_five_seed_random_controls(range(8), k=3)
    random_second = build_five_seed_random_controls(range(8), k=3)
    mismatch_first = build_five_seed_mismatch_controls(["a", "b", "c", "d"])
    mismatch_second = build_five_seed_mismatch_controls(["a", "b", "c", "d"])

    assert tuple(int(seed) for seed in random_first) == FIXED_CONTROL_SEEDS
    assert tuple(int(seed) for seed in mismatch_first) == FIXED_CONTROL_SEEDS
    assert random_first == random_second
    assert mismatch_first == mismatch_second
    for mapping in mismatch_first.values():
        assert set(mapping) == set(mapping.values())
        assert all(source != target for source, target in mapping.items())


def test_oracle_prefers_overlap_then_nearest_boundary_neighbours():
    timestamps = torch.tensor([[0.0, 1.0], [1.0, 2.0], [2.0, 3.0], [3.0, 4.0]])
    indices = torch.arange(4)
    mask = torch.ones(4, dtype=torch.bool)

    selected = oracle_topk_indices(timestamps, indices, mask, 2.25, 2.75, k=3)

    assert selected == [1, 2, 3]


def test_legacy_output_has_explicit_source_anchor_and_timestamp_audit():
    features = torch.zeros(1, 6, 4)
    features[0, 2] = torch.tensor([1.0, 0.0, 0.0, 0.0])
    timestamps = torch.tensor([[float(index), float(index + 1)] for index in range(6)])
    windows = torch.zeros(1, 6, 32)
    windows[0, 2] = 0.2
    mask = torch.tensor([[True, True, True, True, False, False]])
    selector = DynamicWindowSelector(input_dim=4, top_k=4, nms_iou=1.0)

    output = selector(features, timestamps, mask, windows)
    audit = audit_legacy_multiscale_output(output, timestamps, mask)

    assert audit["status"] == "pass"
    assert output.selected_source_indices.dtype == torch.long
    assert torch.all(output.selected_source_indices[~output.selection_mask] == -1)
    assert torch.equal(output.micro_scores, output.eventness_cached_scores)
