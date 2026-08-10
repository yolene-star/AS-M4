import sys
from pathlib import Path

import torch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from diagnose_omnimmi_pt import (  # noqa: E402
    build_prompt,
    build_pt_query,
    configure_audio_mode,
    configure_beats_chunk_size,
    score_logits,
    select_frame_indices,
    streaming_av_diagnostics,
    window_waveform,
)
from summarize_omnimmi_pt_scores import score_one  # noqa: E402


def test_query_and_prompt_match_public_adapter_shape():
    sample = {
        "conversation": [
            {"human": "old", "assistant": "answer"},
            {"human": "noise"},
        ]
    }
    query = build_pt_query(sample)
    assert query == "human\nold\nassistant\nanswer\nhuman\nnoise\n"
    prompt = build_prompt(query)
    assert prompt.startswith("<|im_start|>system\nYou are a helpful assistant.")
    assert prompt.endswith("<|im_start|>assistant\n")


def test_fixed_and_fps_frame_indices():
    assert select_frame_indices(100, 25.0, "fixed", 32)[-1] == 99
    assert len(select_frame_indices(100, 25.0, "fixed", 32)) == 32
    assert len(select_frame_indices(100, 25.0, "fps1", 32)) == 4
    assert len(select_frame_indices(1000, 25.0, "fps1_max", 32)) == 32


def test_beta_changes_decision_without_changing_probability():
    logits = torch.zeros(4)
    logits[1] = 1.0
    low = score_logits(logits, temperature=1.0, beta=1e-6, token_ids=(1,))
    high = score_logits(logits, temperature=1.0, beta=10.0, token_ids=(1,))
    assert low["tokens"]["1"]["probability"] == high["tokens"]["1"]["probability"]
    assert low["tokens"]["1"]["silent"] is True
    assert high["tokens"]["1"]["silent"] is False


def test_offline_score_matches_diagnostic_score():
    logits = torch.tensor([0.0, 1.0, -1.0])
    diagnostic = score_logits(logits, 0.5, 0.2, token_ids=(1,))
    offline = score_one(logits, 0.5, 0.2, token_id=1)
    assert offline["probability"] == diagnostic["tokens"]["1"]["probability"]
    assert offline["threshold"] == diagnostic["threshold"]
    assert offline["silent"] == diagnostic["tokens"]["1"]["silent"]


def test_window_waveform_pads_short_audio_and_keeps_timestamps():
    waveform = torch.arange(6, dtype=torch.float32)
    windows, timestamps = window_waveform(
        waveform,
        sample_rate=4,
        window_sec=1.0,
        hop_sec=0.5,
    )
    assert windows.shape == (2, 4)
    assert torch.equal(windows[0], torch.tensor([0.0, 1.0, 2.0, 3.0]))
    assert torch.equal(windows[1], torch.tensor([2.0, 3.0, 4.0, 5.0]))
    assert torch.equal(
        timestamps,
        torch.tensor([[0.0, 1.0], [0.5, 1.5]]),
    )


def test_audio_modes_set_exact_rollback_contract():
    class Config:
        pass

    class Model:
        config = Config()

    model = Model()
    configure_audio_mode(model, "behavior_off", 0.005)
    assert model.config.enable_scene_audio is False
    assert model.config.force_audio_gate is None
    configure_audio_mode(model, "gate0", 0.005)
    assert model.config.enable_scene_audio is True
    assert model.config.force_audio_gate == 0.0
    configure_audio_mode(model, "correct", 0.005)
    assert model.config.enable_scene_audio is True
    assert model.config.force_audio_gate is None
    assert model.config.as_m4_inference_simple_audio_gate == 0.005


def test_streaming_diagnostics_falls_back_to_inner_model():
    expected = [{"gate_mean": 0.005}]

    class Inner:
        _last_streaming_av_diagnostics = expected

    class Outer:
        def get_model(self):
            return Inner()

    assert streaming_av_diagnostics(Outer()) == expected


def test_configure_beats_chunk_size_updates_inner_encoder():
    class Encoder:
        encode_chunk_size = 0

    class Inner:
        def __init__(self):
            self.encoder = Encoder()

        def get_scene_audio_encoder(self):
            return self.encoder

    class Config:
        pass

    class Outer:
        config = Config()

        def __init__(self):
            self.inner = Inner()

        def get_model(self):
            return self.inner

    model = Outer()
    configure_beats_chunk_size(model, 64)
    assert model.inner.encoder.encode_chunk_size == 64
    assert model.config.scene_audio_beats_encode_chunk_size == 64
