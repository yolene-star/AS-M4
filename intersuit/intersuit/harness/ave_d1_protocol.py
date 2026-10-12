"""AVE D1 validation 的固定 1 秒窗口、策略排名与 localization 指标协议。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from typing import Iterable, Sequence

import torch

from intersuit.harness.dynamic_selector_contract import FIXED_CONTROL_SEEDS


CLIP_DURATION_SEC = 10.0
MICRO_WINDOW_SEC = 1.0
WINDOW_COUNT = 10
MAIN_BUDGET = 4
SUPPLEMENTARY_BUDGETS = (2, 8)
D1_BUDGETS = (2, 4, 8)
ONSET_FRAME_SEC = 0.025
ONSET_HOP_SEC = 0.010
BOOTSTRAP_SEED = 20260724
BOOTSTRAP_RESAMPLES = 10_000


@dataclass(frozen=True)
class SelectionEvaluation:
    strategy: str
    budget: int
    valid_window_count: int
    selected_ranked_indices: list[int]
    selected_time_order_indices: list[int]
    selected_timestamps: list[list[float]]
    window_hit: float
    positive_window_recall: float
    first_hit_rank: int | None
    max_tiou: float
    compression_ratio: float


def build_micro_window_timestamps() -> torch.Tensor:
    """Return the frozen ten original one-second AVE windows."""

    return torch.tensor(
        [[float(index), float(index + 1)] for index in range(WINDOW_COUNT)],
        dtype=torch.float32,
    )


def normalize_target_intervals(annotations: Iterable[dict]) -> list[tuple[float, float]]:
    """Validate, clip, sort, and union physical-video target intervals."""

    intervals = []
    for annotation in annotations:
        start = float(annotation["event_start_sec"])
        end = float(annotation["event_end_sec"])
        if not math.isfinite(start) or not math.isfinite(end) or end <= start:
            raise ValueError("AVE target interval must have finite positive duration")
        start = max(0.0, min(CLIP_DURATION_SEC, start))
        end = max(0.0, min(CLIP_DURATION_SEC, end))
        if end <= start:
            raise ValueError("AVE target interval is empty after clipping")
        intervals.append((start, end))
    if not intervals:
        raise ValueError("at least one AVE target interval is required")
    intervals.sort()
    merged: list[list[float]] = []
    for start, end in intervals:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(start, end) for start, end in merged]


def ranked_score_indices(
    scores: torch.Tensor,
    timestamps: torch.Tensor,
    valid_mask: torch.Tensor | None = None,
) -> list[int]:
    """Rank high score first with earlier timestamp/source-index tie breaks."""

    if scores.ndim != 1:
        raise ValueError("scores must be one-dimensional")
    if timestamps.shape != (scores.numel(), 2):
        raise ValueError("timestamps must have shape [N, 2]")
    mask = (
        torch.ones(scores.numel(), dtype=torch.bool)
        if valid_mask is None
        else valid_mask.to(dtype=torch.bool)
    )
    if mask.shape != scores.shape:
        raise ValueError("valid_mask must have shape [N]")
    if not torch.isfinite(scores).all() or not torch.isfinite(timestamps).all():
        raise ValueError("scores and timestamps must be finite")
    rows = [
        (
            -float(scores[index].item()),
            float(timestamps[index, 0].item()),
            float(timestamps[index, 1].item()),
            index,
        )
        for index in range(scores.numel())
        if bool(mask[index].item())
    ]
    rows.sort()
    return [row[3] for row in rows]


def select_from_ranking(ranked_indices: Sequence[int], k: int) -> list[int]:
    if k <= 0:
        raise ValueError("k must be positive")
    values = [int(index) for index in ranked_indices]
    if len(values) != len(set(values)):
        raise ValueError("ranked_indices must be unique")
    return values[: min(k, len(values))]


def uniform_topk_indices(valid_indices: Sequence[int], k: int) -> list[int]:
    """Select centers of K equal temporal bins and return temporal order."""

    if k <= 0:
        raise ValueError("k must be positive")
    indices = sorted(int(index) for index in valid_indices)
    if len(indices) != len(set(indices)):
        raise ValueError("valid_indices must be unique")
    count = len(indices)
    selected_count = min(k, count)
    if selected_count == 0:
        return []
    positions = [
        min(count - 1, int((slot + 0.5) * count / selected_count))
        for slot in range(selected_count)
    ]
    if len(positions) != len(set(positions)):
        raise AssertionError("uniform bin centers unexpectedly collided")
    return [indices[position] for position in positions]


def oracle_ranked_indices(
    timestamps: torch.Tensor,
    target_intervals: Sequence[tuple[float, float]],
    valid_mask: torch.Tensor | None = None,
) -> list[int]:
    """Rank by union-overlap duration, then nearest target and earlier time."""

    if timestamps.ndim != 2 or timestamps.shape[1] != 2:
        raise ValueError("timestamps must have shape [N, 2]")
    mask = (
        torch.ones(timestamps.shape[0], dtype=torch.bool)
        if valid_mask is None
        else valid_mask.to(dtype=torch.bool)
    )
    if mask.shape != timestamps.shape[:1]:
        raise ValueError("valid_mask must have shape [N]")
    rows = []
    for index, (start_tensor, end_tensor) in enumerate(timestamps):
        if not bool(mask[index].item()):
            continue
        start = float(start_tensor.item())
        end = float(end_tensor.item())
        overlap = sum(
            max(0.0, min(end, target_end) - max(start, target_start))
            for target_start, target_end in target_intervals
        )
        distance = min(
            max(target_start - end, start - target_end, 0.0)
            for target_start, target_end in target_intervals
        )
        rows.append((-overlap, distance, start, end, index))
    rows.sort()
    return [row[4] for row in rows]


def sample_specific_random_ranking(
    valid_indices: Sequence[int],
    *,
    sample_id: str,
    seed: int,
) -> list[int]:
    """Return a per-video deterministic permutation derived from a fixed seed."""

    indices = [int(index) for index in valid_indices]
    if len(indices) != len(set(indices)):
        raise ValueError("valid_indices must be unique")
    digest = hashlib.sha256(f"{int(seed)}:{sample_id}".encode("utf-8")).digest()
    derived_seed = int.from_bytes(digest[:8], "big") % (2**63 - 1)
    generator = torch.Generator(device="cpu")
    generator.manual_seed(derived_seed)
    order = torch.randperm(len(indices), generator=generator).tolist()
    return [indices[position] for position in order]


def build_five_seed_sample_random_rankings(
    valid_indices: Sequence[int],
    sample_id: str,
) -> dict[str, list[int]]:
    return {
        str(seed): sample_specific_random_ranking(
            valid_indices,
            sample_id=sample_id,
            seed=seed,
        )
        for seed in FIXED_CONTROL_SEEDS
    }


def waveform_energy_onset_scores(
    waveform: torch.Tensor,
    sample_rate: int,
) -> tuple[torch.Tensor, torch.Tensor, dict]:
    """Compute frozen RMS energy and positive RMS-flux onset scores."""

    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    value = waveform.detach().cpu().float()
    if value.ndim != 1 or not torch.isfinite(value).all():
        raise ValueError("waveform must be a finite mono one-dimensional tensor")
    expected_samples = int(round(CLIP_DURATION_SEC * sample_rate))
    original_samples = value.numel()
    if original_samples < expected_samples:
        value = torch.nn.functional.pad(value, (0, expected_samples - original_samples))
    else:
        value = value[:expected_samples]
    samples_per_window = int(round(MICRO_WINDOW_SEC * sample_rate))
    windows = value.reshape(WINDOW_COUNT, samples_per_window)
    energy = windows.square().mean(dim=1).sqrt()

    frame_length = max(1, int(round(ONSET_FRAME_SEC * sample_rate)))
    hop_length = max(1, int(round(ONSET_HOP_SEC * sample_rate)))
    frames = value.unfold(0, frame_length, hop_length)
    envelope = frames.square().mean(dim=1).sqrt()
    positive_flux = torch.relu(envelope[1:] - envelope[:-1])
    onset = torch.zeros(WINDOW_COUNT, dtype=torch.float32)
    flux_centers = (
        torch.arange(1, envelope.numel(), dtype=torch.float64) * hop_length
        + frame_length / 2.0
    )
    window_indices = torch.clamp(
        torch.floor(flux_centers / sample_rate).long(),
        min=0,
        max=WINDOW_COUNT - 1,
    )
    onset.scatter_add_(0, window_indices, positive_flux)
    if not torch.isfinite(energy).all() or not torch.isfinite(onset).all():
        raise ValueError("waveform scores contain NaN/Inf")
    return energy, onset, {
        "original_sample_count": original_samples,
        "normalized_sample_count": expected_samples,
        "padded_sample_count": max(0, expected_samples - original_samples),
        "truncated_sample_count": max(0, original_samples - expected_samples),
        "sample_rate": sample_rate,
        "onset_frame_samples": frame_length,
        "onset_hop_samples": hop_length,
    }


def interval_overlap(
    window: tuple[float, float],
    target: tuple[float, float],
) -> float:
    return max(0.0, min(window[1], target[1]) - max(window[0], target[0]))


def interval_tiou(
    window: tuple[float, float],
    target: tuple[float, float],
) -> float:
    overlap = interval_overlap(window, target)
    union = (window[1] - window[0]) + (target[1] - target[0]) - overlap
    return overlap / union if union > 0 else 0.0


def evaluate_selection(
    *,
    strategy: str,
    budget: int,
    ranked_selected_indices: Sequence[int],
    timestamps: torch.Tensor,
    target_intervals: Sequence[tuple[float, float]],
    valid_mask: torch.Tensor | None = None,
) -> SelectionEvaluation:
    """Evaluate one fixed-K physical-video selection without changing rank."""

    if budget <= 0:
        raise ValueError("budget must be positive")
    mask = (
        torch.ones(timestamps.shape[0], dtype=torch.bool)
        if valid_mask is None
        else valid_mask.to(dtype=torch.bool)
    )
    valid_indices = [index for index in range(mask.numel()) if bool(mask[index].item())]
    selected = select_from_ranking(ranked_selected_indices, min(budget, len(valid_indices)))
    expected_selected_count = min(budget, len(valid_indices))
    if len(selected) != expected_selected_count:
        raise ValueError(
            "selection must contain exactly min(K, N_valid) source indices"
        )
    if any(index not in valid_indices for index in selected):
        raise ValueError("selection contains invalid source index")
    windows = {
        index: (
            float(timestamps[index, 0].item()),
            float(timestamps[index, 1].item()),
        )
        for index in valid_indices
    }
    positive = {
        index
        for index, window in windows.items()
        if any(interval_overlap(window, target) > 0 for target in target_intervals)
    }
    if not positive:
        raise ValueError("target intervals do not overlap any valid micro-window")
    hit_positions = [
        rank
        for rank, index in enumerate(selected, start=1)
        if index in positive
    ]
    max_tiou = max(
        (
            interval_tiou(windows[index], target)
            for index in selected
            for target in target_intervals
        ),
        default=0.0,
    )
    selected_time_order = sorted(selected, key=lambda index: (windows[index][0], index))
    return SelectionEvaluation(
        strategy=strategy,
        budget=budget,
        valid_window_count=len(valid_indices),
        selected_ranked_indices=selected,
        selected_time_order_indices=selected_time_order,
        selected_timestamps=[list(windows[index]) for index in selected_time_order],
        window_hit=1.0 if hit_positions else 0.0,
        positive_window_recall=len(set(selected) & positive) / len(positive),
        first_hit_rank=min(hit_positions) if hit_positions else None,
        max_tiou=max_tiou,
        compression_ratio=len(selected) / len(valid_indices),
    )


def aggregate_evaluations(evaluations: Sequence[SelectionEvaluation]) -> dict:
    if not evaluations:
        raise ValueError("evaluations must not be empty")
    first_ranks = [
        value.first_hit_rank
        for value in evaluations
        if value.first_hit_rank is not None
    ]
    return {
        "sample_count": len(evaluations),
        "window_hit_at_k": sum(value.window_hit for value in evaluations)
        / len(evaluations),
        "positive_window_recall_at_k": sum(
            value.positive_window_recall for value in evaluations
        )
        / len(evaluations),
        "mean_first_hit_rank": (
            sum(first_ranks) / len(first_ranks) if first_ranks else None
        ),
        "first_hit_missing_count": len(evaluations) - len(first_ranks),
        "mean_max_tiou": sum(value.max_tiou for value in evaluations)
        / len(evaluations),
        "mean_compression_ratio": sum(
            value.compression_ratio for value in evaluations
        )
        / len(evaluations),
    }


def paired_bootstrap_mean_difference(
    first: Sequence[float],
    second: Sequence[float],
    *,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> dict:
    if len(first) != len(second) or not first:
        raise ValueError("paired inputs must have the same positive length")
    if resamples <= 0:
        raise ValueError("resamples must be positive")
    differences = torch.tensor(first, dtype=torch.float64) - torch.tensor(
        second, dtype=torch.float64
    )
    generator = torch.Generator(device="cpu")
    generator.manual_seed(int(seed))
    indexes = torch.randint(
        0,
        differences.numel(),
        (resamples, differences.numel()),
        generator=generator,
    )
    means = differences[indexes].mean(dim=1)
    return {
        "paired_sample_count": differences.numel(),
        "mean_difference": float(differences.mean().item()),
        "ci95_lower": float(torch.quantile(means, 0.025).item()),
        "ci95_upper": float(torch.quantile(means, 0.975).item()),
        "resamples": resamples,
        "seed": int(seed),
    }
