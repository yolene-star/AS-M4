"""UnAV S1 canary20独立媒体审计测试。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/audit_m4_ptpa_unav_s1_canary20_v1.py"
SPEC = importlib.util.spec_from_file_location("audit_m4_ptpa_unav_s1_canary20_v1", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def test_independent_audit_verifies_exact_media_set(monkeypatch, tmp_path):
    media_root = tmp_path / "media"
    media_root.mkdir()
    plan = []
    results = []
    for index in range(20):
        video_id = f"video_{index:02d}"
        split = "train" if index < 10 else "validation"
        path = media_root / f"{video_id}.mp4"
        path.write_bytes(video_id.encode())
        plan.append({"physical_media_id": video_id, "official_split": split})
        results.append(
            {
                "physical_media_id": video_id,
                "official_split": split,
                "path": str(path),
                "size": path.stat().st_size,
                "sha256": module.sha256(path),
                "passed": True,
                "audit": {
                    "duration_delta_sec": 0.0,
                    "max_event_offset_sec": 1.0,
                    "media_duration_sec": 2.0,
                    "full_media_decode_passed": True,
                    "audio_decode_16khz_mono_passed": True,
                },
            }
        )
    plan_path = tmp_path / "plan.jsonl"
    results_path = tmp_path / "results.jsonl"
    plan_path.write_text("".join(json.dumps(row) + "\n" for row in plan))
    results_path.write_text("".join(json.dumps(row) + "\n" for row in results))
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "status": "PASS_CANARY20_MEDIA_QUALIFIED_EFFECT_LOCKED",
                "plan_sha256": module.sha256(plan_path),
                "results_sha256": module.sha256(results_path),
                "permissions": {
                    "feature_extraction_allowed": False,
                    "effect_access_allowed": False,
                    "test_media_access_allowed": False,
                    "test_effect_access_allowed": False,
                    "training_allowed": False,
                    "redistribution_allowed": False,
                },
            }
        )
    )

    result = module.audit(plan_path, results_path, summary_path, media_root)

    assert result["status"] == module.PASS_STATUS
    assert result["rows"] == 20
    assert result["split_counts"] == {"train": 10, "validation": 10}
    assert result["media_sha_mismatch_count"] == 0
