"""v4非零gate诊断结果锁测试。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "configs/m4_ptpa_unav_v4_nonzero_gate_result_lock_v1.json"


def _load() -> dict:
    return json.loads(LOCK.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def test_nonzero_result_audit_matches_locked_sha():
    lock = _load()
    artifact = lock["artifacts"]
    path = ROOT.parent / artifact["independent_audit_path"]

    assert path.is_file()
    assert _sha256(path) == artifact["independent_audit_sha256"]


def test_nonzero_result_stops_expansion_and_effect_claims():
    lock = _load()
    checks = lock["checks"]
    results = lock["results"]

    assert lock["status"] == "PASS_TECHNICAL_NO_SELECTIVE_TASK_GAIN_STOP_EXPANSION"
    assert results["behavior_correct"] == results["correct_audio_correct"] == results["mismatch_audio_correct"] == 3
    assert results["correct_only_improvements"] == 0
    assert results["mismatch_only_improvements"] == 0
    assert checks["technical_execution_pass"] is True
    assert checks["correct_selective_gain_observed"] is False
    assert checks["full_dev_allowed"] is False
    assert checks["formal_test_allowed"] is False
    assert checks["training_allowed"] is False
    assert checks["m4_effect_claim_allowed"] is False
