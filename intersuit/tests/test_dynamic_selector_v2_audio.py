from __future__ import annotations

import numpy as np

from intersuit.harness.dynamic_selector_v2_audio import energy_scores, onset_scores, topk_from_scores
from intersuit.harness.dynamic_selector_v2_localization import fixed_window_timestamps


def test_energy_selects_loud_window():
    waveform = np.concatenate([np.full(100, 0.1, dtype=np.float32), np.ones(100, dtype=np.float32)])
    scores = energy_scores(waveform, 100, fixed_window_timestamps(2.0))
    assert topk_from_scores(scores, 1) == [1]


def test_onset_selects_window_with_abrupt_change():
    waveform = np.zeros(4000, dtype=np.float32)
    waveform[2500:] = 1.0
    scores = onset_scores(waveform, 1000, fixed_window_timestamps(4.0), frame_length=100, frame_hop=20)
    assert topk_from_scores(scores, 1) == [2]


def test_topk_uses_earlier_index_for_ties():
    assert topk_from_scores([1.0, 2.0, 2.0], 2) == [1, 2]
