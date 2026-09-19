"""LLP modality 预测审计器的 CPU tests。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/evaluate_llp_modality_predictions_v1.py"
SPEC = importlib.util.spec_from_file_location("evaluate_llp_modality_predictions_v1", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def manifest_rows():
    rows = []
    for base_id, role, audio, visual in (("a", "audio_only", "yes", "no"), ("v", "visual_only", "no", "yes")):
        for query, target, conditions in (
            ("audio_presence", audio, ["correct_audio", "mismatch_audio", "video_only", "silence"]),
            ("visual_presence", visual, ["video_only", "correct_audio", "mismatch_audio"]),
        ):
            for condition in conditions:
                rows.append({"eval_id": f"{base_id}:{query}:{condition}", "base_id": base_id, "query_type": query, "condition": condition, "modality_role": role, "target_answer": target})
    return rows


def test_normalize_yes_no_is_strict_but_accepts_short_explanation():
    assert module.normalize_yes_no("Yes, it is audible") == "yes"
    assert module.normalize_yes_no("No.") == "no"
    assert module.normalize_yes_no("Maybe") is None


def test_contract_mock_exercises_selectivity_and_invariance():
    manifest = manifest_rows()
    result = module.evaluate(manifest, module.make_contract_mock(manifest))
    assert result["invalid_prediction_count"] == 0
    assert result["audio_positive_correct_accuracy"] == 1.0
    assert result["correct_mismatch_margin"] == 1.0
    assert result["correct_silence_margin"] == 1.0
    assert result["visual_only_flip_rate"] == 0.0
    assert result["silence_video_only_consistency"] == 1.0


def test_missing_prediction_fails_closed():
    manifest = manifest_rows()
    predictions = module.make_contract_mock(manifest)[:-1]
    with pytest.raises(ValueError, match="预测键集合不一致"):
        module.evaluate(manifest, predictions)
