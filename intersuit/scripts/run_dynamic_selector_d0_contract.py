#!/usr/bin/env python
"""运行 Dynamic Window Selector 的 D0 第一步独立 contract harness。"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import platform

import torch

from intersuit.harness.dynamic_selector_contract import (
    EVENTNESS_POLICY_NAME,
    FIXED_CONTROL_SEEDS,
    LEGACY_MULTISCALE_POLICY_NAME,
    audit_legacy_multiscale_output,
    build_five_seed_mismatch_controls,
    build_five_seed_random_controls,
    module_state_sha256,
    oracle_topk_indices,
    stable_topk_indices,
)
from intersuit.model.streaming_av.dynamic_window_selector import DynamicWindowSelector


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("intersuit/harness/artifacts/dynamic_selector_d0_contract"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now().astimezone()

    features = torch.zeros(1, 8, 4)
    features[0, 2] = torch.tensor([1.0, 0.0, 0.0, 0.0])
    features[0, 5] = torch.tensor([0.0, 1.0, 0.0, 0.0])
    timestamps = torch.tensor([[float(index), float(index + 1)] for index in range(8)])
    windows = torch.zeros(1, 8, 160)
    windows[0, 2] = 0.20
    windows[0, 5] = 0.15
    mask = torch.tensor([[True, True, True, True, True, True, False, False]])

    selector = DynamicWindowSelector(input_dim=4, top_k=4, nms_iou=1.0)
    state_sha_before = module_state_sha256(selector)
    with torch.inference_mode():
        output = selector(features, timestamps, mask, windows)
    state_sha_after = module_state_sha256(selector)
    selector_audit = audit_legacy_multiscale_output(output, timestamps, mask)

    valid_indices = torch.nonzero(mask[0], as_tuple=False).flatten().tolist()
    random_controls = build_five_seed_random_controls(valid_indices, k=3)
    random_controls_repeat = build_five_seed_random_controls(valid_indices, k=3)
    mismatch_ids = tuple(f"sample-{index}" for index in range(6))
    mismatch_controls = build_five_seed_mismatch_controls(mismatch_ids)
    mismatch_controls_repeat = build_five_seed_mismatch_controls(mismatch_ids)

    tie_scores = torch.tensor([0.5, 0.5, 0.1, 0.0])
    tie_timestamps = torch.tensor([[2.0, 3.0], [0.0, 1.0], [1.0, 2.0], [3.0, 4.0]])
    tie_sources = torch.tensor([2, 0, 1, 3])
    tie_mask = torch.ones(4, dtype=torch.bool)
    tie_selection = stable_topk_indices(tie_scores, tie_timestamps, tie_sources, tie_mask, k=1)
    oracle_selection = oracle_topk_indices(
        timestamps,
        torch.arange(8),
        mask[0],
        target_start=2.25,
        target_end=2.75,
        k=3,
    )

    checks = {
        "selector_source_timestamp_audit": selector_audit["status"] == "pass",
        "eventness_is_explicitly_cached": selector_audit["score_name"] == EVENTNESS_POLICY_NAME,
        "legacy_multiscale_name_is_explicit": selector_audit["policy_name"] == LEGACY_MULTISCALE_POLICY_NAME,
        "five_random_seeds_present": tuple(int(seed) for seed in random_controls) == FIXED_CONTROL_SEEDS,
        "five_random_seeds_reproducible": random_controls == random_controls_repeat,
        "five_mismatch_seeds_present": tuple(int(seed) for seed in mismatch_controls) == FIXED_CONTROL_SEEDS,
        "five_mismatch_seeds_reproducible": mismatch_controls == mismatch_controls_repeat,
        "mismatch_is_one_to_one_without_self_pairs": all(
            len(set(mapping.values())) == len(mapping)
            and all(source != target for source, target in mapping.items())
            for mapping in mismatch_controls.values()
        ),
        "earlier_window_wins_ties": tie_selection == [0],
        "oracle_overlap_then_boundary_fill": oracle_selection == [1, 2, 3],
        "module_state_sha_unchanged": state_sha_before == state_sha_after,
        "all_outputs_finite": not selector_audit["errors"],
    }
    status = "pass" if all(checks.values()) else "fail"

    locked_config = {
        "stage": "D0-step-1-contract",
        "dataset": None,
        "device": "cpu",
        "fixed_control_seeds": list(FIXED_CONTROL_SEEDS),
        "eventness_policy_name": EVENTNESS_POLICY_NAME,
        "legacy_policy_name": LEGACY_MULTISCALE_POLICY_NAME,
        "source_index_semantics": "legacy multiscale candidate causal end-anchor micro-window",
        "started_at": started_at.isoformat(),
    }
    summary = {
        "status": status,
        "checks": checks,
        "selector_audit": selector_audit,
        "random_controls": random_controls,
        "mismatch_controls": mismatch_controls,
        "tie_selection": tie_selection,
        "oracle_selection": oracle_selection,
        "module_state_sha_before": state_sha_before,
        "module_state_sha_after": state_sha_after,
        "environment": {
            "conda_default_env": os.environ.get("CONDA_DEFAULT_ENV"),
            "python": platform.python_version(),
            "torch": torch.__version__,
        },
    }
    report = _build_report(locked_config, summary)

    _write_json(output_dir / "locked_config.json", locked_config)
    _write_json(output_dir / "summary.json", summary)
    (output_dir / "report.md").write_text(report, encoding="utf-8")
    (output_dir / "run.started_at").write_text(started_at.isoformat() + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "output_dir": str(output_dir)}, ensure_ascii=False))
    return 0 if status == "pass" else 1


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _build_report(config: dict, summary: dict) -> str:
    checks = "\n".join(
        f"- [{'x' if passed else ' '}] `{name}`"
        for name, passed in summary["checks"].items()
    )
    return f"""# Dynamic Window Selector D0 第一步 Contract 报告

## 结论

本步骤状态：**{summary["status"]}**。

本报告只覆盖 D0 立即执行顺序的第 1 项：独立 contract harness、cached eventness 命名、显式 source index/timestamp 审计，以及五个固定 Random/mismatch seed。它不表示 D0 已退出，也不授权启动 D1。

## 锁定语义

- eventness 策略名：`{config["eventness_policy_name"]}`
- 旧多尺度实现名：`{config["legacy_policy_name"]}`
- source index：旧多尺度候选的 causal end-anchor 原始 micro-window
- 数据集：无（仅合成张量）
- 设备：CPU

## 检查项

{checks}

## 后续边界

下一步才处理“正式选择单位固定为原始 1 秒 micro-window，并将旧多尺度实现降级为历史基线”。在该步骤和剩余 D0 单测、8 条单卡 smoke 全部通过前，不下载或运行任何官方效果数据。
"""


if __name__ == "__main__":
    raise SystemExit(main())
