#!/usr/bin/env python3
"""独立审计 UnAV v4 到 M4 的单样本 behavior-off/gate0 smoke。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_single_jsonl(path: Path) -> dict:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if len(rows) != 1:
        raise ValueError(f"{path}必须恰好包含1行，实际为{len(rows)}")
    return rows[0]


def audit(behavior_path: Path, gate0_path: Path, frozen_audit_path: Path) -> dict:
    behavior = read_single_jsonl(behavior_path)
    gate0 = read_single_jsonl(gate0_path)
    frozen_rows = json.loads(frozen_audit_path.read_text(encoding="utf-8"))
    diagnostics = gate0.get("as_m4_diagnostics") or []
    behavior_tokens = behavior.get("generation_debug", {}).get("tokens", {})
    gate0_tokens = gate0.get("generation_debug", {}).get("tokens", {})
    audio_input = gate0_tokens.get("audio_input") or {}

    checks = {
        "single_same_sample": behavior.get("sample_id") == gate0.get("sample_id"),
        "non_test_dev_sample": behavior.get("sample_id")
        == "ave_stage2_QSgqjJ2iglM_audio",
        "prediction_exact_match": behavior.get("prediction") == gate0.get("prediction"),
        "generated_tokens_exact_match": behavior_tokens.get("first_20_new_token_ids")
        == gate0_tokens.get("first_20_new_token_ids"),
        "gate0_audio_was_decoded": int(audio_input.get("window_count") or 0) > 0,
        "gate0_audio_norm_positive": float(gate0.get("audio_norm") or 0.0) > 0.0,
        "gate0_scalar_exact_zero": float(gate0.get("gate")) == 0.0,
        "gate0_delta_ratio_exact_zero": float(gate0.get("delta_to_video_ratio")) == 0.0,
        "gate0_raw_delta_ratio_exact_zero": float(gate0.get("raw_delta_to_video_ratio")) == 0.0,
        "gate0_capped_delta_ratio_exact_zero": float(
            gate0.get("capped_delta_to_video_ratio")
        )
        == 0.0,
        "diagnostics_present": bool(diagnostics),
        "all_diagnostic_gates_zero": bool(diagnostics)
        and all(
            float(item.get(name) or 0.0) == 0.0
            for item in diagnostics
            for name in ("gate_mean", "gate_min", "gate_max")
        ),
        "all_diagnostic_deltas_zero": bool(diagnostics)
        and all(
            float(item.get(name) or 0.0) == 0.0
            for item in diagnostics
            for name in (
                "delta_norm",
                "delta_to_video_ratio",
                "raw_delta_norm",
                "raw_delta_to_video_ratio",
                "capped_delta_norm",
                "capped_delta_to_video_ratio",
            )
        ),
        "frozen_runtime_sha_unchanged": bool(frozen_rows)
        and all(item.get("unchanged") is True for item in frozen_rows),
        "logical_cuda_device_is_zero": behavior.get("cuda_device")
        == gate0.get("cuda_device")
        == "cuda:0",
    }
    status = "PASS_V4_M4_GATE0_EXACT_ROLLBACK" if all(checks.values()) else "FAIL_V4_M4_GATE0"
    return {
        "status": status,
        "checks": checks,
        "sample_id": behavior.get("sample_id"),
        "prediction": behavior.get("prediction"),
        "prediction_correct": behavior.get("correct"),
        "generated_token_ids": behavior_tokens.get("first_20_new_token_ids"),
        "gate0_audio_window_count": audio_input.get("window_count"),
        "gate0_audio_norm": gate0.get("audio_norm"),
        "behavior_runtime_seconds": behavior.get("runtime_seconds"),
        "gate0_runtime_seconds": gate0.get("runtime_seconds"),
        "behavior_peak_memory_allocated_bytes": behavior.get(
            "cuda_peak_memory_allocated_bytes"
        ),
        "gate0_peak_memory_allocated_bytes": gate0.get(
            "cuda_peak_memory_allocated_bytes"
        ),
        "behavior_path": str(behavior_path.resolve()),
        "behavior_sha256": sha256_file(behavior_path),
        "gate0_path": str(gate0_path.resolve()),
        "gate0_sha256": sha256_file(gate0_path),
        "frozen_audit_path": str(frozen_audit_path.resolve()),
        "frozen_audit_sha256": sha256_file(frozen_audit_path),
        "physical_gpu": 2,
        "cuda_visible_devices": "2",
        "test_accessed": False,
        "training_performed": False,
        "nonzero_gate_executed": False,
        "m4_effect_claim_allowed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--behavior", type=Path, required=True)
    parser.add_argument("--gate0", type=Path, required=True)
    parser.add_argument("--frozen-audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.behavior, args.gate0, args.frozen_audit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if not result["status"].startswith("PASS"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
