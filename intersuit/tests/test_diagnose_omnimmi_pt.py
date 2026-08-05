import sys
from pathlib import Path

import torch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from diagnose_omnimmi_pt import (  # noqa: E402
    build_prompt,
    build_pt_query,
    score_logits,
    select_frame_indices,
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
