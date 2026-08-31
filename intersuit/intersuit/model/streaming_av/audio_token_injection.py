"""Independent scene-audio token insertion utilities for S3 diagnostics."""

from __future__ import annotations

from typing import NamedTuple

import torch


class AudioTokenInjectionOutput(NamedTuple):
    inputs_embeds: torch.Tensor
    attention_mask: torch.Tensor
    position_ids: torch.Tensor
    inserted: bool


def insert_audio_tokens(
    inputs_embeds: torch.Tensor,
    attention_mask: torch.Tensor,
    position_ids: torch.Tensor | None,
    audio_tokens: torch.Tensor | None,
    *,
    insert_at: int | None = None,
    gate: float = 1.0,
) -> AudioTokenInjectionOutput:
    """Insert a fixed audio-token block without changing existing token values.

    ``gate=0`` is an exact no-op. Audio tokens are appended by default; an explicit
    insertion index is useful for diagnostics but must be within the sequence.
    """
    if inputs_embeds.ndim != 3 or attention_mask.shape != inputs_embeds.shape[:2]:
        raise ValueError("inputs_embeds/attention_mask shape mismatch")
    if position_ids is not None and position_ids.shape != inputs_embeds.shape[:2]:
        raise ValueError("position_ids shape mismatch")
    if not 0.0 <= float(gate) <= 1.0:
        raise ValueError("gate must be in [0,1]")
    if audio_tokens is None or float(gate) == 0.0:
        pos = position_ids if position_ids is not None else torch.arange(inputs_embeds.shape[1], device=inputs_embeds.device).view(1, -1).expand(inputs_embeds.shape[0], -1)
        return AudioTokenInjectionOutput(inputs_embeds, attention_mask, pos, False)
    if audio_tokens.ndim != 3 or audio_tokens.shape[0] != inputs_embeds.shape[0] or audio_tokens.shape[2] != inputs_embeds.shape[2]:
        raise ValueError("audio_tokens must be [B,A,H] matching input batch/hidden size")
    batch, seq, _ = inputs_embeds.shape
    index = seq if insert_at is None else int(insert_at)
    if not 0 <= index <= seq:
        raise ValueError("insert_at outside input sequence")
    tokens = audio_tokens.to(device=inputs_embeds.device, dtype=inputs_embeds.dtype) * float(gate)
    merged = torch.cat([inputs_embeds[:, :index], tokens, inputs_embeds[:, index:]], dim=1)
    audio_mask = torch.ones((batch, tokens.shape[1]), dtype=attention_mask.dtype, device=attention_mask.device)
    merged_mask = torch.cat([attention_mask[:, :index], audio_mask, attention_mask[:, index:]], dim=1)
    base_pos = position_ids if position_ids is not None else torch.arange(seq, device=inputs_embeds.device).view(1, -1).expand(batch, -1)
    inserted_pos = torch.arange(index, index + tokens.shape[1], device=base_pos.device).view(1, -1).expand(batch, -1)
    shifted_tail = base_pos[:, index:] + tokens.shape[1]
    merged_pos = torch.cat([base_pos[:, :index], inserted_pos, shifted_tail], dim=1)
    return AudioTokenInjectionOutput(merged, merged_mask, merged_pos, True)
