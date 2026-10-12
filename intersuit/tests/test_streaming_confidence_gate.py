"""CPU contracts for fusion-first causal audio confidence gating."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import importlib.util
from pathlib import Path
import sys

import pytest
import torch


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gate_module = load_module(
    "as_m4_first_stage_confidence_gate",
    ROOT / "intersuit" / "model" / "streaming_av" / "confidence_gate.py",
)
state_module = load_module(
    "as_m4_streaming_confidence_state",
    ROOT / "intersuit" / "streaming" / "confidence_state.py",
)

FirstStageAudioConfidenceGate = gate_module.FirstStageAudioConfidenceGate
compute_audio_signal_features = gate_module.compute_audio_signal_features
StreamingConfidenceStateManager = state_module.StreamingConfidenceStateManager
StreamingGateConfig = state_module.StreamingGateConfig


def build_gate() -> FirstStageAudioConfidenceGate:
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(20260829)
        return FirstStageAudioConfidenceGate(
            hidden_size=4,
            projection_dim=3,
            gate_max=0.25,
        ).eval()


def clear_signal(batch: int, steps: int):
    return compute_audio_signal_features(torch.full((batch, steps, 160), 0.1))


def test_first_stage_shapes_ranges_and_identity_sync():
    gate = build_gate()
    audio = torch.ones(2, 3, 4) * 0.5
    output = gate(audio, audio, torch.ones(2, 4), clear_signal(2, 3))

    assert len(output) == 7
    for value in output:
        assert value.shape == (2, 3)
        assert torch.isfinite(value).all()
        assert torch.all((0.0 <= value) & (value <= 1.0))
    assert torch.equal(output.sync, torch.ones_like(output.sync))
    assert torch.allclose(output.raw_gate, output.quality * output.relevance)
    assert output.gate.max().item() <= 0.25


def test_silence_closes_quality_and_gate_exactly():
    gate = build_gate()
    features = torch.ones(1, 2, 4)
    silence = compute_audio_signal_features(torch.zeros(1, 2, 160))
    output = gate(features, features, torch.ones(1, 4), silence)

    assert torch.equal(output.quality, torch.zeros_like(output.quality))
    assert torch.equal(output.gate, torch.zeros_like(output.gate))


def test_relevant_question_ranks_above_opposite_question():
    gate = build_gate()
    audio = torch.tensor([[[1.0, 1.0, 1.0, 1.0]]])
    video = audio.clone()
    relevant = gate(audio, video, audio[:, 0], clear_signal(1, 1))
    opposite = gate(audio, video, -audio[:, 0], clear_signal(1, 1))

    assert relevant.window_relevance.item() > opposite.window_relevance.item()
    assert relevant.gate.item() > opposite.gate.item()


def test_future_invariance_for_pointwise_gate():
    gate = build_gate()
    torch.manual_seed(17)
    audio = torch.randn(1, 5, 4)
    video = torch.randn(1, 5, 4)
    question = torch.randn(1, 4)
    full = gate(audio, video, question, clear_signal(1, 5))
    prefix = gate(audio[:, :3], video[:, :3], question, clear_signal(1, 3))

    for full_value, prefix_value in zip(full, prefix):
        assert torch.allclose(full_value[:, :3], prefix_value, atol=1e-7, rtol=0.0)


def test_mask_and_force_gate_zero_are_exact():
    gate = build_gate()
    audio = torch.ones(1, 3, 4)
    mask = torch.tensor([[True, False, True]])
    output = gate(
        audio,
        audio,
        torch.ones(1, 4),
        clear_signal(1, 3),
        valid_mask=mask,
        force_audio_gate=0.0,
    )

    assert torch.equal(output.gate, torch.zeros_like(output.gate))
    for value in output:
        assert value[0, 1].item() == 0.0


def run_updates(manager, stream_id: str, values: list[float], start: int = 0):
    return [
        manager.update(stream_id, value, float(start + index))
        for index, value in enumerate(values)
    ]


def test_prefix_and_chunk_boundaries_are_equivalent():
    config = StreamingGateConfig(ema_beta=0.5, on_threshold=0.1, hold_threshold=0.05)
    values = [0.0, 0.4, 0.8, 0.2, 0.0]
    whole = StreamingConfidenceStateManager(config)
    chunked = StreamingConfidenceStateManager(config)
    expected = run_updates(whole, "s", values)
    actual = run_updates(chunked, "s", values[:2])
    actual += run_updates(chunked, "s", values[2:], start=2)

    assert expected == actual
    assert whole.snapshot("s") == chunked.snapshot("s")


def test_streams_are_isolated_under_interleaving():
    config = StreamingGateConfig(ema_beta=0.5)
    interleaved = StreamingConfidenceStateManager(config)
    separate_a = StreamingConfidenceStateManager(config)
    separate_b = StreamingConfidenceStateManager(config)
    a_values = [0.1, 0.8, 0.3]
    b_values = [0.9, 0.0, 0.6]
    for index, (a_value, b_value) in enumerate(zip(a_values, b_values)):
        interleaved.update("a", a_value, float(index))
        interleaved.update("b", b_value, float(index))
        separate_a.update("a", a_value, float(index))
        separate_b.update("b", b_value, float(index))

    assert interleaved.snapshot("a") == separate_a.snapshot("a")
    assert interleaved.snapshot("b") == separate_b.snapshot("b")


def test_reset_removes_all_prior_state():
    manager = StreamingConfidenceStateManager()
    manager.update("s", 0.9, 1.0)
    manager.reset("s")
    reset_update = manager.update("s", 0.2, 0.0)
    fresh = StreamingConfidenceStateManager().update("s", 0.2, 0.0)

    assert reset_update == fresh
    assert len(manager) == 1


def test_snapshot_is_immutable_and_does_not_follow_future_updates():
    manager = StreamingConfidenceStateManager()
    manager.update("s", 0.5, 1.0)
    snapshot = manager.snapshot("s", question_id="q1")
    manager.update("s", 0.9, 2.0)

    assert snapshot.memory_version == 1
    assert manager.snapshot("s").memory_version == 2
    with pytest.raises(FrozenInstanceError):
        snapshot.memory_version = 7


def test_force_close_is_exact_and_future_safe():
    manager = StreamingConfidenceStateManager()
    manager.update("s", 1.0, 0.0)
    closed = manager.update("s", 1.0, 1.0, force_close=True)
    captured = closed
    manager.update("s", 1.0, 2.0)

    assert closed.raw_gate == 0.0
    assert closed.ema_gate == 0.0
    assert closed.output_gate == 0.0
    assert closed.active is False
    assert captured == closed


def test_timestamp_regression_is_rejected_per_stream():
    manager = StreamingConfidenceStateManager()
    manager.update("s", 0.2, 2.0)
    with pytest.raises(ValueError, match="non-decreasing"):
        manager.update("s", 0.2, 1.0)


def test_invalid_config_and_stream_ids_fail_closed():
    with pytest.raises(ValueError):
        StreamingGateConfig(on_threshold=0.1, hold_threshold=0.2)
    manager = StreamingConfidenceStateManager()
    with pytest.raises(ValueError, match="stream_id"):
        manager.update("", 0.2, 0.0)
