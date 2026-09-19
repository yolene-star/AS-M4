#!/usr/bin/env python3
"""独立审计 OmniMMI PT 原始 M4 与 AS-M4 behavior-off 配对 smoke。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import torch


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = REPO_ROOT / "intersuit/harness/artifacts/omnimmi_video_regression_v1/pt_limit2_20260919"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def audit_pair(original_dir: Path, as_dir: Path) -> dict[str, Any]:
    original_config = read_json(original_dir / "run_config.json")
    as_config = read_json(as_dir / "run_config.json")
    ignored = {"created_at", "model_path"}
    original_comparable = {key: value for key, value in original_config.items() if key not in ignored}
    as_comparable = {key: value for key, value in as_config.items() if key not in ignored}
    if original_comparable != as_comparable:
        raise ValueError("两组 smoke 的非模型运行配置不一致")
    if original_config["audio_mode"] != "behavior_off" or as_config["audio_mode"] != "behavior_off":
        raise ValueError("配对 smoke 必须均为 behavior_off")
    original_rows = read_jsonl(original_dir / "scores.jsonl")
    as_rows = read_jsonl(as_dir / "scores.jsonl")
    if len(original_rows) != 2 or len(as_rows) != 2:
        raise ValueError("配对 smoke 必须各有 2 条")
    per_sample = []
    exact_prediction_count = 0
    exact_logits_count = 0
    for original, candidate in zip(original_rows, as_rows):
        for key in ("sample_index", "video", "query", "frame_count", "frame_indices"):
            if original[key] != candidate[key]:
                raise ValueError(f"样本输入不一致：{key}")
        original_logits = torch.load(original["logits_path"], map_location="cpu", weights_only=True)["final_logits"].float()
        candidate_logits = torch.load(candidate["logits_path"], map_location="cpu", weights_only=True)["final_logits"].float()
        if original_logits.shape != candidate_logits.shape:
            raise ValueError("logits shape 不一致")
        max_abs_diff = float((original_logits - candidate_logits).abs().max())
        prediction_equal = original["prediction"] == candidate["prediction"]
        exact_prediction_count += int(prediction_equal)
        exact_logits_count += int(max_abs_diff == 0.0)
        per_sample.append(
            {
                "sample_index": original["sample_index"],
                "video": original["video"],
                "prediction_equal": prediction_equal,
                "max_abs_logit_diff": max_abs_diff,
            }
        )
    status = (
        "PASS_OMNIMMI_PT_LIMIT2_EXACT_BEHAVIOR_OFF_ROLLBACK"
        if exact_prediction_count == 2 and exact_logits_count == 2
        else "FAIL_OMNIMMI_PT_LIMIT2_BEHAVIOR_OFF_MISMATCH"
    )
    return {
        "status": status,
        "sample_count": 2,
        "input_contract_equal": True,
        "exact_prediction_count": exact_prediction_count,
        "exact_logits_count": exact_logits_count,
        "per_sample": per_sample,
        "gpu_used": True,
        "physical_gpu": 2,
        "full_pt_pa_authorized": False,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    if os.environ.get("CONDA_DEFAULT_ENV") != "M4":
        raise RuntimeError("必须通过 conda run -n M4 启动")
    result = audit_pair(args.root / "original_m4", args.root / "as_m4_behavior_off")
    output = args.root / "pair_audit.json"
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    return parser.parse_args()


if __name__ == "__main__":
    print(json.dumps(run(parse_args()), ensure_ascii=False, sort_keys=True))
