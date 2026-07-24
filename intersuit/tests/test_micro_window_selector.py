"""正式原始 micro-window selector 的 D0 CPU contract 测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import torch

from intersuit.model.llava_arch import normalize_scene_audio_window_mode
from intersuit.model.streaming_av.builder import build_streaming_av_module
from intersuit.model.streaming_av.dynamic_window_selector import (
    DynamicWindowSelector,
    LegacyMultiscaleDynamicWindowSelector,
)


def _inputs(steps: int = 6, dim: int = 4):
    features = torch.arange(steps * dim, dtype=torch.float32).reshape(1, steps, dim)
    timestamps = torch.tensor(
        [[float(index), float(index + 1)] for index in range(steps)],
        dtype=torch.float32,
    )
    windows = torch.zeros(1, steps, 32)
    mask = torch.ones(1, steps, dtype=torch.bool)
    return features, timestamps, windows, mask


def test_micro_window_selection_never_pools_or_changes_source_timestamps():
    features, timestamps, windows, mask = _inputs()
    windows[0, 1] = 0.2
    windows[0, 4] = 0.1
    selector = DynamicWindowSelector(input_dim=4, top_k=3)

    output = selector(features, timestamps, mask, windows)

    selected_indices = output.selected_source_indices[0, output.selection_mask[0]]
    assert torch.all(selected_indices[1:] >= selected_indices[:-1])
    for slot, source_index in enumerate(selected_indices.tolist()):
        assert torch.equal(output.selected_features[0, slot], features[0, source_index])
        assert torch.equal(output.selected_timestamps[0, slot], timestamps[source_index])
        assert output.source_weights[0, slot, source_index] == 1
        assert output.source_weights[0, slot].sum() == 1


def test_micro_window_topk_uses_earlier_tie_break_and_k_greater_than_n_selects_all():
    features, timestamps, windows, mask = _inputs(steps=4)
    selector_k1 = DynamicWindowSelector(input_dim=4, top_k=1)
    selector_all = DynamicWindowSelector(input_dim=4, top_k=9)

    first = selector_k1(torch.zeros_like(features), timestamps, mask, windows)
    all_windows = selector_all(features, timestamps, mask, windows)

    assert first.selected_source_indices[0, 0].item() == 0
    assert torch.equal(first.eventness_cached_scores, torch.zeros_like(first.eventness_cached_scores))
    assert all_windows.selection_mask.sum().item() == 4
    assert torch.equal(all_windows.selected_source_indices[0, :4], torch.arange(4))
    assert torch.equal(all_windows.selected_features[0, :4], features[0])
    assert torch.equal(all_windows.selected_timestamps[0, :4], timestamps)


def test_micro_window_rejects_zero_k_long_windows_and_excludes_padding():
    with pytest.raises(ValueError, match="positive"):
        DynamicWindowSelector(input_dim=4, top_k=0)

    features, timestamps, windows, mask = _inputs()
    mask[0, -2:] = False
    selector = DynamicWindowSelector(input_dim=4, top_k=9)
    output = selector(features, timestamps, mask, windows)
    assert output.selection_mask.sum().item() == 4
    assert torch.all(output.selected_source_indices[output.selection_mask] < 4)

    long_timestamps = timestamps.clone()
    long_timestamps[0, 1] = 2.0
    with pytest.raises(ValueError, match="no longer than 1 seconds"):
        selector(features, long_timestamps, mask, windows)


def test_window_modes_are_explicit_and_builder_keeps_legacy_path_separate():
    assert normalize_scene_audio_window_mode("micro_window") == "micro_window"
    assert normalize_scene_audio_window_mode("legacy_multiscale_dynamic") == "legacy_multiscale_dynamic"
    assert normalize_scene_audio_window_mode("fixed") == "fixed"
    with pytest.raises(ValueError, match="ambiguous"):
        normalize_scene_audio_window_mode("dynamic")

    config = SimpleNamespace(hidden_size=4)
    module = build_streaming_av_module(config)
    assert isinstance(module.dynamic_window_selector, DynamicWindowSelector)
    assert isinstance(
        module.legacy_multiscale_dynamic_window_selector,
        LegacyMultiscaleDynamicWindowSelector,
    )
    assert type(module.dynamic_window_selector) is not type(
        module.legacy_multiscale_dynamic_window_selector
    )
