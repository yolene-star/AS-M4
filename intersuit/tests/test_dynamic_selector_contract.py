"""D0 独立 selector contract harness 的 CPU 单元测试。"""

from __future__ import annotations

import pytest
import torch

from intersuit.harness.dynamic_selector_contract import (
    FIXED_CONTROL_SEEDS,
    ValidatedTensorCache,
    audit_legacy_multiscale_output,
    audit_micro_window_output,
    build_five_seed_mismatch_controls,
    build_five_seed_random_controls,
    oracle_topk_indices,
    stable_topk_indices,
    tensor_sha256,
)
from intersuit.model.streaming_av.dynamic_window_selector import (
    DynamicWindowSelector,
    LegacyMultiscaleDynamicWindowSelector,
)


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
    selector = LegacyMultiscaleDynamicWindowSelector(input_dim=4, top_k=4, nms_iou=1.0)

    output = selector(features, timestamps, mask, windows)
    audit = audit_legacy_multiscale_output(output, timestamps, mask)

    assert audit["status"] == "pass"
    assert output.selected_source_indices.dtype == torch.long
    assert torch.all(output.selected_source_indices[~output.selection_mask] == -1)
    assert torch.equal(output.micro_scores, output.eventness_cached_scores)


def test_formal_micro_window_audit_rejects_no_pooling_or_timestamp_changes():
    features = torch.arange(24, dtype=torch.float32).reshape(1, 6, 4)
    timestamps = torch.tensor([[float(index), float(index + 1)] for index in range(6)])
    windows = torch.zeros(1, 6, 32)
    mask = torch.tensor([[True, True, True, True, False, False]])
    output = DynamicWindowSelector(input_dim=4, top_k=3)(
        features,
        timestamps,
        mask,
        windows,
    )

    audit = audit_micro_window_output(output, features, timestamps, mask)

    assert audit["status"] == "pass"
    assert audit["selection_unit"] == "original_micro_window"


def test_validated_cache_rejects_miss_duplicate_corruption_and_input_sha_change(tmp_path):
    input_tensor = torch.arange(8, dtype=torch.float32)
    input_sha = tensor_sha256(input_tensor)
    changed_input_sha = tensor_sha256(input_tensor + 1)
    cache = ValidatedTensorCache(tmp_path / "cache")

    with pytest.raises(KeyError, match="cache miss"):
        cache.get("sample", input_sha256=input_sha)

    cached = input_tensor.square()
    cache.put("sample", cached, input_sha256=input_sha)
    assert torch.equal(cache.get("sample", input_sha256=input_sha), cached)
    with pytest.raises(ValueError, match="duplicate"):
        cache.put("sample", cached, input_sha256=input_sha)
    with pytest.raises(ValueError, match="input SHA changed"):
        cache.get("sample", input_sha256=changed_input_sha)

    cache_path = cache.root / cache.entries["sample"]["path"]
    cache_path.write_bytes(b"not-a-valid-torch-cache")
    with pytest.raises(ValueError, match="corrupt"):
        cache.get("sample", input_sha256=input_sha)


def test_validated_cache_rejects_corrupt_manifest(tmp_path):
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    (cache_root / "manifest.json").write_text("{broken", encoding="utf-8")

    with pytest.raises(ValueError, match="manifest is corrupt"):
        ValidatedTensorCache(cache_root)
