"""Deterministic waveform heuristics for Dynamic Selector V2 S1."""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np


def energy_scores(
    waveform: np.ndarray,
    sample_rate: int,
    timestamps: Sequence[Sequence[float]],
) -> list[float]:
    waveform, sample_rate = _validate_waveform(waveform, sample_rate)
    scores = []
    for start, end in timestamps:
        left, right = _sample_bounds(start, end, sample_rate, len(waveform))
        segment = waveform[left:right]
        scores.append(float(np.log(np.mean(np.square(segment, dtype=np.float64)) + 1e-12)))
    return scores


def onset_scores(
    waveform: np.ndarray,
    sample_rate: int,
    timestamps: Sequence[Sequence[float]],
    *,
    frame_length: int = 400,
    frame_hop: int = 160,
) -> list[float]:
    waveform, sample_rate = _validate_waveform(waveform, sample_rate)
    if frame_length <= 0 or frame_hop <= 0:
        raise ValueError("frame_length and frame_hop must be positive")
    starts = np.arange(0, max(len(waveform), 1), frame_hop, dtype=np.int64)
    padded = np.pad(waveform, (0, frame_length), mode="constant")
    frames = np.stack([padded[start : start + frame_length] for start in starts])
    magnitudes = np.log1p(np.abs(np.fft.rfft(frames * np.hanning(frame_length), axis=1)))
    flux = np.zeros(len(frames), dtype=np.float64)
    if len(frames) > 1:
        flux[1:] = np.maximum(magnitudes[1:] - magnitudes[:-1], 0.0).mean(axis=1)
    centers = (starts + frame_length / 2.0) / sample_rate
    scores = []
    for start, end in timestamps:
        mask = (centers >= float(start)) & (centers < float(end))
        scores.append(float(flux[mask].mean()) if np.any(mask) else 0.0)
    return scores


def topk_from_scores(scores: Sequence[float], k: int) -> list[int]:
    if not scores:
        raise ValueError("scores must not be empty")
    if k <= 0:
        raise ValueError("k must be positive")
    values = [float(score) for score in scores]
    if not all(math.isfinite(score) for score in values):
        raise ValueError("scores must be finite")
    return sorted(range(len(values)), key=lambda index: (-values[index], index))[: min(k, len(values))]


def _validate_waveform(waveform: np.ndarray, sample_rate: int) -> tuple[np.ndarray, int]:
    values = np.asarray(waveform, dtype=np.float32)
    if values.ndim != 1 or not len(values):
        raise ValueError("waveform must be a non-empty mono vector")
    if not np.isfinite(values).all():
        raise ValueError("waveform must be finite")
    sample_rate = int(sample_rate)
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    return values, sample_rate


def _sample_bounds(start: float, end: float, sample_rate: int, sample_count: int) -> tuple[int, int]:
    start, end = float(start), float(end)
    if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end):
        raise ValueError(f"invalid timestamp: {(start, end)}")
    left = min(int(round(start * sample_rate)), sample_count - 1)
    right = min(max(int(round(end * sample_rate)), left + 1), sample_count)
    return left, right
