#!/usr/bin/env python
"""独立重算 adapter-only 四条件 NLL 评估并核对资产 SHA。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from intersuit.train.audio_semantic_m4_adapter import summarize_counterfactual_nll


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def audit_evaluation(*, summary_path: Path, rows_path: Path, runtime_contract_path: Path, manifest_path: Path) -> dict:
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in rows_path.read_text(encoding="utf-8").splitlines() if line]
    recomputed = summarize_counterfactual_nll(rows)
    errors = []
    if sha256_file(rows_path) != summary.get("rows_sha256"):
        errors.append("rows SHA 与 summary 不一致")
    if sha256_file(runtime_contract_path) != summary.get("contract_sha256"):
        errors.append("运行时合同 SHA 与 summary 不一致")
    if sha256_file(manifest_path) != summary.get("manifest_sha256"):
        errors.append("manifest SHA 与 summary 不一致")
    if recomputed != summary.get("metrics"):
        errors.append("独立重算 metrics 与 summary 不一致")
    if summary.get("adapter_sha256_before") != summary.get("adapter_sha256_after"):
        errors.append("只读评估 adapter SHA 前后不一致")
    if summary.get("frozen_runtime_sha256_before") != summary.get("frozen_runtime_sha256_after"):
        errors.append("只读评估冻结模块 SHA 前后不一致")
    return {
        "status": "PASS_ADAPTER_EVAL_INDEPENDENT_AUDIT" if not errors else "FAIL_ADAPTER_EVAL_INDEPENDENT_AUDIT",
        "errors": errors,
        "summary_sha256": sha256_file(summary_path),
        "rows_sha256": sha256_file(rows_path),
        "runtime_contract_sha256": sha256_file(runtime_contract_path),
        "manifest_sha256": sha256_file(manifest_path),
        "recomputed_metrics": recomputed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--rows", type=Path, required=True)
    parser.add_argument("--runtime-contract", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit_evaluation(
        summary_path=args.summary.resolve(),
        rows_path=args.rows.resolve(),
        runtime_contract_path=args.runtime_contract.resolve(),
        manifest_path=args.manifest.resolve(),
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    if not result["status"].startswith("PASS_"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
