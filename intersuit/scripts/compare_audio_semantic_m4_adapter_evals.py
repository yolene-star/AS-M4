#!/usr/bin/env python
"""比较 adapter-only 训练前后 30 组 NLL，并执行预注册 20-step 门禁。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from intersuit.train.audio_semantic_m4_adapter import evaluate_counterfactual_gate


def compare_evaluations(*, baseline_summary: Path, post_summary: Path, contract_path: Path) -> dict:
    baseline = json.loads(baseline_summary.read_text(encoding="utf-8"))
    post = json.loads(post_summary.read_text(encoding="utf-8"))
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    errors = []
    if baseline.get("evaluation_kind") != "initialization":
        errors.append("baseline evaluation_kind 不是 initialization")
    if post.get("evaluation_kind") != "checkpoint":
        errors.append("post evaluation_kind 不是 checkpoint")
    if baseline.get("manifest_sha256") != post.get("manifest_sha256"):
        errors.append("baseline/post manifest SHA 不一致")
    if int(baseline.get("metrics", {}).get("group_count", -1)) != int(contract["evaluation"]["group_count"]):
        errors.append("baseline group_count 与合同不一致")
    if int(post.get("metrics", {}).get("group_count", -1)) != int(contract["evaluation"]["group_count"]):
        errors.append("post group_count 与合同不一致")
    if errors:
        return {"status": "FAIL_ADAPTER_CANARY20_EVAL_ASSET_AUDIT", "errors": errors}
    gate = evaluate_counterfactual_gate(
        baseline["metrics"], post["metrics"], contract["evaluation"]["thresholds"]
    )
    return {
        "status": gate["status"],
        "errors": [],
        "checks": gate["checks"],
        "comparison": gate["comparison"],
        "baseline": {
            "summary": str(baseline_summary),
            "manifest_sha256": baseline["manifest_sha256"],
            "adapter_sha256": baseline["adapter_sha256_before"],
            "metrics": {key: value for key, value in baseline["metrics"].items() if key != "per_group"},
        },
        "post": {
            "summary": str(post_summary),
            "checkpoint_sha256": post.get("checkpoint_sha256"),
            "adapter_sha256": post["adapter_sha256_before"],
            "metrics": {key: value for key, value in post["metrics"].items() if key != "per_group"},
        },
        "visual_only_question_control": "NOT_EVALUATED",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-summary", type=Path, required=True)
    parser.add_argument("--post-summary", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = compare_evaluations(
        baseline_summary=args.baseline_summary.resolve(),
        post_summary=args.post_summary.resolve(),
        contract_path=args.contract.resolve(),
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
