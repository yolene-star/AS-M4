"""D1 validation 阶段 review 结论分类测试。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/review_ave_official_d1_validation.py"
)
spec = importlib.util.spec_from_file_location(
    "review_ave_official_d1_validation_tested",
    SCRIPT,
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
assert spec.loader is not None
spec.loader.exec_module(module)


def sections(status: str = "pass") -> dict:
    return {
        "contract": {"one": {"status": status}},
        "data_and_test": {"one": {"status": "pass"}},
        "metrics_and_fairness": {"one": {"status": "pass"}},
        "runtime_and_artifacts": {"one": {"status": "pass"}},
    }


def test_review_with_resolved_omission_can_unlock_test():
    assert (
        module.review_status(sections(), omitted=True)
        == "review_passed_with_omissions"
    )


def test_any_failed_core_item_blocks_test():
    assert module.review_status(sections("fail"), omitted=True) == "review_failed"
