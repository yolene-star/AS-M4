#!/usr/bin/env python3
"""审计 v4→M4 fusion projector 重构的 CPU 合同，不运行模型或训练。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decay-result", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    decay = json.loads(args.decay_result.read_text(encoding="utf-8"))
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    adapter = decay["aggregate"]["m4_adapter"]
    acceptance = contract["acceptance"]
    checks = {
        "adapter_separation_available": (
            adapter["mean_relative_difference"]
            >= acceptance["preserve_adapter_relative_difference_min"]
        ),
        "adapter_not_collapsed": (
            adapter["mean_pair_cosine"] <= acceptance["preserve_adapter_cosine_max"]
        ),
        "source_modules_frozen_required": contract["forbidden"]["m4_training"],
        "gpu_expansion_forbidden": contract["forbidden"]["gpu_expansion"],
        "gate_increase_forbidden": contract["forbidden"]["gate_increase"],
    }
    result = {
        "status": "PASS_CPU_FUSION_RECONSTRUCTION_CONTRACT"
        if all(checks.values())
        else "FAIL_CPU_FUSION_RECONSTRUCTION_CONTRACT",
        "allowed_modes": contract["allowed_modes"],
        "checks": checks,
        "reference_adapter_metrics": adapter,
        "required_next_stage": contract["next_stage_requires"],
        "training_performed": False,
        "gpu_run_performed": False,
        "formal_test_accessed": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
