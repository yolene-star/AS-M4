"""v4到M4的gate0独立审计器测试。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/audit_m4_ptpa_unav_v4_gate0_smoke_v1.py"
SPEC = importlib.util.spec_from_file_location("v4_gate0_audit", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _write_jsonl(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def test_gate0_exact_rollback_passes(tmp_path):
    tokens = {"first_20_new_token_ids": [1, 2], "audio_input": None}
    behavior = {
        "sample_id": "ave_stage2_QSgqjJ2iglM_audio",
        "prediction": "answer",
        "correct": False,
        "cuda_device": "cuda:0",
        "runtime_seconds": 1.0,
        "cuda_peak_memory_allocated_bytes": 10,
        "generation_debug": {"tokens": tokens},
    }
    gate0 = {
        **behavior,
        "gate": 0.0,
        "audio_norm": 2.0,
        "delta_to_video_ratio": 0.0,
        "raw_delta_to_video_ratio": 0.0,
        "capped_delta_to_video_ratio": 0.0,
        "as_m4_diagnostics": [
            {
                "gate_mean": 0.0,
                "gate_min": 0.0,
                "gate_max": 0.0,
                "delta_norm": 0.0,
                "delta_to_video_ratio": 0.0,
                "raw_delta_norm": 0.0,
                "raw_delta_to_video_ratio": 0.0,
                "capped_delta_norm": 0.0,
                "capped_delta_to_video_ratio": 0.0,
            }
        ],
        "generation_debug": {
            "tokens": {
                "first_20_new_token_ids": [1, 2],
                "audio_input": {"window_count": 3},
            }
        },
    }
    behavior_path = tmp_path / "behavior.jsonl"
    gate0_path = tmp_path / "gate0.jsonl"
    frozen_path = tmp_path / "frozen.json"
    _write_jsonl(behavior_path, behavior)
    _write_jsonl(gate0_path, gate0)
    frozen_path.write_text(json.dumps([{"unchanged": True}]), encoding="utf-8")

    result = MODULE.audit(behavior_path, gate0_path, frozen_path)

    assert result["status"] == "PASS_V4_M4_GATE0_EXACT_ROLLBACK"
    assert all(result["checks"].values())
    assert result["test_accessed"] is False
    assert result["nonzero_gate_executed"] is False


def test_gate0_nonzero_delta_fails(tmp_path):
    behavior_path = tmp_path / "behavior.jsonl"
    gate0_path = tmp_path / "gate0.jsonl"
    frozen_path = tmp_path / "frozen.json"
    base = {
        "sample_id": "ave_stage2_QSgqjJ2iglM_audio",
        "prediction": "answer",
        "correct": False,
        "cuda_device": "cuda:0",
        "generation_debug": {
            "tokens": {"first_20_new_token_ids": [1], "audio_input": {"window_count": 1}}
        },
        "gate": 0.0,
        "audio_norm": 1.0,
        "delta_to_video_ratio": 0.1,
        "raw_delta_to_video_ratio": 0.0,
        "capped_delta_to_video_ratio": 0.0,
        "as_m4_diagnostics": [{"gate_mean": 0, "gate_min": 0, "gate_max": 0}],
    }
    _write_jsonl(behavior_path, base)
    _write_jsonl(gate0_path, base)
    frozen_path.write_text(json.dumps([{"unchanged": True}]), encoding="utf-8")

    result = MODULE.audit(behavior_path, gate0_path, frozen_path)

    assert result["status"] == "FAIL_V4_M4_GATE0"
    assert result["checks"]["gate0_delta_ratio_exact_zero"] is False
