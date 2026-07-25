"""D1 AVE official test 一次性入口的授权与冻结协议测试。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/run_ave_official_d1_test_once.py"
)
spec = importlib.util.spec_from_file_location(
    "run_ave_official_d1_test_once_tested",
    SCRIPT,
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
assert spec.loader is not None
spec.loader.exec_module(module)


def test_review_must_explicitly_authorize_test(tmp_path):
    validation = tmp_path / "validation.json"
    runner = tmp_path / "runner.py"
    candidate = tmp_path / "candidate.jsonl"
    media = tmp_path / "media.jsonl"
    validation.write_text("{}\n", encoding="utf-8")
    runner.write_text("# frozen\n", encoding="utf-8")
    candidate.write_text("{}\n", encoding="utf-8")
    media.write_text("{}\n", encoding="utf-8")
    review = {
        "status": "review_failed",
        "test_run_allowed": False,
        "test_accessed": False,
    }

    with pytest.raises(ValueError, match="未授权"):
        module.assert_review_authorized(
            review,
            validation_summary_path=validation,
            runner_path=runner,
            candidate_manifest_path=candidate,
            media_inventory_path=media,
        )


def test_review_freezes_validation_and_runner_sha(tmp_path):
    validation = tmp_path / "validation.json"
    runner = tmp_path / "runner.py"
    candidate = tmp_path / "candidate.jsonl"
    media = tmp_path / "media.jsonl"
    validation.write_text("{}\n", encoding="utf-8")
    runner.write_text("# frozen\n", encoding="utf-8")
    candidate.write_text("{}\n", encoding="utf-8")
    media.write_text("{}\n", encoding="utf-8")
    review = {
        "status": "review_passed_with_omissions",
        "test_run_allowed": True,
        "test_accessed": False,
        "frozen_test_run": {
            "validation_summary_sha256": module.file_sha256(validation),
            "runner_sha256": "wrong",
            "candidate_manifest_sha256": module.file_sha256(candidate),
            "media_inventory_sha256": module.file_sha256(media),
            "budgets": [2, 4, 8],
            "main_budget": 4,
            "conditional_strategies": {
                "sync_margin_cached_topk": "omitted_ineligible_asset"
            },
        },
    }

    with pytest.raises(ValueError, match="runner SHA"):
        module.assert_review_authorized(
            review,
            validation_summary_path=validation,
            runner_path=runner,
            candidate_manifest_path=candidate,
            media_inventory_path=media,
        )


def test_primary_gate_uses_frozen_k4_thresholds():
    metrics = {
        "local_boundary_primary": {
            "oracle_topk|K=4|seed=None": {"window_hit_at_k": 1.0}
        },
        "random_local_boundary_summary": {
            "K=4": {"mean": {"window_hit_at_k": 0.89}}
        },
        "oracle_minus_random_window_hit_paired_bootstrap": {
            "K=4": {
                "ci95_lower": 0.05,
                "ci95_upper": 0.15,
                "resamples": 10_000,
                "seed": 20260724,
            }
        },
    }

    result = module.primary_gate(metrics)

    assert result["status"] == "pass"
    assert result["oracle_minus_random"] == pytest.approx(0.11)
