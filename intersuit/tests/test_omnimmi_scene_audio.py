from __future__ import annotations

import torch
import pytest

from intersuit.harness.omnimmi_scene_audio import (
    frame_audio_windows,
    project_audio_signal_features,
)
from intersuit.model.streaming_av.confidence_gate import AudioSignalFeatures


def test_frame_audio_windows_preserve_frame_order_and_pad_tail():
    waveform = torch.arange(10, dtype=torch.float32)
    windows, timestamps = frame_audio_windows(
        waveform,
        [0, 2],
        frame_fps=2.0,
        sample_rate=4,
        window_sec=1.0,
    )

    assert torch.equal(windows[0], torch.tensor([0.0, 1.0, 2.0, 3.0]))
    assert torch.equal(windows[1], torch.tensor([4.0, 5.0, 6.0, 7.0]))
    assert torch.equal(timestamps, torch.tensor([[0.0, 1.0], [1.0, 2.0]]))


def test_frame_audio_windows_zero_pad_beyond_waveform_end():
    waveform = torch.tensor([1.0, 2.0, 3.0])
    windows, _ = frame_audio_windows(
        waveform,
        [1],
        frame_fps=1.0,
        sample_rate=2,
        window_sec=1.0,
    )

    assert torch.equal(windows, torch.tensor([[3.0, 0.0]]))


def test_project_audio_signal_features_pads_streaming_selector_slots():
    signal = AudioSignalFeatures(
        rms=torch.tensor([[0.25]]),
        loudness_dbfs=torch.tensor([[-12.0]]),
        silence_ratio=torch.tensor([[0.1]]),
        norm=torch.tensor([[4.0]]),
    )
    weights = torch.tensor([[[1.0], [0.0], [0.0]]])

    projected = project_audio_signal_features(signal, weights)

    assert torch.equal(projected.rms, torch.tensor([[0.25, 0.0, 0.0]]))
    assert torch.equal(projected.norm, torch.tensor([[4.0, 0.0, 0.0]]))
    assert projected.loudness_dbfs.shape == (1, 3)


def test_project_audio_signal_features_rejects_source_shape_mismatch():
    signal = AudioSignalFeatures(*(torch.ones(1, 2) for _ in range(4)))

    with pytest.raises(ValueError, match="不匹配"):
        project_audio_signal_features(signal, torch.ones(1, 3, 1))
