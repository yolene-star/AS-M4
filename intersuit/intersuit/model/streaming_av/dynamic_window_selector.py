"""Legacy multiscale cached-eventness window selection for AS-M4."""

from __future__ import annotations

from typing import NamedTuple

import torch
import torch.nn.functional as F
from torch import nn


class SelectorState(NamedTuple):
    """Small causal state carried between streaming calls."""

    ema_mean: torch.Tensor
    ema_var: torch.Tensor
    last_feature: torch.Tensor
    last_rms: torch.Tensor
    event_active: torch.Tensor


class DynamicWindowSelectorOutput(NamedTuple):
    """Selected padded candidates and explicit source diagnostics.

    ``selected_source_indices`` is the original micro-window index used as the
    causal end anchor for each legacy multiscale candidate.  It is deliberately
    explicit: callers must not recover an index from floating-point timestamps
    or ``source_weights.argmax()``.
    """

    selected_features: torch.Tensor
    selected_timestamps: torch.Tensor
    selected_source_indices: torch.Tensor
    selection_scores: torch.Tensor
    selection_mask: torch.Tensor
    source_weights: torch.Tensor
    eventness_cached_scores: torch.Tensor
    dynamic_thresholds: torch.Tensor
    state: SelectorState

    @property
    def micro_scores(self) -> torch.Tensor:
        """Deprecated compatibility alias for pre-D0 callers."""

        return self.eventness_cached_scores


class LegacyMultiscaleDynamicWindowSelector(nn.Module):
    """Legacy selector based on cached feature change, energy and hysteresis.

    Candidate intervals are trailing (causal), use a small set of scales, and
    are pooled from already encoded micro-window features. The module has no
    trainable parameters.  Because feature change requires encoded audio, this
    implementation is an ``eventness_cached`` baseline and cannot be described
    as a pre-encoder or BEATs-saving selector.
    """

    def __init__(
        self,
        input_dim: int,
        scales_sec: tuple[float, ...] = (1.0, 2.0, 4.0),
        top_k: int = 16,
        nms_iou: float = 0.6,
        ema_beta: float = 0.9,
        start_scale: float = 1.0,
        hold_scale: float = 0.25,
        min_score: float = 0.05,
        rms_reference: float = 0.05,
        silence_threshold: float = 1e-4,
        causal: bool = True,
    ) -> None:
        super().__init__()
        if input_dim <= 0 or top_k <= 0:
            raise ValueError("input_dim and top_k must be positive")
        if not scales_sec or any(float(value) <= 0 for value in scales_sec):
            raise ValueError("scales_sec must contain positive durations")
        if not 0.0 < ema_beta < 1.0:
            raise ValueError("ema_beta must be in (0, 1)")
        if not 0.0 <= nms_iou <= 1.0:
            raise ValueError("nms_iou must be in [0, 1]")
        self.input_dim = int(input_dim)
        self.scales_sec = tuple(float(value) for value in scales_sec)
        self.top_k = int(top_k)
        self.nms_iou = float(nms_iou)
        self.ema_beta = float(ema_beta)
        self.start_scale = float(start_scale)
        self.hold_scale = float(hold_scale)
        self.min_score = float(min_score)
        self.rms_reference = float(rms_reference)
        self.silence_threshold = float(silence_threshold)
        self.causal = bool(causal)

    def forward(
        self,
        audio_features: torch.Tensor,
        audio_timestamps: torch.Tensor,
        audio_mask: torch.Tensor | None = None,
        audio_windows: torch.Tensor | None = None,
        state: SelectorState | None = None,
    ) -> DynamicWindowSelectorOutput:
        features = _ensure_features(audio_features, self.input_dim)
        batch, steps, _ = features.shape
        timestamps = _ensure_timestamps(audio_timestamps, batch, steps, features.device)
        mask = _ensure_mask(audio_mask, batch, steps, features.device)
        finite_features = torch.nan_to_num(features.float(), nan=0.0, posinf=0.0, neginf=0.0)
        rms, non_silent = _waveform_statistics(audio_windows, batch, steps, features.device, self.silence_threshold)
        if rms is None:
            rms = finite_features.norm(dim=-1) / (float(self.input_dim) ** 0.5)
            non_silent = (finite_features.abs().mean(dim=-1) > 0).to(torch.float32)

        previous_feature = state.last_feature if state is not None else None
        feature_change = _feature_change(finite_features, mask, previous_feature)
        energy = (rms / self.rms_reference).clamp(0.0, 1.0)
        first_rms = state.last_rms.to(device=features.device, dtype=torch.float32) if state is not None else rms[:, :1].squeeze(1)
        previous_rms = torch.cat([first_rms.unsqueeze(1), rms[:, :-1]], dim=1)
        onset = (rms - previous_rms).clamp_min(0.0)
        onset = (onset / self.rms_reference).clamp(0.0, 1.0)
        scores = (0.50 * feature_change + 0.25 * energy + 0.15 * onset + 0.10 * non_silent)
        scores = scores.masked_fill(~mask, 0.0).clamp(0.0, 1.0)

        thresholds, active, next_state = self._causal_threshold(scores, rms, finite_features, mask, state)
        return self._select_candidates(
            finite_features,
            timestamps,
            mask,
            scores,
            thresholds,
            active,
            next_state,
        )

    def _causal_threshold(self, scores, rms, features, mask, state):
        batch, steps, dim = features.shape
        device = features.device
        if state is None:
            mean = torch.zeros(batch, device=device)
            var = torch.zeros(batch, device=device)
            previous_feature = torch.zeros(batch, dim, device=device)
            previous_rms = torch.zeros(batch, device=device)
            event_active = torch.zeros(batch, dtype=torch.bool, device=device)
        else:
            mean = state.ema_mean.to(device=device, dtype=torch.float32)
            var = state.ema_var.to(device=device, dtype=torch.float32)
            previous_feature = state.last_feature.to(device=device, dtype=torch.float32)
            previous_rms = state.last_rms.to(device=device, dtype=torch.float32)
            event_active = state.event_active.to(device=device, dtype=torch.bool)

        thresholds = torch.zeros(batch, steps, device=device)
        active_values = torch.zeros(batch, steps, dtype=torch.bool, device=device)
        beta = self.ema_beta
        for index in range(steps):
            valid = mask[:, index]
            value = scores[:, index]
            mean = torch.where(valid, beta * mean + (1.0 - beta) * value, mean)
            var = torch.where(valid, beta * var + (1.0 - beta) * (value - mean).square(), var)
            std = var.clamp_min(1e-8).sqrt()
            start_threshold = (mean + self.start_scale * std).clamp(self.min_score, 1.0)
            hold_threshold = (mean + self.hold_scale * std).clamp(self.min_score * 0.5, 1.0)
            threshold = torch.where(event_active, hold_threshold, start_threshold)
            current_active = torch.where(event_active, value >= hold_threshold, value >= start_threshold)
            current_active = current_active & valid & (rms[:, index] > self.silence_threshold)
            thresholds[:, index] = threshold
            active_values[:, index] = current_active
            event_active = current_active
            previous_feature = torch.where(valid.unsqueeze(-1), features[:, index], previous_feature)
            previous_rms = torch.where(valid, rms[:, index], previous_rms)

        next_state = SelectorState(mean.detach(), var.detach(), previous_feature.detach(), previous_rms.detach(), event_active.detach())
        return thresholds, active_values, next_state

    def _select_candidates(self, features, timestamps, mask, scores, thresholds, active, state):
        batch, steps, dim = features.shape
        candidates = []
        for batch_index in range(batch):
            rows = []
            valid_indices = torch.nonzero(mask[batch_index], as_tuple=False).flatten().tolist()
            for end_index in torch.nonzero(active[batch_index], as_tuple=False).flatten().tolist():
                end_time = float(timestamps[batch_index, end_index, 1].item())
                for scale in self.scales_sec:
                    start_time = end_time - scale
                    if valid_indices:
                        first_start = float(timestamps[batch_index, valid_indices[0], 0].item())
                        start_time = max(start_time, first_start)
                    interval_start = torch.tensor(start_time, device=features.device)
                    interval_end = torch.tensor(end_time, device=features.device)
                    starts = timestamps[batch_index, :, 0]
                    ends = timestamps[batch_index, :, 1]
                    overlap = (torch.minimum(ends, interval_end) - torch.maximum(starts, interval_start)).clamp_min(0.0)
                    causal_mask = ends <= interval_end + 1e-5 if self.causal else torch.ones_like(mask[batch_index])
                    weights = overlap * mask[batch_index].to(overlap.dtype) * causal_mask.to(overlap.dtype) * scores[batch_index]
                    weight_sum = weights.sum()
                    if weight_sum.item() <= 0:
                        continue
                    pooled = (features[batch_index] * weights.unsqueeze(-1)).sum(dim=0) / weight_sum
                    mean_score = (scores[batch_index] * overlap * mask[batch_index].to(overlap.dtype)).sum() / overlap.mul(mask[batch_index]).sum().clamp_min(1e-6)
                    max_score = scores[batch_index].masked_fill(~(overlap > 0), 0.0).max()
                    covered = (overlap > 0) & mask[batch_index]
                    coverage = (covered & active[batch_index]).float().sum() / covered.float().sum().clamp_min(1.0)
                    candidate_score = (0.55 * mean_score + 0.30 * max_score + 0.15 * coverage).clamp(0.0, 1.0)
                    if candidate_score.item() < self.min_score:
                        continue
                    rows.append((candidate_score, interval_start, interval_end, pooled, end_index, weights / weight_sum))
            rows.sort(
                key=lambda row: (
                    -float(row[0].item()),
                    float(row[1].item()),
                    float(row[2].item()),
                    int(row[4]),
                )
            )
            kept = []
            for row in rows:
                if len(kept) >= self.top_k:
                    break
                if all(_interval_iou(row[1], row[2], other[1], other[2]) <= self.nms_iou for other in kept):
                    kept.append(row)
            kept.sort(key=lambda row: (float(row[1].item()), float(row[2].item()), int(row[4])))
            candidates.append(kept)

        selected = features.new_zeros(batch, self.top_k, dim)
        selected_times = timestamps.new_zeros(batch, self.top_k, 2)
        selected_source_indices = torch.full(
            (batch, self.top_k),
            -1,
            dtype=torch.long,
            device=features.device,
        )
        selected_scores = scores.new_zeros(batch, self.top_k)
        selected_mask = torch.zeros(batch, self.top_k, dtype=torch.bool, device=features.device)
        source_weights = features.new_zeros(batch, self.top_k, steps)
        for batch_index, rows in enumerate(candidates):
            for slot, row in enumerate(rows):
                selected_scores[batch_index, slot] = row[0]
                selected_times[batch_index, slot] = torch.stack([row[1], row[2]])
                selected[batch_index, slot] = row[3]
                selected_source_indices[batch_index, slot] = row[4]
                source_weights[batch_index, slot] = row[5]
                selected_mask[batch_index, slot] = True
        return DynamicWindowSelectorOutput(
            selected_features=selected,
            selected_timestamps=selected_times,
            selected_source_indices=selected_source_indices,
            selection_scores=selected_scores,
            selection_mask=selected_mask,
            source_weights=source_weights,
            eventness_cached_scores=scores,
            dynamic_thresholds=thresholds,
            state=state,
        )


class DynamicWindowSelector(LegacyMultiscaleDynamicWindowSelector):
    """Formal cached-eventness Top-K over original micro-windows.

    Unlike :class:`LegacyMultiscaleDynamicWindowSelector`, this selector never
    pools multiple source windows and never runs interval NMS.  Every selected
    feature, timestamp, and source index refers to exactly one input
    micro-window.  The default contract accepts full one-second windows and a
    shorter final partial window.
    """

    def __init__(
        self,
        input_dim: int,
        top_k: int = 16,
        ema_beta: float = 0.9,
        start_scale: float = 1.0,
        hold_scale: float = 0.25,
        min_score: float = 0.05,
        rms_reference: float = 0.05,
        silence_threshold: float = 1e-4,
        causal: bool = True,
        micro_window_sec: float = 1.0,
        timestamp_tolerance: float = 1e-4,
    ) -> None:
        if micro_window_sec <= 0:
            raise ValueError("micro_window_sec must be positive")
        if timestamp_tolerance < 0:
            raise ValueError("timestamp_tolerance must be non-negative")
        super().__init__(
            input_dim=input_dim,
            scales_sec=(float(micro_window_sec),),
            top_k=top_k,
            nms_iou=1.0,
            ema_beta=ema_beta,
            start_scale=start_scale,
            hold_scale=hold_scale,
            min_score=min_score,
            rms_reference=rms_reference,
            silence_threshold=silence_threshold,
            causal=causal,
        )
        self.micro_window_sec = float(micro_window_sec)
        self.timestamp_tolerance = float(timestamp_tolerance)

    def forward(
        self,
        audio_features: torch.Tensor,
        audio_timestamps: torch.Tensor,
        audio_mask: torch.Tensor | None = None,
        audio_windows: torch.Tensor | None = None,
        state: SelectorState | None = None,
    ) -> DynamicWindowSelectorOutput:
        features = _ensure_features(audio_features, self.input_dim)
        batch, steps, _ = features.shape
        timestamps = _ensure_timestamps(audio_timestamps, batch, steps, features.device)
        mask = _ensure_mask(audio_mask, batch, steps, features.device)
        durations = timestamps[..., 1] - timestamps[..., 0]
        invalid_duration = mask & (
            (durations <= 0)
            | (durations > self.micro_window_sec + self.timestamp_tolerance)
        )
        if invalid_duration.any():
            raise ValueError(
                "micro_window mode requires positive original windows no longer "
                f"than {self.micro_window_sec:g} seconds"
            )
        return super().forward(
            features,
            timestamps,
            audio_mask=mask,
            audio_windows=audio_windows,
            state=state,
        )

    def _select_candidates(self, features, timestamps, mask, scores, thresholds, active, state):
        del active
        batch, steps, dim = features.shape
        selected = features.new_zeros(batch, self.top_k, dim)
        selected_times = timestamps.new_zeros(batch, self.top_k, 2)
        selected_source_indices = torch.full(
            (batch, self.top_k),
            -1,
            dtype=torch.long,
            device=features.device,
        )
        selected_scores = scores.new_zeros(batch, self.top_k)
        selected_mask = torch.zeros(batch, self.top_k, dtype=torch.bool, device=features.device)
        source_weights = features.new_zeros(batch, self.top_k, steps)

        for batch_index in range(batch):
            ranked = [
                (
                    -float(scores[batch_index, source_index].item()),
                    float(timestamps[batch_index, source_index, 0].item()),
                    float(timestamps[batch_index, source_index, 1].item()),
                    source_index,
                )
                for source_index in torch.nonzero(mask[batch_index], as_tuple=False).flatten().tolist()
            ]
            ranked.sort()
            chosen = sorted(ranked[: min(self.top_k, len(ranked))], key=lambda row: (row[1], row[2], row[3]))
            for slot, row in enumerate(chosen):
                source_index = row[3]
                selected[batch_index, slot] = features[batch_index, source_index]
                selected_times[batch_index, slot] = timestamps[batch_index, source_index]
                selected_source_indices[batch_index, slot] = source_index
                selected_scores[batch_index, slot] = scores[batch_index, source_index]
                selected_mask[batch_index, slot] = True
                source_weights[batch_index, slot, source_index] = 1.0

        return DynamicWindowSelectorOutput(
            selected_features=selected,
            selected_timestamps=selected_times,
            selected_source_indices=selected_source_indices,
            selection_scores=selected_scores,
            selection_mask=selected_mask,
            source_weights=source_weights,
            eventness_cached_scores=scores,
            dynamic_thresholds=thresholds,
            state=state,
        )


def _interval_iou(start_a, end_a, start_b, end_b):
    intersection = torch.minimum(end_a, end_b) - torch.maximum(start_a, start_b)
    intersection = intersection.clamp_min(0.0)
    union = (end_a - start_a) + (end_b - start_b) - intersection
    return float((intersection / union.clamp_min(1e-6)).item())


def _feature_change(features, mask, previous_feature=None):
    previous = torch.cat([features[:, :1], features[:, :-1]], dim=1)
    if previous_feature is not None:
        previous[:, 0] = previous_feature.to(device=features.device, dtype=features.dtype)
    change = 1.0 - F.cosine_similarity(features, previous, dim=-1, eps=1e-6)
    both_zero = (features.norm(dim=-1) <= 1e-6) & (previous.norm(dim=-1) <= 1e-6)
    change = change.masked_fill(both_zero, 0.0)
    if previous_feature is None:
        change[:, 0] = 0.0
    return change.clamp(0.0, 1.0).masked_fill(~mask, 0.0)


def _waveform_statistics(windows, batch, steps, device, silence_threshold):
    if windows is None:
        return None, None
    values = windows.to(device=device, dtype=torch.float32)
    if values.ndim == 2:
        values = values.unsqueeze(0)
    if values.shape[:2] != (batch, steps):
        raise ValueError(f"audio_windows shape {tuple(values.shape)} does not match {(batch, steps)}")
    values = torch.nan_to_num(values, nan=0.0, posinf=0.0, neginf=0.0)
    rms = values.square().mean(dim=-1).sqrt()
    non_silent = values.abs().gt(float(silence_threshold)).float().mean(dim=-1)
    return rms, non_silent


def _ensure_features(features, input_dim):
    if features.ndim == 2:
        features = features.unsqueeze(0)
    if features.ndim != 3 or features.shape[-1] != input_dim:
        raise ValueError(f"Expected audio_features [B,T,{input_dim}], got {tuple(features.shape)}")
    return features


def _ensure_timestamps(timestamps, batch, steps, device):
    values = timestamps.to(device=device, dtype=torch.float32)
    if values.ndim == 2:
        values = values.unsqueeze(0).expand(batch, -1, -1)
    if values.shape != (batch, steps, 2):
        raise ValueError(f"audio_timestamps shape {tuple(values.shape)} does not match {(batch, steps, 2)}")
    if not torch.isfinite(values).all() or (values[..., 0] > values[..., 1]).any():
        raise ValueError("audio_timestamps must be finite and satisfy start <= end")
    return values


def _ensure_mask(mask, batch, steps, device):
    if mask is None:
        return torch.ones(batch, steps, dtype=torch.bool, device=device)
    values = mask.to(device=device, dtype=torch.bool)
    if values.shape != (batch, steps):
        raise ValueError(f"audio_mask shape {tuple(values.shape)} does not match {(batch, steps)}")
    return values
