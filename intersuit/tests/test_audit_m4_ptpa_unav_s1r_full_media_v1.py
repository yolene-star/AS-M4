"""UnAV S1-R全量媒体独立审计基本合同测试。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/audit_m4_ptpa_unav_s1r_full_media_v1.py"
SPEC = importlib.util.spec_from_file_location("audit_m4_ptpa_unav_s1r_full_media_v1", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def test_pass_status_records_rejected_media_without_promoting_strict_full_pass():
    assert module.PASS_STATUS == "PASS_INDEPENDENT_AUDIT_WITH_2_MEDIA_REJECTED_EFFECT_LOCKED"
    assert "QUALIFIED" not in module.PASS_STATUS.split("WITH")[0]


def test_default_outputs_keep_qualified_and_rejected_manifests_separate():
    assert module.DEFAULT_QUALIFIED.name == "qualified_media_manifest.jsonl"
    assert module.DEFAULT_REJECTED.name == "rejected_media_manifest.jsonl"
    assert module.DEFAULT_QUALIFIED != module.DEFAULT_REJECTED
