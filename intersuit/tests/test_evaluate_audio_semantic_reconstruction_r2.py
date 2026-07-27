from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import torch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/evaluate_audio_semantic_reconstruction_r2.py"
SPEC = importlib.util.spec_from_file_location("evaluate_audio_semantic_r2", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class FakeTokenizer:
    def apply_chat_template(self, messages, tokenize=True):
        assert messages[0]["role"] == "assistant"
        return [1, 10, 20, 30]

    def __call__(self, text, add_special_tokens=False):
        class Result:
            input_ids = [20]

        return Result()


def test_old_target_includes_chat_tokens_after_first_token() -> None:
    tokenizer = FakeTokenizer()
    assert MODULE.old_answer_token_ids(tokenizer, "label") == [10, 20, 30]
    assert MODULE.plain_token_ids(tokenizer, "label") == [20]


def test_fixed_target_golden_fixture_is_perfect() -> None:
    rows = [
        {
            "sample_id": "a",
            "canonical_label": "a",
            "projected_audio": torch.tensor([1.0, 0.0]),
        },
        {
            "sample_id": "b",
            "canonical_label": "b",
            "projected_audio": torch.tensor([0.0, 1.0]),
        },
    ]
    metrics, per_sample = MODULE.evaluate_fixed_targets(
        rows,
        ["a", "b"],
        {"a": torch.tensor([1.0, 0.0]), "b": torch.tensor([0.0, 1.0])},
        bootstrap_seed=17,
    )
    assert metrics["rank_first"] == 1.0
    assert metrics["macro_class_recall_at_1"] == 1.0
    assert metrics["margin_bootstrap_95_ci"][0] > 0
    assert all(row["correct"] for row in per_sample)
