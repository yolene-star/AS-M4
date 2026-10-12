"""LLP teacher-forced NLL smoke 审计器的 CPU tests。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/audit_llp_modality_teacher_forced_v1.py"
SPEC = importlib.util.spec_from_file_location("audit_llp_modality_teacher_forced_v1", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def rows(correct=1.0, mismatch=2.0, video=2.5):
    data = []
    for query, conditions, role in (
        ("audio_presence", ("correct_audio", "mismatch_audio", "video_only", "silence"), "audio_only"),
        ("visual_presence", ("correct_audio", "mismatch_audio", "video_only"), "visual_only"),
    ):
        for condition in conditions:
            value = {"correct_audio": correct, "mismatch_audio": mismatch, "video_only": video, "silence": video}[condition]
            data.append({"base_id": "g", "query_type": query, "condition": condition, "modality_role": role, "nll": value})
    return data


def test_pipeline_pass_but_selectivity_can_fail():
    result = module.audit(rows())
    assert result["pipeline_pass"] is True
    assert result["selectivity_evidence_pass"] is True
    result = module.audit(rows(correct=3.0, mismatch=2.0, video=2.5))
    assert result["selectivity_evidence_pass"] is False


def test_missing_condition_fails_closed():
    with pytest.raises(ValueError, match="条件不完整"):
        module.audit(rows()[:-1])
