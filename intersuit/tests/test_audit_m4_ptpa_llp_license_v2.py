from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/audit_m4_ptpa_llp_license_v2.py"
SPEC = importlib.util.spec_from_file_location("audit_m4_ptpa_llp_license_v2", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def test_real_license_review_preserves_media_and_blocks_every_effect_permission():
    rows, review = module.build_outputs()

    assert len(rows) == 150
    assert review["status"] == "blocked_pending_per_item_license_or_institutional_authorization"
    assert review["media_qualified"] == 150
    assert review["license_verified"] == 0
    assert review["license_unverified"] == 150
    assert review["cc_by_verified"] == 0
    assert review["standard_youtube_license_verified"] == 0
    assert review["blockers"] == ["per_item_youtube_license_not_verified"]
    assert review["permissions"] == {
        "media_qualification_preserved": True,
        "feature_extraction_allowed": False,
        "gpu_execution_allowed": False,
        "training_allowed": False,
        "effect_access_allowed": False,
        "redistribution_allowed": False,
    }
    assert review["next_stage_allowed"] is False
    assert all(row["license_status"] == "unverified" for row in rows)
    assert all(row["feature_extraction_allowed"] is False for row in rows)
    assert all(row["redistribution_allowed"] is False for row in rows)


def test_main_writes_ledger_review_and_chinese_report(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--output-root", str(tmp_path)])
    module.main()

    ledger = tmp_path / "llp_pilot150_license_ledger.jsonl"
    review = json.loads((tmp_path / "llp_pilot150_license_review.json").read_text())
    assert ledger.read_text().count("\n") == 150
    assert review["ledger_sha256"] == module.sha256(ledger)
    assert (tmp_path / "LLP_PILOT150_LICENSE_REVIEW_ZH.md").is_file()
