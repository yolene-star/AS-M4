"""D0 contract helpers for dynamic-window selector diagnostics.

This module is intentionally dataset-free.  It centralizes deterministic
selection, source-index/timestamp auditing, and the five fixed random/mismatch
controls required before any official-data experiment is allowed.
"""

from __future__ import annotations

import hashlib
from typing import Iterable

import torch
from torch import nn

from intersuit.model.streaming_av.dynamic_window_selector import DynamicWindowSelectorOutput


FIXED_CONTROL_SEEDS: tuple[int, ...] = (
    20260720,
    20260721,
    20260722,
    20260723,
    20260724,
)
EVENTNESS_POLICY_NAME = "eventness_cached_topk"
LEGACY_MULTISCALE_POLICY_NAME = "legacy_multiscale_dynamic"


def stable_topk_indices(
    scores: torch.Tensor,
    timestamps: torch.Tensor,
    source_indices: torch.Tensor,
    valid_mask: torch.Tensor,
    k: int,
) -> list[int]:
    """Rank valid windows by score with an explicit earlier-window tie break."""

    if k <= 0:
        raise ValueError("k must be positive")
    if scores.ndim != 1:
        raise ValueError("scores must be one-dimensional")
    if timestamps.shape != (scores.numel(), 2):
        raise ValueError("timestamps must have shape [N, 2]")
    if source_indices.shape != scores.shape or valid_mask.shape != scores.shape:
        raise ValueError("source_indices and valid_mask must have shape [N]")
    if not torch.isfinite(scores).all() or not torch.isfinite(timestamps).all():
        raise ValueError("scores and timestamps must be finite")

    rows = [
        (
            -float(scores[index].item()),
            float(timestamps[index, 0].item()),
            float(timestamps[index, 1].item()),
            int(source_indices[index].item()),
        )
        for index in range(scores.numel())
        if bool(valid_mask[index].item())
    ]
    rows.sort()
    selected = rows[: min(k, len(rows))]
    return sorted((row[3] for row in selected))


def random_topk_indices(valid_source_indices: Iterable[int], k: int, seed: int) -> list[int]:
    """Select a reproducible random subset and restore original time order."""

    if k <= 0:
        raise ValueError("k must be positive")
    indices = [int(value) for value in valid_source_indices]
    if len(indices) != len(set(indices)):
        raise ValueError("valid_source_indices must be unique")
    if not indices:
        return []
    generator = torch.Generator(device="cpu")
    generator.manual_seed(int(seed))
    order = torch.randperm(len(indices), generator=generator).tolist()
    return sorted(indices[index] for index in order[: min(k, len(indices))])


def build_five_seed_random_controls(valid_source_indices: Iterable[int], k: int) -> dict[str, list[int]]:
    """Build the preregistered five Random selections."""

    indices = tuple(int(value) for value in valid_source_indices)
    return {
        str(seed): random_topk_indices(indices, k, seed)
        for seed in FIXED_CONTROL_SEEDS
    }


def build_mismatch_mapping(sample_ids: Iterable[str], seed: int) -> dict[str, str]:
    """Build a deterministic one-to-one derangement for a mismatch control."""

    ids = [str(value) for value in sample_ids]
    if len(ids) < 2:
        raise ValueError("at least two unique sample_ids are required")
    if len(ids) != len(set(ids)):
        raise ValueError("sample_ids must be unique")

    generator = torch.Generator(device="cpu")
    generator.manual_seed(int(seed))
    shuffled = [ids[index] for index in torch.randperm(len(ids), generator=generator).tolist()]
    offset = int(torch.randint(1, len(ids), (1,), generator=generator).item())
    mapping = {
        source: shuffled[(index + offset) % len(shuffled)]
        for index, source in enumerate(shuffled)
    }
    _validate_mismatch_mapping(mapping, ids)
    return {source: mapping[source] for source in ids}


def build_five_seed_mismatch_controls(sample_ids: Iterable[str]) -> dict[str, dict[str, str]]:
    """Build the preregistered five mismatch derangements."""

    ids = tuple(str(value) for value in sample_ids)
    return {
        str(seed): build_mismatch_mapping(ids, seed)
        for seed in FIXED_CONTROL_SEEDS
    }


def oracle_topk_indices(
    timestamps: torch.Tensor,
    source_indices: torch.Tensor,
    valid_mask: torch.Tensor,
    target_start: float,
    target_end: float,
    k: int,
) -> list[int]:
    """Select overlap windows first, then the closest boundary neighbours."""

    if k <= 0:
        raise ValueError("k must be positive")
    if not target_start <= target_end:
        raise ValueError("target_start must not exceed target_end")
    if timestamps.ndim != 2 or timestamps.shape[-1] != 2:
        raise ValueError("timestamps must have shape [N, 2]")
    if source_indices.shape != timestamps.shape[:1] or valid_mask.shape != timestamps.shape[:1]:
        raise ValueError("source_indices and valid_mask must have shape [N]")

    rows = []
    for index in range(timestamps.shape[0]):
        if not bool(valid_mask[index].item()):
            continue
        start = float(timestamps[index, 0].item())
        end = float(timestamps[index, 1].item())
        overlap = max(0.0, min(end, target_end) - max(start, target_start))
        boundary_distance = max(target_start - end, start - target_end, 0.0)
        rows.append(
            (
                0 if overlap > 0 else 1,
                -overlap,
                boundary_distance,
                start,
                int(source_indices[index].item()),
            )
        )
    rows.sort()
    return sorted(row[4] for row in rows[: min(k, len(rows))])


def audit_legacy_multiscale_output(
    output: DynamicWindowSelectorOutput,
    source_timestamps: torch.Tensor,
    source_mask: torch.Tensor,
    *,
    atol: float = 1e-5,
) -> dict:
    """Audit explicit source anchors without inferring them from weights."""

    timestamps = source_timestamps
    if timestamps.ndim == 2:
        timestamps = timestamps.unsqueeze(0)
    mask = source_mask
    if mask.ndim == 1:
        mask = mask.unsqueeze(0)
    batch, steps = mask.shape
    errors: list[str] = []
    records: list[dict] = []

    if timestamps.shape != (batch, steps, 2):
        raise ValueError("source_timestamps and source_mask shapes are inconsistent")
    if output.selection_mask.shape != output.selected_source_indices.shape:
        errors.append("selection_mask 与 selected_source_indices 形状不一致")
    if output.source_weights.shape[:2] != output.selection_mask.shape:
        errors.append("source_weights 的候选维度不一致")
    if output.source_weights.shape[-1] != steps:
        errors.append("source_weights 的来源窗口维度不一致")

    finite_tensors = {
        "selected_features": output.selected_features,
        "selected_timestamps": output.selected_timestamps,
        "selection_scores": output.selection_scores,
        "source_weights": output.source_weights,
        "eventness_cached_scores": output.eventness_cached_scores,
        "dynamic_thresholds": output.dynamic_thresholds,
    }
    for name, value in finite_tensors.items():
        if not torch.isfinite(value).all():
            errors.append(f"{name} 含 NaN/Inf")

    for batch_index in range(batch):
        previous_start = None
        for slot in range(output.selection_mask.shape[1]):
            selected = bool(output.selection_mask[batch_index, slot].item())
            source_index = int(output.selected_source_indices[batch_index, slot].item())
            if not selected:
                if source_index != -1:
                    errors.append(f"batch={batch_index}, slot={slot} padding 索引不是 -1")
                continue
            if source_index < 0 or source_index >= steps:
                errors.append(f"batch={batch_index}, slot={slot} source index 越界")
                continue
            if not bool(mask[batch_index, source_index].item()):
                errors.append(f"batch={batch_index}, slot={slot} source index 指向 padding")
            selected_start = float(output.selected_timestamps[batch_index, slot, 0].item())
            selected_end = float(output.selected_timestamps[batch_index, slot, 1].item())
            anchor_end = float(timestamps[batch_index, source_index, 1].item())
            if abs(selected_end - anchor_end) > atol:
                errors.append(f"batch={batch_index}, slot={slot} 候选结束时间与 source anchor 不一致")
            if previous_start is not None and selected_start + atol < previous_start:
                errors.append(f"batch={batch_index} 选中候选未按原始时间排序")
            previous_start = selected_start
            weights = output.source_weights[batch_index, slot]
            if bool((weights[~mask[batch_index]] != 0).any().item()):
                errors.append(f"batch={batch_index}, slot={slot} padding source weight 非零")
            if abs(float(weights.sum().item()) - 1.0) > atol:
                errors.append(f"batch={batch_index}, slot={slot} source weight 未归一化")
            records.append(
                {
                    "batch_index": batch_index,
                    "slot": slot,
                    "selected_source_index": source_index,
                    "selected_timestamp": [selected_start, selected_end],
                    "anchor_timestamp": timestamps[batch_index, source_index].tolist(),
                }
            )

    return {
        "status": "pass" if not errors else "fail",
        "policy_name": LEGACY_MULTISCALE_POLICY_NAME,
        "score_name": EVENTNESS_POLICY_NAME,
        "errors": errors,
        "selected_records": records,
    }


def module_state_sha256(module: nn.Module) -> str:
    """Hash parameter and buffer bytes in a stable name order."""

    digest = hashlib.sha256()
    for name, value in sorted(module.state_dict().items()):
        tensor = value.detach().cpu().contiguous()
        digest.update(name.encode("utf-8"))
        digest.update(str(tensor.dtype).encode("ascii"))
        digest.update(str(tuple(tensor.shape)).encode("ascii"))
        digest.update(tensor.view(torch.uint8).numpy().tobytes())
    return digest.hexdigest()


def _validate_mismatch_mapping(mapping: dict[str, str], sample_ids: list[str]) -> None:
    if set(mapping) != set(sample_ids):
        raise ValueError("mismatch mapping sources do not match sample_ids")
    if set(mapping.values()) != set(sample_ids):
        raise ValueError("mismatch mapping must be one-to-one")
    if any(source == target for source, target in mapping.items()):
        raise ValueError("mismatch mapping must not pair a sample with itself")
