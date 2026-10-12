"""LLP S1 冻结语义上界 evaluator 的 CPU 合同测试。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import torch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/evaluate_m4_ptpa_llp_s1_effect_v1.py"
SPEC = importlib.util.spec_from_file_location("evaluate_m4_ptpa_llp_s1_effect_v1", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def test_stable_order_is_deterministic_and_seeded():
    values = [f"video-{index}" for index in range(20)]
    first = module.stable_order(values, 17, "prototype:Speech")
    assert first == module.stable_order(values, 17, "prototype:Speech")
    assert first != module.stable_order(values, 29, "prototype:Speech")
    assert sorted(first) == sorted(values)


def test_active_indices_use_half_open_overlap():
    timestamps = torch.tensor([[0.0, 1.0], [1.0, 2.0], [2.0, 3.0]])
    assert module.active_indices(timestamps, [(1.0, 2.0)]) == [1]
    assert module.active_indices(timestamps, [(0.5, 1.5)]) == [0, 1]


def test_cluster_bootstrap_is_deterministic_and_clusters_video_rows():
    rows = [
        {"physical_media_id": "a", "margin": 1.0},
        {"physical_media_id": "a", "margin": 3.0},
        {"physical_media_id": "b", "margin": -1.0},
    ]
    first = module.cluster_bootstrap_ci(rows, "margin", seed=9, replicates=1000)
    second = module.cluster_bootstrap_ci(rows, "margin", seed=9, replicates=1000)
    assert first == second
    # 视频 a 先聚合为 2，视频 b 为 -1，两个物理视频等权点估计为 0.5。
    assert first[0] <= 0.5 <= first[1]
    assert module.cluster_mean(rows, "margin") == 0.5


def test_validate_authorization_is_fail_closed(tmp_path):
    lock = tmp_path / "lock.json"
    lock.write_text(
        json.dumps(
            {
                "permissions": {
                    "llp_s1_internal_validation_effect_evaluation_allowed": False
                },
                "datasets": {"llp": {}},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(PermissionError, match="未授权"):
        module.validate_authorization(lock)


def test_build_prototypes_uses_seeded_physical_subsample():
    train = []
    for index in range(8):
        vector = torch.zeros(4)
        vector[index % 4] = 1.0
        train.append(
            {
                "physical_media_id": f"v{index}",
                "weak_labels": {"A"},
                "clip_mean": module.normalize(vector),
                "rms_top5": module.normalize(vector),
            }
        )
    prototypes, selected = module.build_prototypes(
        train, ["A"], "clip_mean", seed=17, fraction=0.75, minimum=3
    )
    assert prototypes.shape == (1, 4)
    assert len(selected["A"]) == 6
    assert torch.isfinite(prototypes).all()
