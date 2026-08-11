from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
EVALUATOR_PATH = ROOT / "third_party" / "OmniMMI" / "evaluations" / "evaluate.py"
SPEC = importlib.util.spec_from_file_location("omnimmi_evaluate_strict_json", EVALUATOR_PATH)
evaluator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluator)


def test_loader_accepts_standard_json_booleans_null_and_diagnostics(tmp_path):
    path = tmp_path / "predictions.jsonl"
    record = {
        "answer": [1, 3],
        "pred": [2],
        "audio_diagnostics": {"enabled": True, "optional": None},
    }
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    assert evaluator.load_prediction_jsonl(path) == [record]


def test_loader_rejects_python_literal_non_json_and_missing_schema(tmp_path):
    literal_path = tmp_path / "literal.jsonl"
    literal_path.write_text("{'answer': [1, 3], 'pred': [2], 'enabled': True}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid JSONL"):
        evaluator.load_prediction_jsonl(literal_path)

    missing_path = tmp_path / "missing.jsonl"
    missing_path.write_text('{"answer": [1, 3]}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="missing required keys"):
        evaluator.load_prediction_jsonl(missing_path)
