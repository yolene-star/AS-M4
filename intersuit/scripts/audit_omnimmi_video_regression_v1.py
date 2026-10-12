#!/usr/bin/env python3
"""审计 OmniMMI 仅用于视频能力回归的预注册合同。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONTRACT = REPO_ROOT / "intersuit/harness/configs/omnimmi_video_regression_v1.json"
DEFAULT_OUTPUT = REPO_ROOT / "intersuit/harness/artifacts/omnimmi_video_regression_v1/contract_audit.json"
SHARDS = [f"model-{index:05d}-of-00004.safetensors" for index in range(1, 5)]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def audit_contract(contract: dict[str, Any]) -> dict[str, Any]:
    require(contract.get("purpose") == "detect_video_understanding_regression_only", "OmniMMI 用途错误")
    require(contract.get("manual_modality_labels_required") is False, "不得要求人工模态标签")
    require(contract.get("splits") == ["PT_full_200", "PA_full_200"], "PT/PA full split 未冻结")
    require(
        contract.get("comparison_conditions")
        == ["original_m4", "as_m4_behavior_off_video_only", "as_m4_standard_correct_audio"],
        "比较条件不完整",
    )
    required_invariants = {
        "same_video", "same_question", "same_prompt", "same_frame_sampling",
        "same_decode_parameters", "same_official_evaluator",
    }
    require(set(contract.get("input_invariants", [])) == required_invariants, "同输入约束不完整")
    gates = contract.get("gates", {})
    require(gates.get("behavior_off_exact_prediction_match_required") is True, "behavior off 未要求逐样本一致")
    require(gates.get("behavior_off_metric_delta_abs_max") == 0.0, "behavior off 指标容差必须为 0")
    require(gates.get("audio_improvement_claim_allowed") is False, "OmniMMI 不得用于音频提升宣称")
    margins = gates.get("standard_audio_noninferiority_margin", {})
    expected_margins = {
        "pt_official_accuracy_delta_min": -0.01,
        "pa_accuracy_delta_min": -0.01,
        "pa_precision_delta_min": -0.01,
        "pa_iou_delta_min": -0.01,
        "empty_output_rate_delta_max": 0.005,
    }
    require(margins == expected_margins, "非劣容差未按预注册值冻结")
    auth = contract.get("authorization", {})
    require(auth.get("cpu_contract_audit") is True, "CPU audit 未授权")
    require(auth.get("gpu_smoke") is False, "完成后必须重新关闭 GPU smoke")
    require(
        auth.get("completed_gpu_smoke")
        == {
            "physical_gpu": 2,
            "benchmark": "PT",
            "limit": 2,
            "conditions": ["original_m4", "as_m4_behavior_off_video_only"],
            "status": "PASS_OMNIMMI_PT_LIMIT2_EXACT_BEHAVIOR_OFF_ROLLBACK",
            "evidence": "intersuit/harness/artifacts/omnimmi_video_regression_v1/pt_limit2_20260919/pair_audit.json",
        },
        "已完成 GPU smoke 证据未精确冻结",
    )
    for key in ("full_pt_pa", "training"):
        require(auth.get(key) is False, f"当前阶段必须关闭：{key}")

    models = contract["models"]
    original = resolve_path(models["original_m4"])
    as_m4 = resolve_path(models["as_m4"])
    version_manifest = resolve_path(models["as_m4_model_version_manifest"])
    require(original.is_dir() and as_m4.is_dir(), "模型目录缺失")
    require(version_manifest.is_file(), "AS-M4 model version manifest 缺失")
    shard_links: dict[str, str] = {}
    for shard in SHARDS:
        original_shard = (original / shard).resolve()
        as_shard = as_m4 / shard
        require(original_shard.is_file(), f"原始 M4 shard 缺失：{shard}")
        require(as_shard.is_symlink(), f"AS-M4 shard 必须软链接复用原始基座：{shard}")
        require(as_shard.resolve() == original_shard, f"AS-M4 shard 未指向原始 M4：{shard}")
        shard_links[shard] = str(as_shard.resolve())
    baseline = contract["baseline_metrics"]
    require(baseline["pt_official_accuracy"] == 0.57, "PT 官方基线错误")
    require(baseline["pt_offline_locked_accuracy_diagnostic_only"] == 0.63, "PT 诊断基线错误")
    require(baseline["pa_accuracy"] == 0.215, "PA Accuracy 基线错误")
    require(baseline["pa_precision"] == 0.3182, "PA Precision 基线错误")
    require(baseline["pa_iou"] == 0.1402, "PA IoU 基线错误")
    return {
        "status": "PASS_OMNIMMI_VIDEO_REGRESSION_LIMIT2_SMOKE_FULL_CLOSED",
        "manual_modality_labels_required": False,
        "comparison_conditions": contract["comparison_conditions"],
        "baseline_metrics": baseline,
        "noninferiority_margin": margins,
        "base_shards_reused_exactly": True,
        "shard_links": shard_links,
        "model_version_manifest_sha256": sha256(version_manifest),
        "gpu_authorized": False,
        "completed_gpu_smoke": auth["completed_gpu_smoke"],
        "full_pt_pa_authorized": False,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    if os.environ.get("CONDA_DEFAULT_ENV") != "M4":
        raise RuntimeError("必须通过 conda run -n M4 启动")
    result = audit_contract(read_json(args.contract))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


if __name__ == "__main__":
    print(json.dumps(run(parse_args()), ensure_ascii=False, sort_keys=True))
