"""D1 AVE validation 汇总与 fail-closed 退出门禁测试。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/finalize_ave_official_d1_validation.py"
)
spec = importlib.util.spec_from_file_location(
    "finalize_ave_official_d1_validation_tested",
    SCRIPT,
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
assert spec.loader is not None
spec.loader.exec_module(module)


def passing_inputs() -> dict:
    baseline = {
        "status": "validation_baselines_frozen",
        "validation_physical_video_count": 391,
        "local_boundary_physical_video_count": 143,
        "budgets": [2, 4, 8],
        "fixed_random_seeds": [20260720, 20260721, 20260722, 20260723, 20260724],
        "test_accessed": False,
        "metrics": {
            "local_boundary_primary": {
                "oracle_topk|K=4|seed=None": {"window_hit_at_k": 1.0}
            },
            "random_local_boundary_summary": {
                "K=4": {"mean": {"window_hit_at_k": 0.89}}
            },
            "oracle_minus_random_window_hit_paired_bootstrap": {
                "K=4": {
                    "ci95_lower": 0.05,
                    "resamples": 10_000,
                    "seed": 20260724,
                }
            },
        },
    }
    return {
        "d0_summary": {"status": "pass"},
        "manifest_lock": {
            "status": "frozen",
            "audits": {
                "candidate_cross_split_overlap": {
                    "train_validation": 0,
                    "train_test": 0,
                    "validation_test": 0,
                },
                "candidate_historical_overlap": 0,
            },
            "test_seal": {
                "sealed": True,
                "predictions_run": False,
                "effect_results_viewed": False,
            },
        },
        "media_lock": {
            "status": "frozen",
            "counts": {
                "train": {"missing_count": 0},
                "validation": {"missing_count": 0},
                "test": {"missing_count": 0},
            },
            "test_seal": {"sealed": True},
            "effect_evaluation_performed": False,
        },
        "preflight_summary": {
            "status": "pass",
            "sample_count": 391,
            "invalid_count": 0,
            "test_accessed": False,
        },
        "baseline_summary": baseline,
        "eventness_feature_summary": {
            "status": "pass",
            "execution_mode": "extraction",
            "python_environment": "M4",
            "newly_extracted_count": 391,
            "all_fixed_exact_count": 391,
            "audit_pass_count": 391,
            "visible_cuda_device_count": 1,
            "asset_generation_environment": {
                "cuda_visible_devices": "1",
                "python_executable": sys.executable,
            },
            "test_accessed": False,
        },
        "eventness_summary": {
            "status": "pass",
            "validation_physical_video_count": 391,
            "local_boundary_physical_video_count": 143,
            "budgets": [2, 4, 8],
            "all_fixed_equivalence_status": "pass",
            "test_accessed": False,
        },
        "qualification_summary": {
            "status": "omitted_ineligible_asset",
            "formal_eligible": False,
            "qualification_statuses": [
                "diagnostic_failed",
                "schema_incompatible",
                "missing_inputs",
            ],
            "test_accessed": False,
        },
        "baseline_reproduced": True,
        "eventness_reproduced": True,
    }


def test_all_d1_validation_exit_checks_pass_for_frozen_evidence():
    checks = module.build_exit_checks(**passing_inputs())

    assert checks
    assert all(checks.values())


def test_oracle_gain_below_threshold_fails_closed():
    values = passing_inputs()
    values["baseline_summary"]["metrics"]["random_local_boundary_summary"]["K=4"][
        "mean"
    ]["window_hit_at_k"] = 0.91

    checks = module.build_exit_checks(**values)

    assert checks["oracle_gain_over_random_at_least_10pp"] is False


def test_pending_conditional_asset_fails_closed():
    values = passing_inputs()
    values["qualification_summary"]["status"] = "pending"

    checks = module.build_exit_checks(**values)

    assert checks["conditional_asset_resolved"] is False
