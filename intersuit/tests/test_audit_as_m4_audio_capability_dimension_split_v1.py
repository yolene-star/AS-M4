"""多基准分维度合同的 CPU 定向测试。"""

from __future__ import annotations

import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/audit_as_m4_audio_capability_dimension_split_v1.py"
SPEC = importlib.util.spec_from_file_location(
    "audit_as_m4_audio_capability_dimension_split_v1", SCRIPT
)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def valid_contract(tmp_path: Path) -> dict:
    assets = []
    for name in ("unav.md", "k4a.json", "k8a.json", "k4b.json", "k8b.json", "packet.jsonl", "packet.json"):
        path = tmp_path / name
        path.write_text("{}\n", encoding="utf-8")
        assets.append(str(path))
    manifest = tmp_path / "llp.jsonl"
    rows = [
        {"dataset": "LLP", "split": "val", "sample_id": f"v{i}", "modality_role": role, "event_label": None if role == "background" else "Speech"}
        for i, role in enumerate(("audio_only", "visual_only", "audio_visual", "background"))
    ]
    manifest.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return {
        "contract_id": "test",
        "status": "MAINLINE_FROZEN_DIMENSION_SPLIT_V1",
        "global_rules": {
            "one_test_set_per_dimension": True,
            "cross_dataset_sample_pooling": False,
            "cross_dataset_metric_denominator": False,
            "cross_dataset_composite_score": False,
            "shared_frozen_checkpoint_required": True,
            "parameter_updates_during_evaluation": False,
        },
        "dimensions": [
            {"dimension_id": key, "test_set": {"dataset": dataset, "split": module.EXPECTED_SPLIT_BY_DATASET[dataset]}, "metrics": [f"metric_{index}"]}
            for index, (key, dataset) in enumerate(module.EXPECTED_DATASET_BY_DIMENSION.items())
        ],
        "dataset_gates": {
            "UnAV-100": {"status": "READY_PARTIAL_MATRIX", "required_assets": assets[:5]},
            "LLP/AVVP": {"status": "BLOCKED_EVALUATOR_RECONSTRUCTION", "manifest": str(manifest), "manifest_split": "val", "required_modalities": ["audio_only", "visual_only", "audio_visual", "background"]},
            "OmniMMI": {"status": "PENDING_HUMAN_MODALITY_REVIEW", "required_assets": assets[5:]},
        },
        "authorization": {"cpu_contract_audit": True, "gpu_smoke": False, "gpu_150step": False, "formal_training": False},
    }


def test_valid_dimension_split_contract_passes(tmp_path):
    result = module.audit_contract(valid_contract(tmp_path))
    assert result["status"] == "PASS_CPU_CONTRACT_DATASET_GATES_CLOSED"
    assert result["dimension_count"] == 5
    assert result["llp_validation"]["window_rows"] == 4
    assert result["cross_dataset_composite_score"] is False


def test_rejects_multiple_test_sets_in_one_dimension(tmp_path):
    contract = valid_contract(tmp_path)
    contract["dimensions"][0]["test_sets"] = [contract["dimensions"][0]["test_set"]]
    with pytest.raises(ValueError, match="禁止多测试集字段"):
        module.audit_contract(contract)


def test_rejects_metric_owned_by_two_dimensions(tmp_path):
    contract = valid_contract(tmp_path)
    contract["dimensions"][1]["metrics"] = contract["dimensions"][0]["metrics"]
    with pytest.raises(ValueError, match="指标跨维度重复"):
        module.audit_contract(contract)


def test_rejects_cross_dataset_composite_score(tmp_path):
    contract = deepcopy(valid_contract(tmp_path))
    contract["global_rules"]["cross_dataset_composite_score"] = True
    with pytest.raises(ValueError, match="cross_dataset_composite_score"):
        module.audit_contract(contract)


def test_rejects_wrong_dataset_assignment(tmp_path):
    contract = valid_contract(tmp_path)
    contract["dimensions"][0]["test_set"]["dataset"] = "LLP/AVVP"
    with pytest.raises(ValueError, match="数据集错误"):
        module.audit_contract(contract)
