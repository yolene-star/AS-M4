from __future__ import annotations

import torch
import pytest

from intersuit.model.streaming_av.audio_token_injection import build_audio_token_features, insert_audio_tokens


def test_audio_token_append_and_positions():
    x = torch.zeros(1, 3, 4)
    mask = torch.ones(1, 3, dtype=torch.bool)
    audio = torch.ones(1, 2, 4)
    out = insert_audio_tokens(x, mask, None, audio, gate=0.5)
    assert out.inserted is True
    assert out.inputs_embeds.shape == (1, 5, 4)
    assert torch.equal(out.position_ids, torch.arange(5).view(1, -1))
    assert torch.equal(out.inputs_embeds[:, :3], x)
    assert torch.equal(out.inputs_embeds[:, 3:], audio * 0.5)


def test_gate_zero_is_exact_noop():
    x = torch.randn(2, 3, 4)
    mask = torch.ones(2, 3, dtype=torch.bool)
    pos = torch.arange(3).view(1, -1).expand(2, -1)
    out = insert_audio_tokens(x, mask, pos, torch.randn(2, 2, 4), gate=0.0)
    assert out.inserted is False
    assert torch.equal(out.inputs_embeds, x)
    assert torch.equal(out.attention_mask, mask)
    assert torch.equal(out.position_ids, pos)


def test_insert_index_shifts_tail_and_rejects_bad_shape():
    x = torch.zeros(1, 3, 2)
    mask = torch.ones(1, 3, dtype=torch.bool)
    out = insert_audio_tokens(x, mask, None, torch.ones(1, 1, 2), insert_at=1)
    assert torch.equal(out.position_ids, torch.tensor([[0, 1, 2, 3]]))
    with pytest.raises(ValueError):
        insert_audio_tokens(x, mask, None, torch.ones(1, 1, 3))


def test_physical_silence_bypass_returns_no_tokens_per_sample():
    features=torch.randn(2,3,4)
    feature_mask=torch.tensor([[1,1,0],[1,0,0]],dtype=torch.bool)
    windows=torch.stack([torch.zeros(3,8),torch.ones(3,8)])
    tokens=build_audio_token_features(
        features,feature_mask,windows,gate=1.0,silence_bypass=True,silence_threshold=1e-6
    )
    assert tokens[0] is None
    assert torch.equal(tokens[1],features[1,:1])
    gate0=build_audio_token_features(features,feature_mask,windows,gate=0.0)
    assert gate0==[None,None]
