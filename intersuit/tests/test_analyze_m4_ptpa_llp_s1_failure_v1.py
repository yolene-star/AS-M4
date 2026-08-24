"""LLP S1 failure analysis 的 CPU 合同测试。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/analyze_m4_ptpa_llp_s1_failure_v1.py"
SPEC = importlib.util.spec_from_file_location("analyze_m4_ptpa_llp_s1_failure_v1", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def test_video_equal_mean_does_not_overweight_multilabel_video():
    rows = [
        {"physical_media_id": "a", "value": 1.0},
        {"physical_media_id": "a", "value": 3.0},
        {"physical_media_id": "b", "value": -1.0},
    ]
    assert module.video_equal_mean(rows, "value") == 0.5


def test_permutation_spearman_is_deterministic():
    first = module.permutation_spearman(
        [1, 2, 3, 4, 5], [5, 4, 3, 2, 1], replicates=500, seed=7
    )
    second = module.permutation_spearman(
        [1, 2, 3, 4, 5], [5, 4, 3, 2, 1], replicates=500, seed=7
    )
    assert first == second
    assert first["rho"] == pytest.approx(-1.0)
    assert 0 < first["permutation_p_two_sided"] <= 1


def test_count_bin_obeys_frozen_boundaries():
    bins = {"few": [1, 2], "medium": [3, 5], "head": [6, None]}
    assert module.count_bin(2, bins) == "few"
    assert module.count_bin(3, bins) == "medium"
    assert module.count_bin(20, bins) == "head"


def test_validate_authorization_is_fail_closed(tmp_path):
    lock = tmp_path / "lock.json"
    lock.write_text(
        json.dumps(
            {
                "permissions": {
                    "llp_s1_internal_failure_analysis_allowed": False,
                    "llp_s1_internal_validation_effect_evaluation_allowed": False,
                },
                "datasets": {"llp": {}},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(PermissionError, match="未授权"):
        module.validate_authorization(lock)
