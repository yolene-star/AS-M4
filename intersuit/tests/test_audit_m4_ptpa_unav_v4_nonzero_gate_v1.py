"""v4非零gate独立审计器基础测试。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/audit_m4_ptpa_unav_v4_nonzero_gate_v1.py"
SPEC = importlib.util.spec_from_file_location("v4_nonzero_audit", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_summarize_counts_fail_closed_fields():
    rows = [
        {
            "correct": True,
            "prediction": "A",
            "delta_to_video_ratio": 0.001,
            "cuda_peak_memory_allocated_bytes": 10,
            "generation_debug": {"tokens": {"first_token_is_eos": False}},
        },
        {
            "correct": False,
            "prediction": "",
            "delta_to_video_ratio": 0.002,
            "cuda_peak_memory_allocated_bytes": 20,
            "generation_debug": {"tokens": {"first_token_is_eos": True}},
        },
    ]

    summary = MODULE.summarize(rows)

    assert summary["rows"] == 2
    assert summary["correct"] == 1
    assert summary["empty_predictions"] == 1
    assert summary["first_token_eos"] == 1
    assert summary["min_delta_to_video_ratio"] == 0.001
    assert summary["max_delta_to_video_ratio"] == 0.002
    assert summary["max_peak_memory_allocated_bytes"] == 20
