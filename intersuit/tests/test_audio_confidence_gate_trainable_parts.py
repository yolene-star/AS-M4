from __future__ import annotations

import pytest
import torch
from torch import nn

from intersuit.train.trainable_parts import (
    assert_audio_confidence_gate_only_optimizer,
    assert_exact_trainable_parameters,
    audio_confidence_gate_parameter_names,
    enable_audio_confidence_gate_only,
)


class _Gate(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.question_router = nn.Sequential(nn.Linear(4, 3), nn.Linear(3, 1))
        self.audio_projection = nn.Linear(4, 3)
        self.video_projection = nn.Linear(4, 3)
        self.question_projection = nn.Linear(4, 3)
        self.quality_mlp = nn.Sequential(nn.LayerNorm(7), nn.Linear(7, 1))
        self.relevance_mlp = nn.Sequential(nn.LayerNorm(11), nn.Linear(11, 1))


class _Streaming(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.first_stage_confidence_gate = _Gate()
        self.confidence_gate = nn.Linear(4, 1)
        self.fusion = nn.Linear(4, 4)


class _Body(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.streaming_av_module = _Streaming()
        self.scene_audio_encoder = nn.Linear(4, 4)


class _Model(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.model = _Body()
        self.lm_head = nn.Linear(4, 10)


def test_gate_only_allowlist_freezes_backbones_legacy_gate_and_fusion() -> None:
    model = _Model()
    allowed = enable_audio_confidence_gate_only(model)

    assert allowed == audio_confidence_gate_parameter_names(model)
    assert allowed
    assert all("first_stage_confidence_gate" in name for name in allowed)
    assert tuple(sorted(name for name, parameter in model.named_parameters() if parameter.requires_grad)) == allowed
    assert not any(
        parameter.requires_grad
        for name, parameter in model.named_parameters()
        if "first_stage_confidence_gate" not in name
    )


def test_gate_only_optimizer_and_step_cannot_mutate_frozen_parameters() -> None:
    model = _Model()
    allowed = enable_audio_confidence_gate_only(model)
    frozen_before = {
        name: parameter.detach().clone()
        for name, parameter in model.named_parameters()
        if name not in allowed
    }
    optimizer = torch.optim.AdamW(
        (parameter for parameter in model.parameters() if parameter.requires_grad), lr=1e-3
    )
    assert assert_audio_confidence_gate_only_optimizer(optimizer, model) == allowed

    loss = sum(parameter.square().sum() for parameter in model.parameters() if parameter.requires_grad)
    loss.backward()
    optimizer.step()

    for name, parameter in model.named_parameters():
        if name in frozen_before:
            assert parameter.grad is None
            assert torch.equal(parameter.detach(), frozen_before[name])


def test_gate_only_audits_detect_missing_or_extra_parameters() -> None:
    model = _Model()
    allowed = enable_audio_confidence_gate_only(model)
    model.lm_head.weight.requires_grad_(True)
    optimizer = torch.optim.SGD(
        (parameter for parameter in model.parameters() if parameter.requires_grad), lr=0.1
    )
    with pytest.raises(RuntimeError, match="Gate-only optimizer"):
        assert_audio_confidence_gate_only_optimizer(optimizer, model)
    with pytest.raises(RuntimeError, match="allowlist"):
        assert_exact_trainable_parameters(model, allowed)

    incomplete = _Model()
    del incomplete.model.streaming_av_module.first_stage_confidence_gate.relevance_mlp
    with pytest.raises(RuntimeError, match="Gate-only 参数解析失败"):
        audio_confidence_gate_parameter_names(incomplete)
