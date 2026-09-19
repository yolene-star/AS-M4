#!/usr/bin/env python3
"""审计 AS-M4 多基准分维度合同，禁止跨数据集混合评分。"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONTRACT = (
    REPO_ROOT / "intersuit/harness/configs/as_m4_audio_capability_dimension_split_v1.json"
)
DEFAULT_OUTPUT = (
    REPO_ROOT
    / "intersuit/harness/artifacts/as_m4_audio_capability_dimension_split_v1/audit.json"
)

EXPECTED_DATASET_BY_DIMENSION = {
    "temporal_event_localization": "UnAV-100",
    "budgeted_window_selection": "UnAV-100",
    "modality_dependency": "LLP/AVVP",
    "audio_selectivity_robustness": "LLP/AVVP",
    "downstream_task_utility": "OmniMMI",
}

EXPECTED_SPLIT_BY_DATASET = {
    "UnAV-100": "validation",
    "LLP/AVVP": "validation",
    "OmniMMI": "pt_pa_reviewed_subset",
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def audit_contract(contract: dict[str, Any]) -> dict[str, Any]:
    require(
        contract.get("status") == "MAINLINE_FROZEN_DIMENSION_SPLIT_V1",
        "合同状态不是 MAINLINE_FROZEN_DIMENSION_SPLIT_V1",
    )
    rules = contract.get("global_rules", {})
    require(rules.get("one_test_set_per_dimension") is True, "未启用单维度单测试集")
    for key in (
        "cross_dataset_sample_pooling",
        "cross_dataset_metric_denominator",
        "cross_dataset_composite_score",
        "parameter_updates_during_evaluation",
    ):
        require(rules.get(key) is False, f"危险全局规则必须为 false：{key}")
    require(
        rules.get("shared_frozen_checkpoint_required") is True,
        "未要求所有维度共享冻结 checkpoint",
    )

    dimensions = contract.get("dimensions")
    require(isinstance(dimensions, list) and dimensions, "dimensions 必须为非空列表")
    require(len(dimensions) == len(EXPECTED_DATASET_BY_DIMENSION), "维度数量不匹配")

    dimension_ids: set[str] = set()
    metric_owner: dict[str, str] = {}
    assignments: dict[str, dict[str, Any]] = {}
    for item in dimensions:
        dimension_id = item.get("dimension_id")
        require(dimension_id in EXPECTED_DATASET_BY_DIMENSION, f"未知维度：{dimension_id}")
        require(dimension_id not in dimension_ids, f"重复维度：{dimension_id}")
        dimension_ids.add(dimension_id)
        require("test_sets" not in item and "datasets" not in item, f"{dimension_id} 禁止多测试集字段")
        test_set = item.get("test_set")
        require(isinstance(test_set, dict), f"{dimension_id} 缺少唯一 test_set")
        require(set(test_set) == {"dataset", "split"}, f"{dimension_id} test_set 字段不冻结")
        dataset = test_set["dataset"]
        split = test_set["split"]
        require(
            dataset == EXPECTED_DATASET_BY_DIMENSION[dimension_id],
            f"{dimension_id} 数据集错误：{dataset}",
        )
        require(split == EXPECTED_SPLIT_BY_DATASET[dataset], f"{dimension_id} split 错误：{split}")
        metrics = item.get("metrics")
        require(isinstance(metrics, list) and metrics, f"{dimension_id} metrics 为空")
        require(len(metrics) == len(set(metrics)), f"{dimension_id} 内部指标重复")
        for metric in metrics:
            require(metric not in metric_owner, f"指标跨维度重复：{metric}")
            metric_owner[metric] = dimension_id
        assignments[dimension_id] = {"dataset": dataset, "split": split, "metrics": metrics}

    require(dimension_ids == set(EXPECTED_DATASET_BY_DIMENSION), "维度集合不完整")

    gates = contract.get("dataset_gates", {})
    require(set(gates) == set(EXPECTED_SPLIT_BY_DATASET), "dataset_gates 集合不匹配")
    missing_assets: list[str] = []
    for dataset, gate in gates.items():
        for asset in gate.get("required_assets", []):
            if not resolve_path(asset).is_file():
                missing_assets.append(f"{dataset}:{asset}")
    require(not missing_assets, "缺少冻结资产：" + ", ".join(missing_assets))

    llp_gate = gates["LLP/AVVP"]
    llp_manifest = resolve_path(llp_gate["manifest"])
    require(llp_manifest.is_file(), f"LLP manifest 不存在：{llp_manifest}")
    require(llp_gate.get("manifest_split") == "val", "LLP validation 源 split 必须显式锁定为 val")
    llp_rows = [
        row
        for row in read_jsonl(llp_manifest)
        if row.get("dataset") == "LLP" and row.get("split") == llp_gate["manifest_split"]
    ]
    require(llp_rows, "LLP validation 窗口为空")
    modality_counts = Counter(row.get("modality_role") for row in llp_rows)
    required_modalities = set(llp_gate.get("required_modalities", []))
    require(required_modalities <= set(modality_counts), "LLP validation 缺少必需模态")
    require(all(modality_counts[name] > 0 for name in required_modalities), "LLP 模态窗口为空")
    require(
        all(row.get("event_label") is not None for row in llp_rows if row.get("modality_role") != "background"),
        "LLP 非背景窗口缺少 event_label",
    )

    authorization = contract.get("authorization", {})
    require(authorization.get("cpu_contract_audit") is True, "CPU 合同审计未授权")
    for key in ("gpu_smoke", "gpu_150step", "formal_training"):
        require(authorization.get(key) is False, f"当前阶段必须关闭：{key}")

    return {
        "status": "PASS_CPU_CONTRACT_DATASET_GATES_CLOSED",
        "contract_id": contract["contract_id"],
        "dimension_count": len(dimensions),
        "metric_count": len(metric_owner),
        "assignments": assignments,
        "llp_validation": {
            "manifest_split": llp_gate["manifest_split"],
            "window_rows": len(llp_rows),
            "physical_media": len({row["sample_id"] for row in llp_rows}),
            "modality_counts": dict(sorted(modality_counts.items())),
        },
        "dataset_gate_status": {dataset: gate["status"] for dataset, gate in gates.items()},
        "cross_dataset_composite_score": False,
        "gpu_authorized": False,
        "training_authorized": False,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    if os.environ.get("CONDA_DEFAULT_ENV") != "M4":
        raise RuntimeError("必须通过 conda run -n M4 启动")
    contract = read_json(args.contract)
    result = audit_contract(contract)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


if __name__ == "__main__":
    audit = run(parse_args())
    print(json.dumps(audit, ensure_ascii=False, sort_keys=True))
