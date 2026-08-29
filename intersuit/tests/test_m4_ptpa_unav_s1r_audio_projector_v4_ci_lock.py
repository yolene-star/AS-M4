"""UnAV v4物理视频级CI机器锁测试。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "configs/m4_ptpa_unav_s1r_audio_projector_v4_ci_lock.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load() -> dict:
    return json.loads(LOCK.read_text(encoding="utf-8"))


def test_ci_result_artifacts_match_locked_sha():
    lock = _load()

    for artifact in lock["result_artifacts"].values():
        path = ROOT.parent / artifact["path"]
        assert path.is_file()
        assert _sha256(path) == artifact["sha256"]


def test_ci_claims_are_precise_and_fail_closed():
    lock = _load()
    checks = lock["checks"]

    assert lock["seeds"] == [17, 23, 47]
    assert lock["replicates_per_seed"] == 5000
    assert checks["projected_margin_ci_lower_positive_all_seeds"] is True
    assert checks["margin_delta_ci_lower_positive_all_seeds"] is True
    assert checks["macro_delta_ci_includes_zero_all_seeds"] is True
    assert checks["recall_significant_improvement_claim_allowed"] is False
    assert checks["margin_improvement_claim_allowed"] is True
    assert checks["m4_end_to_end_claim_allowed"] is False
    assert checks["test_accessed"] is False
    assert checks["beats_training_performed"] is False
    assert checks["m4_training_performed"] is False


def test_ci_point_matches_unified_v4_lock():
    point = _load()["point"]

    assert point["validation_videos"] == 1717
    assert point["validation_event_queries"] == 4070
    assert point["validation_video_label_queries"] == 2413
    assert point["macro_delta"] > -0.02
    assert point["margin_delta"] > 0
    assert point["raw_correct_preservation_rate"] > 0.99
