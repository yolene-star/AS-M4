"""D1 official test review 的晋级判定测试。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/review_ave_official_d1_test.py"
)
spec = importlib.util.spec_from_file_location(
    "review_ave_official_d1_test_tested",
    SCRIPT,
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
assert spec.loader is not None
spec.loader.exec_module(module)


def test_failed_effect_gate_blocks_d2_even_when_integrity_passes():
    status, allowed = module.evaluate_review_status(
        {"integrity": True},
        {"oracle_hit": True, "gain": False},
    )

    assert status == "review_failed"
    assert allowed is False


def test_all_integrity_and_gate_checks_allow_d2():
    status, allowed = module.evaluate_review_status(
        {"integrity": True},
        {"oracle_hit": True, "gain": True},
    )

    assert status == "review_passed_with_omissions"
    assert allowed is True
