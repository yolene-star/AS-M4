#!/usr/bin/env python3
"""审计 LLP teacher-forced smoke：区分管线通过与音频选择性证据。"""

from __future__ import annotations

import argparse
import json
import math
import os
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROWS = REPO_ROOT / "intersuit/harness/artifacts/llp_modality_evaluator_v1/gpu_role_stratified_20260920/run/rows.jsonl"
DEFAULT_OUTPUT = REPO_ROOT / "intersuit/harness/artifacts/llp_modality_evaluator_v1/gpu_role_stratified_20260920/run/nll_audit.json"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise ValueError("NLL rows 为空")
    if any(not math.isfinite(float(row["nll"])) for row in rows):
        raise ValueError("NLL 含非有限值")
    grouped: dict[tuple[str, str], dict[str, Any]] = defaultdict(dict)
    for row in rows:
        key = (row["base_id"], row["query_type"])
        if row["condition"] in grouped[key]:
            raise ValueError(f"condition 重复：{key}/{row['condition']}")
        grouped[key][row["condition"]] = row
    required_audio = {"correct_audio", "mismatch_audio", "video_only", "silence"}
    required_visual = {"correct_audio", "mismatch_audio", "video_only"}
    per_group = []
    for (base_id, query_type), conditions in sorted(grouped.items()):
        required = required_audio if query_type == "audio_presence" else required_visual
        if set(conditions) != required:
            raise ValueError(f"条件不完整：{base_id}/{query_type}")
        correct = float(conditions["correct_audio"]["nll"])
        mismatch = float(conditions["mismatch_audio"]["nll"])
        video = float(conditions["video_only"]["nll"])
        silence = float(conditions.get("silence", conditions["video_only"])["nll"])
        per_group.append(
            {
                "base_id": base_id,
                "query_type": query_type,
                "modality_role": conditions["correct_audio"]["modality_role"],
                "correct_minus_mismatch_nll": mismatch - correct,
                "correct_minus_video_only_nll": video - correct,
                "silence_minus_video_only_abs_nll": abs(silence - video),
            }
        )
    positive_audio = [
        item for item in per_group
        if item["query_type"] == "audio_presence" and item["modality_role"] in {"audio_only", "audio_visual"}
    ]
    visual = [item for item in per_group if item["query_type"] == "visual_presence"]
    selectivity_checks = {
        "positive_audio_correct_better_than_mismatch": bool(positive_audio) and all(item["correct_minus_mismatch_nll"] > 0 for item in positive_audio),
        "positive_audio_correct_better_than_video_only": bool(positive_audio) and all(item["correct_minus_video_only_nll"] > 0 for item in positive_audio),
        "silence_video_only_exact_within_float": all(item["silence_minus_video_only_abs_nll"] <= 1e-6 for item in per_group),
        "visual_rows_present": bool(visual),
    }
    return {
        "status": "PASS_LLP_MODALITY_PIPELINE_ONLY_SELECTIVITY_NOT_PROVEN",
        "pipeline_pass": True,
        "selectivity_evidence_pass": all(selectivity_checks.values()),
        "selectivity_checks": selectivity_checks,
        "group_count": len(grouped),
        "row_count": len(rows),
        "positive_audio_group_count": len(positive_audio),
        "mean_positive_audio_correct_minus_mismatch_nll": mean(item["correct_minus_mismatch_nll"] for item in positive_audio) if positive_audio else None,
        "mean_positive_audio_correct_minus_video_only_nll": mean(item["correct_minus_video_only_nll"] for item in positive_audio) if positive_audio else None,
        "mean_silence_video_only_abs_nll": mean(item["silence_minus_video_only_abs_nll"] for item in per_group),
        "per_group": per_group,
        "gpu_effect_claim_allowed": False,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    if os.environ.get("CONDA_DEFAULT_ENV") != "M4":
        raise RuntimeError("必须通过 conda run -n M4 启动")
    result = audit(read_jsonl(args.rows))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=Path, default=DEFAULT_ROWS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


if __name__ == "__main__":
    print(json.dumps(run(parse_args()), ensure_ascii=False, sort_keys=True))
