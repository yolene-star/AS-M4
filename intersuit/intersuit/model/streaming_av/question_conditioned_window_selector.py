"""Trainable question-conditioned causal Top-K selector for AS-M4 V2."""

from __future__ import annotations

from typing import NamedTuple

import torch
import torch.nn.functional as F
from torch import nn


class QuestionConditionedSelectorState(NamedTuple):
    """Exact retained Top-K memory required by causal replay."""

    retained_features: torch.Tensor
    retained_scores: torch.Tensor
    retained_timestamps: torch.Tensor
    retained_source_indices: torch.Tensor
    retained_mask: torch.Tensor
    next_source_index: torch.Tensor


class QuestionConditionedSelectorOutput(NamedTuple):
    """Selected windows plus scores for the newly supplied chunk."""

    selected_features: torch.Tensor
    selected_timestamps: torch.Tensor
    selected_source_indices: torch.Tensor
    selection_scores: torch.Tensor
    selection_mask: torch.Tensor
    source_weights: torch.Tensor
    all_scores: torch.Tensor
    state: QuestionConditionedSelectorState


class QuestionConditionedWindowSelector(nn.Module):
    """Score encoded audio windows against a pooled question embedding.

    Selection is deterministic. Ties are resolved by timestamp and then by the
    explicit source index. In causal mode the state retains only the global
    Top-K, which is sufficient to reproduce exact prefix Top-K selection.
    """

    def __init__(
        self,
        audio_dim: int,
        question_dim: int,
        projection_dim: int = 256,
        top_k: int = 8,
    ) -> None:
        super().__init__()
        if min(audio_dim, question_dim, projection_dim, top_k) <= 0:
            raise ValueError("audio_dim, question_dim, projection_dim and top_k must be positive")
        self.audio_dim = int(audio_dim)
        self.question_dim = int(question_dim)
        self.projection_dim = int(projection_dim)
        self.top_k = int(top_k)
        self.audio_projection = nn.Linear(self.audio_dim, self.projection_dim, bias=False)
        self.question_projection = nn.Linear(self.question_dim, self.projection_dim, bias=False)

    def forward(
        self,
        audio_features: torch.Tensor,
        question_features: torch.Tensor,
        timestamps: torch.Tensor,
        valid_mask: torch.Tensor | None = None,
        source_indices: torch.Tensor | None = None,
        state: QuestionConditionedSelectorState | None = None,
    ) -> QuestionConditionedSelectorOutput:
        if audio_features.ndim != 3 or audio_features.shape[-1] != self.audio_dim:
            raise ValueError(f"audio_features must be [B,T,{self.audio_dim}]")
        batch_size, chunk_size, _ = audio_features.shape
        if question_features.shape != (batch_size, self.question_dim):
            raise ValueError(f"question_features must be [B,{self.question_dim}]")
        timestamps = self._normalize_timestamps(timestamps, batch_size, chunk_size, audio_features)
        valid_mask = self._normalize_mask(valid_mask, batch_size, chunk_size, audio_features.device)
        source_indices = self._normalize_source_indices(
            source_indices,
            batch_size,
            chunk_size,
            audio_features.device,
            state,
        )
        next_source_index = source_indices.max(dim=1).values + 1 if chunk_size else (
            state.next_source_index if state is not None else torch.zeros(batch_size, dtype=torch.long, device=audio_features.device)
        )

        audio_unit = F.normalize(self.audio_projection(audio_features).float(), dim=-1)
        question_unit = F.normalize(self.question_projection(question_features).float(), dim=-1)
        all_scores = torch.einsum("btd,bd->bt", audio_unit, question_unit)
        all_scores = all_scores.to(dtype=audio_features.dtype).masked_fill(~valid_mask, 0.0)

        if state is None:
            candidate_features = audio_features
            candidate_scores = all_scores
            candidate_timestamps = timestamps
            candidate_source_indices = source_indices
            candidate_mask = valid_mask
            state_size = 0
        else:
            self._validate_state(state, batch_size, audio_features)
            candidate_features = torch.cat([state.retained_features, audio_features], dim=1)
            candidate_scores = torch.cat([state.retained_scores, all_scores], dim=1)
            candidate_timestamps = torch.cat([state.retained_timestamps, timestamps], dim=1)
            candidate_source_indices = torch.cat([state.retained_source_indices, source_indices], dim=1)
            candidate_mask = torch.cat([state.retained_mask, valid_mask], dim=1)
            state_size = state.retained_features.shape[1]

        output = self._select(
            candidate_features,
            candidate_scores,
            candidate_timestamps,
            candidate_source_indices,
            candidate_mask,
            chunk_size=chunk_size,
            state_size=state_size,
            all_scores=all_scores,
            next_source_index=next_source_index,
        )
        return output

    def _select(
        self,
        features: torch.Tensor,
        scores: torch.Tensor,
        timestamps: torch.Tensor,
        source_indices: torch.Tensor,
        mask: torch.Tensor,
        *,
        chunk_size: int,
        state_size: int,
        all_scores: torch.Tensor,
        next_source_index: torch.Tensor,
    ) -> QuestionConditionedSelectorOutput:
        batch_size, _, feature_dim = features.shape
        selected = features.new_zeros(batch_size, self.top_k, feature_dim)
        selected_scores = scores.new_zeros(batch_size, self.top_k)
        selected_times = timestamps.new_zeros(batch_size, self.top_k, 2)
        selected_indices = source_indices.new_full((batch_size, self.top_k), -1)
        selected_mask = torch.zeros(batch_size, self.top_k, dtype=torch.bool, device=features.device)
        source_weights = features.new_zeros(batch_size, self.top_k, chunk_size)

        for batch_index in range(batch_size):
            ranked = []
            for candidate_index in torch.nonzero(mask[batch_index], as_tuple=False).flatten().tolist():
                ranked.append(
                    (
                        -float(scores[batch_index, candidate_index].detach().float().item()),
                        float(timestamps[batch_index, candidate_index, 0].item()),
                        float(timestamps[batch_index, candidate_index, 1].item()),
                        int(source_indices[batch_index, candidate_index].item()),
                        candidate_index,
                    )
                )
            ranked.sort()
            chosen = sorted(ranked[: self.top_k], key=lambda row: (row[1], row[2], row[3]))
            for slot, row in enumerate(chosen):
                candidate_index = row[4]
                selected[batch_index, slot] = features[batch_index, candidate_index]
                selected_scores[batch_index, slot] = scores[batch_index, candidate_index]
                selected_times[batch_index, slot] = timestamps[batch_index, candidate_index]
                selected_indices[batch_index, slot] = source_indices[batch_index, candidate_index]
                selected_mask[batch_index, slot] = True
                local_chunk_index = candidate_index - state_size
                if 0 <= local_chunk_index < chunk_size:
                    source_weights[batch_index, slot, local_chunk_index] = 1.0

        state = QuestionConditionedSelectorState(
            retained_features=selected,
            retained_scores=selected_scores,
            retained_timestamps=selected_times,
            retained_source_indices=selected_indices,
            retained_mask=selected_mask,
            next_source_index=next_source_index,
        )
        return QuestionConditionedSelectorOutput(
            selected_features=selected,
            selected_timestamps=selected_times,
            selected_source_indices=selected_indices,
            selection_scores=selected_scores,
            selection_mask=selected_mask,
            source_weights=source_weights,
            all_scores=all_scores,
            state=state,
        )

    @staticmethod
    def _normalize_timestamps(timestamps, batch_size, chunk_size, reference):
        timestamps = timestamps.to(device=reference.device, dtype=reference.dtype)
        if timestamps.ndim == 2:
            timestamps = timestamps.unsqueeze(0).expand(batch_size, -1, -1)
        if timestamps.shape != (batch_size, chunk_size, 2):
            raise ValueError("timestamps must be [T,2] or [B,T,2]")
        if not torch.all(torch.isfinite(timestamps)) or not torch.all(timestamps[..., 0] < timestamps[..., 1]):
            raise ValueError("timestamps must be finite intervals with start < end")
        return timestamps

    @staticmethod
    def _normalize_mask(mask, batch_size, chunk_size, device):
        if mask is None:
            return torch.ones(batch_size, chunk_size, dtype=torch.bool, device=device)
        mask = mask.to(device=device, dtype=torch.bool)
        if mask.shape != (batch_size, chunk_size):
            raise ValueError("valid_mask must be [B,T]")
        return mask

    @staticmethod
    def _normalize_source_indices(indices, batch_size, chunk_size, device, state):
        if indices is None:
            starts = (
                state.next_source_index.to(device=device, dtype=torch.long)
                if state is not None
                else torch.zeros(batch_size, dtype=torch.long, device=device)
            )
            return starts.unsqueeze(1) + torch.arange(chunk_size, device=device).unsqueeze(0)
        indices = indices.to(device=device, dtype=torch.long)
        if indices.ndim == 1:
            indices = indices.unsqueeze(0).expand(batch_size, -1)
        if indices.shape != (batch_size, chunk_size):
            raise ValueError("source_indices must be [T] or [B,T]")
        return indices

    def _validate_state(self, state, batch_size, reference):
        expected = (batch_size, self.top_k)
        if state.retained_features.shape != (*expected, self.audio_dim):
            raise ValueError("state retained_features shape mismatch")
        if state.retained_scores.shape != expected or state.retained_mask.shape != expected:
            raise ValueError("state score/mask shape mismatch")
        if state.retained_timestamps.shape != (*expected, 2):
            raise ValueError("state timestamps shape mismatch")
        if state.retained_source_indices.shape != expected:
            raise ValueError("state source_indices shape mismatch")
        if state.next_source_index.shape != (batch_size,):
            raise ValueError("state next_source_index shape mismatch")
        if state.retained_features.device != reference.device:
            raise ValueError("state and audio_features must be on the same device")
