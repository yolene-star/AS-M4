"""正式原始 micro-window selector 的 D0 CPU contract 测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import torch
from torch import nn

from intersuit.harness.dynamic_selector_contract import module_state_sha256
from intersuit.model.llava_arch import LlavaMetaForCausalLM, normalize_scene_audio_window_mode
from intersuit.model.scene_audio_encoder.scene_audio_encoder import DummySceneAudioEncoder
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
    source_indices = torch.tensor([[10, 11, 12, 13, -1, -1]])
    output = selector(
        features,
        timestamps,
        mask,
        windows,
        audio_source_indices=source_indices,
    )
    assert output.selection_mask.sum().item() == 4
    assert output.selected_source_indices[output.selection_mask].tolist() == [10, 11, 12, 13]

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


def test_all_padding_returns_empty_finite_output():
    features, timestamps, windows, mask = _inputs()
    mask.zero_()
    output = DynamicWindowSelector(input_dim=4, top_k=3)(
        features,
        timestamps,
        mask,
        windows,
    )

    assert not output.selection_mask.any()
    assert torch.all(output.selected_source_indices == -1)
    assert torch.isfinite(output.selected_features).all()
    assert torch.isfinite(output.eventness_cached_scores).all()
    assert torch.isfinite(output.dynamic_thresholds).all()


def test_strict_causal_replay_preserves_absolute_indices_and_ignores_future_perturbation():
    selector = DynamicWindowSelector(input_dim=4, top_k=1)
    timestamps = torch.tensor([[float(index), float(index + 1)] for index in range(6)])
    prefix = torch.arange(12, dtype=torch.float32).reshape(1, 3, 4)
    suffix_a = torch.zeros(1, 3, 4)
    suffix_b = torch.full((1, 3, 4), 999.0)
    windows = torch.ones(1, 6, 16) * 0.1

    def replay(features):
        state = None
        snapshots = []
        for index in range(features.shape[1]):
            output = selector(
                features[:, index : index + 1],
                timestamps[index : index + 1],
                torch.ones(1, 1, dtype=torch.bool),
                windows[:, index : index + 1],
                state=state,
                audio_source_indices=torch.tensor([[100 + index]]),
            )
            state = output.state
            snapshots.append(
                (
                    output.selected_features.detach().clone(),
                    output.selected_timestamps.detach().clone(),
                    output.selected_source_indices.detach().clone(),
                    output.eventness_cached_scores.detach().clone(),
                    output.dynamic_thresholds.detach().clone(),
                )
            )
        return snapshots

    replay_a = replay(torch.cat([prefix, suffix_a], dim=1))
    replay_b = replay(torch.cat([prefix, suffix_b], dim=1))

    for index in range(3):
        assert replay_a[index][2].item() == 100 + index
        for value_a, value_b in zip(replay_a[index], replay_b[index]):
            assert torch.equal(value_a, value_b)


class _DisabledSceneAudioRoutingModel(LlavaMetaForCausalLM):
    def __init__(self):
        self.config = SimpleNamespace(enable_scene_audio=False, scene_audio_window_mode="fixed")
        self.fallback_calls = 0

    def get_model(self):
        return self

    def prepare_inputs_labels_for_multimodal(self, *args, **kwargs):
        self.fallback_calls += 1
        return "fixed-fallback"


def test_enable_scene_audio_zero_uses_existing_fixed_fallback():
    model = _DisabledSceneAudioRoutingModel()

    result = model.prepare_inputs_labels_for_streaming_av(
        input_ids=torch.ones(1, 2, dtype=torch.long),
        position_ids=None,
        attention_mask=None,
        past_key_values=None,
        labels=None,
        images=None,
        scene_audios=torch.zeros(1, 2, 16),
    )

    assert result == "fixed-fallback"
    assert model.fallback_calls == 1


class _SceneAudioEncodingModel(LlavaMetaForCausalLM):
    def __init__(self):
        self.config = SimpleNamespace(
            hidden_size=4,
            scene_audio_window_mode="fixed",
            dynamic_window_top_k=4,
            dynamic_window_micro_window_sec=1.0,
        )
        self.scene_audio_encoder = DummySceneAudioEncoder(hidden_size=4)
        self.streaming_av_module = build_streaming_av_module(self.config)

    def get_model(self):
        return self

    def get_scene_audio_encoder(self):
        return self.scene_audio_encoder

    def get_streaming_av_module(self):
        return self.streaming_av_module


def test_all_micro_windows_exactly_match_production_fixed_encoder_path_and_parameter_sha():
    model = _SceneAudioEncodingModel()
    windows = torch.arange(4 * 32, dtype=torch.float32).reshape(1, 4, 32) / 100.0
    mask = torch.ones(1, 4, dtype=torch.bool)
    timestamps = torch.tensor([[float(index), float(index + 1)] for index in range(4)])
    tracked_modules = nn.ModuleDict(
        {
            "scene_audio_encoder": model.scene_audio_encoder,
            "streaming_av_module": model.streaming_av_module,
        }
    )
    sha_before = module_state_sha256(tracked_modules)

    with torch.inference_mode():
        fixed = model.encode_scene_audio(windows, mask, timestamps)
        model.config.scene_audio_window_mode = "micro_window"
        selected_all = model.encode_scene_audio(windows, mask, timestamps)

    assert torch.equal(selected_all.features, fixed.features)
    assert torch.equal(selected_all.mask, fixed.mask)
    assert torch.equal(selected_all.timestamps, fixed.timestamps)
    assert module_state_sha256(tracked_modules) == sha_before
