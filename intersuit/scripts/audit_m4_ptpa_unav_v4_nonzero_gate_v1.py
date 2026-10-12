#!/usr/bin/env python3
"""独立审计 v4 非零 gate 的 behavior/correct/mismatch 极小诊断。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def summarize(rows: list[dict]) -> dict:
    return {
        "rows": len(rows),
        "correct": sum(bool(row.get("correct")) for row in rows),
        "empty_predictions": sum(not str(row.get("prediction") or "").strip() for row in rows),
        "first_token_eos": sum(
            bool(row.get("generation_debug", {}).get("tokens", {}).get("first_token_is_eos"))
            for row in rows
        ),
        "max_delta_to_video_ratio": max(
            (float(row.get("delta_to_video_ratio") or 0.0) for row in rows),
            default=0.0,
        ),
        "min_delta_to_video_ratio": min(
            (float(row.get("delta_to_video_ratio") or 0.0) for row in rows),
            default=0.0,
        ),
        "max_peak_memory_allocated_bytes": max(
            (int(row.get("cuda_peak_memory_allocated_bytes") or 0) for row in rows),
            default=0,
        ),
    }


def audit(behavior_path: Path, correct_path: Path, mismatch_path: Path, frozen_path: Path) -> dict:
    behavior_rows = read_jsonl(behavior_path)
    correct_rows = read_jsonl(correct_path)
    mismatch_rows = read_jsonl(mismatch_path)
    frozen_rows = json.loads(frozen_path.read_text(encoding="utf-8"))
    behavior = {row["sample_id"]: row for row in behavior_rows}
    correct = {row["sample_id"]: row for row in correct_rows}
    mismatch = {row["sample_id"]: row for row in mismatch_rows}
    sample_ids = list(behavior)
    aligned = set(behavior) == set(correct) == set(mismatch) and len(sample_ids) == 10

    correct_changes = [sid for sid in sample_ids if behavior[sid]["prediction"] != correct[sid]["prediction"]]
    mismatch_changes = [sid for sid in sample_ids if behavior[sid]["prediction"] != mismatch[sid]["prediction"]]
    correct_only_improvements = [
        sid
        for sid in sample_ids
        if not behavior[sid]["correct"] and correct[sid]["correct"] and not mismatch[sid]["correct"]
    ]
    mismatch_only_improvements = [
        sid
        for sid in sample_ids
        if not behavior[sid]["correct"] and mismatch[sid]["correct"] and not correct[sid]["correct"]
    ]
    diagnostic_rows = correct_rows + mismatch_rows
    finite = all(
        math.isfinite(float(row.get(name) or 0.0))
        for row in diagnostic_rows
        for name in (
            "gate",
            "audio_norm",
            "delta_to_video_ratio",
            "raw_delta_to_video_ratio",
            "capped_delta_to_video_ratio",
        )
    )
    mismatch_distinct = all(
        row.get("mismatch_source_physical_media_id")
        and row.get("mismatch_source_physical_media_id") != row.get("physical_media_id")
        for row in mismatch_rows
    )
    behavior_summary = summarize(behavior_rows)
    correct_summary = summarize(correct_rows)
    mismatch_summary = summarize(mismatch_rows)
    checks = {
        "ten_rows_per_condition_and_ids_aligned": aligned,
        "no_empty_predictions": all(
            summary["empty_predictions"] == 0
            for summary in (behavior_summary, correct_summary, mismatch_summary)
        ),
        "no_first_token_eos": all(
            summary["first_token_eos"] == 0
            for summary in (behavior_summary, correct_summary, mismatch_summary)
        ),
        "diagnostics_finite": finite,
        "correct_nonzero_delta": correct_summary["min_delta_to_video_ratio"] > 0.0,
        "mismatch_nonzero_delta": mismatch_summary["min_delta_to_video_ratio"] > 0.0,
        "delta_cap_respected": max(
            correct_summary["max_delta_to_video_ratio"],
            mismatch_summary["max_delta_to_video_ratio"],
        )
        <= 0.05,
        "mismatch_uses_distinct_physical_media": mismatch_distinct,
        "frozen_runtime_sha_unchanged": bool(frozen_rows)
        and all(row.get("unchanged") is True for row in frozen_rows),
        "mismatch_unique_improvement_not_above_correct": len(mismatch_only_improvements)
        <= len(correct_only_improvements),
    }
    status = "PASS_V4_NONZERO_GATE_DIAGNOSTIC" if all(checks.values()) else "FAIL_V4_NONZERO_GATE_DIAGNOSTIC"
    paths = {
        "behavior": behavior_path,
        "correct": correct_path,
        "mismatch": mismatch_path,
        "frozen_audit": frozen_path,
    }
    return {
        "status": status,
        "checks": checks,
        "summaries": {
            "behavior": behavior_summary,
            "correct": correct_summary,
            "mismatch": mismatch_summary,
        },
        "prediction_changes_vs_behavior": {
            "correct": correct_changes,
            "mismatch": mismatch_changes,
        },
        "correct_only_improvements": correct_only_improvements,
        "mismatch_only_improvements": mismatch_only_improvements,
        "correct_vs_mismatch_prediction_difference_count": sum(
            correct[sid]["prediction"] != mismatch[sid]["prediction"] for sid in sample_ids
        ),
        "artifacts": {
            name: {"path": str(path.resolve()), "sha256": sha256_file(path)}
            for name, path in paths.items()
        },
        "sample_limit": 10,
        "physical_gpu": 2,
        "gate": 0.005,
        "delta_ratio_cap": 0.05,
        "test_accessed": False,
        "training_performed": False,
        "m4_effect_claim_allowed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--behavior", type=Path, required=True)
    parser.add_argument("--correct", type=Path, required=True)
    parser.add_argument("--mismatch", type=Path, required=True)
    parser.add_argument("--frozen-audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.behavior, args.correct, args.mismatch, args.frozen_audit)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if not result["status"].startswith("PASS"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
