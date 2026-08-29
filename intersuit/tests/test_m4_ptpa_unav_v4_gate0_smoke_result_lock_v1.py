"""v4到M4的gate0结果锁测试。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "configs/m4_ptpa_unav_v4_gate0_smoke_result_lock_v1.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load() -> dict:
    return json.loads(LOCK.read_text(encoding="utf-8"))


def test_gate0_result_artifacts_match_locked_sha():
    lock = _load()

    for artifact in lock["result_artifacts"].values():
        path = ROOT.parent / artifact["path"]
        assert path.is_file()
        assert _sha256(path) == artifact["sha256"]


def test_gate0_result_is_exact_rollback_but_not_effect_evidence():
    lock = _load()
    checks = lock["checks"]
    execution = lock["execution"]

    assert lock["status"] == "PASS_V4_M4_GATE0_EXACT_ROLLBACK"
    assert execution["physical_gpu"] == 2
    assert execution["formal_test_accessed"] is False
    assert execution["training_performed"] is False
    assert execution["nonzero_gate_executed"] is False
    assert checks["prediction_exact_match"] is True
    assert checks["generated_tokens_exact_match"] is True
    assert checks["gate0_audio_was_decoded"] is True
    assert checks["gate0_exact_zero"] is True
    assert checks["frozen_runtime_sha_unchanged"] is True
    assert checks["m4_effect_claim_allowed"] is False
    assert checks["nonzero_gate_next_stage_automatically_allowed"] is False


def test_gate0_prediction_failure_is_preserved_not_hidden():
    metrics = _load()["metrics"]

    assert metrics["prediction_correct"] is False
    assert metrics["gate0_delta_to_video_ratio"] == 0.0
    assert metrics["gate0_audio_window_count"] == 20
