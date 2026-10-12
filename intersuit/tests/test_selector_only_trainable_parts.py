from __future__ import annotations

import pytest
import torch
from torch import nn

from intersuit.train.trainable_parts import (
    assert_exact_trainable_parameters,
    assert_selector_only_optimizer,
    enable_scene_audio_selector_only,
    scene_audio_selector_parameter_names,
)


class _Selector(nn.Module):
    def __init__(self):
        super().__init__()
        self.audio_projection = nn.Linear(4, 3, bias=False)
        self.question_projection = nn.Linear(5, 3, bias=False)


class _Streaming(nn.Module):
    def __init__(self):
        super().__init__()
        self.question_conditioned_window_selector = _Selector()
        self.fusion = nn.Linear(3, 3)


class _Body(nn.Module):
    def __init__(self):
        super().__init__()
        self.streaming_av_module = _Streaming()
        self.scene_audio_encoder = nn.Linear(4, 4)


class _Model(nn.Module):
    def __init__(self):
        super().__init__()
        self.model = _Body()
        self.lm_head = nn.Linear(4, 10)


def test_selector_only_allowlist_freezes_every_other_parameter():
    model = _Model()
    allowed = enable_scene_audio_selector_only(model)

    assert allowed == scene_audio_selector_parameter_names(model)
    assert len(allowed) == 2
    assert tuple(name for name, parameter in model.named_parameters() if parameter.requires_grad) == allowed


def test_selector_only_optimizer_audit_detects_missing_or_extra_parameters():
    model = _Model()
    allowed = enable_scene_audio_selector_only(model)
    optimizer = torch.optim.SGD((parameter for parameter in model.parameters() if parameter.requires_grad), lr=0.1)

    assert assert_selector_only_optimizer(optimizer, model) == allowed

    model.lm_head.weight.requires_grad_(True)
    bad_optimizer = torch.optim.SGD((parameter for parameter in model.parameters() if parameter.requires_grad), lr=0.1)
    with pytest.raises(RuntimeError, match="optimizer 审计失败"):
        assert_selector_only_optimizer(bad_optimizer, model)
    with pytest.raises(RuntimeError, match="allowlist 审计失败"):
        assert_exact_trainable_parameters(model, allowed)


def test_selector_only_step_cannot_change_frozen_parameters():
    model = _Model()
    allowed = enable_scene_audio_selector_only(model)
    frozen_before = {
        name: parameter.detach().clone()
        for name, parameter in model.named_parameters()
        if name not in allowed
    }
    optimizer = torch.optim.SGD((parameter for parameter in model.parameters() if parameter.requires_grad), lr=0.1)
    selector = model.model.streaming_av_module.question_conditioned_window_selector
    loss = selector.audio_projection.weight.square().sum() + selector.question_projection.weight.square().sum()
    loss.backward()
    optimizer.step()

    assert all(parameter.grad is None for name, parameter in model.named_parameters() if name not in allowed)
    for name, parameter in model.named_parameters():
        if name in frozen_before:
            assert torch.equal(parameter.detach(), frozen_before[name])


def test_selector_parameter_resolution_fails_when_checkpoint_structure_is_incomplete():
    model = _Model()
    del model.model.streaming_av_module.question_conditioned_window_selector.question_projection

    with pytest.raises(RuntimeError, match="参数解析失败"):
        scene_audio_selector_parameter_names(model)
