"""新 fusion projector gate0 单样本合同。"""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "harness/configs/m4_ptpa_unav_v4_fusion_projector_gate0_smoke_v1.json"
PLAN = ROOT / "harness/configs/m4_ptpa_unav_v4_fusion_projector_gate0_smoke_plan_v1.jsonl"

def test_gate0_contract_is_dev_only_and_frozen():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    assert config["dataset"]["sample_limit"] == 1
    assert config["dataset"]["test_accessed"] is False
    assert config["permissions"]["nonzero_gate_allowed"] is False
    assert config["permissions"]["training_allowed"] is False

def test_gate0_contract_covers_bypass_and_lightweight():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    modes = {exp.get("env", {}).get("AS_M4_FUSION_PROJECTOR_MODE") for exp in config["experiments"]}
    assert {"bypass", "lightweight"} <= modes
    for exp in config["experiments"][1:]:
        assert exp["force_audio_gate"] == "0"

def test_locked_plan_matches_three_contract_conditions():
    rows = [json.loads(line) for line in PLAN.read_text(encoding="utf-8").splitlines()]
    assert [row["id"] for row in rows] == ["V4_BEHAVIOR_OFF", "V4_BYPASS_GATE0", "V4_LIGHTWEIGHT_GATE0"]
    assert rows[1]["env"]["AS_M4_FUSION_PROJECTOR_MODE"] == "bypass"
    assert rows[2]["env"]["AS_M4_FUSION_PROJECTOR_MODE"] == "lightweight"
    assert all(row["env"]["AS_M4_FORCE_AUDIO_GATE"] == "0" for row in rows)
