#!/usr/bin/env python
"""只读 review 一次性 D1 AVE official test，并决定是否允许进入 D2。"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
STAGE_ROOT = (
    REPO_ROOT
    / "intersuit/harness/artifacts/dynamic_selector_d1_ave_official"
)
DEFAULT_TEST_ROOT = STAGE_ROOT / "official_test"
KERNEL_ERROR_PATTERNS = (
    "nvrm",
    "xid",
    "aer",
    "pcie bus error",
    "gpu has fallen",
    "hung task",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--validation-review",
        type=Path,
        default=STAGE_ROOT / "validation_review_summary.json",
    )
    parser.add_argument(
        "--test-summary",
        type=Path,
        default=DEFAULT_TEST_ROOT / "summary.json",
    )
    parser.add_argument(
        "--execution-lock",
        type=Path,
        default=DEFAULT_TEST_ROOT / "execution_lock.json",
    )
    parser.add_argument(
        "--kernel-log",
        type=Path,
        default=DEFAULT_TEST_ROOT / "kernel_since_test.log",
    )
    parser.add_argument(
        "--gpu-power-snapshot",
        type=Path,
        default=DEFAULT_TEST_ROOT / "gpu_power_snapshot.csv",
    )
    parser.add_argument(
        "--test-runner",
        type=Path,
        default=REPO_ROOT / "intersuit/scripts/run_ave_official_d1_test_once.py",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_TEST_ROOT)
    return parser.parse_args()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层必须为 object：{path}")
    return value


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def git_lines(*args: str) -> list[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def evaluate_review_status(
    integrity_checks: dict[str, bool],
    gate_checks: dict[str, bool],
) -> tuple[str, bool]:
    if not all(integrity_checks.values()) or not all(gate_checks.values()):
        return "review_failed", False
    return "review_passed_with_omissions", True


def build_report(summary: dict[str, Any]) -> str:
    gate = summary["primary_gate"]
    lines = [
        "# Dynamic Window Selector D1 AVE official test 阶段 Review",
        "",
        "## 结论",
        "",
        f"状态：**{summary['status']}**。",
        "",
        f"- D2 run allowed：`{str(summary['d2_run_allowed']).lower()}`",
        "- official test 已完成一次，重跑仍为禁止。",
        "- test 物理视频：394；局部边界视频：140。",
        f"- Oracle Hit@4：{gate['oracle_window_hit_at_k']:.6f}",
        f"- Random 五 seed 均值：{gate['random_mean_window_hit_at_k']:.6f}",
        f"- Oracle - Random：{gate['oracle_minus_random']:.6f}",
        f"- paired bootstrap 95% CI：[{gate['ci95_lower']:.6f}, {gate['ci95_upper']:.6f}]",
        "",
        "## 完整性检查",
        "",
    ]
    for name, passed in summary["integrity_checks"].items():
        lines.append(f"- [{'PASS' if passed else 'FAIL'}] `{name}`")
    lines.extend(["", "## 预注册效果门禁", ""])
    for name, passed in summary["gate_checks"].items():
        lines.append(f"- [{'PASS' if passed else 'FAIL'}] `{name}`")
    lines.extend(
        [
            "",
            "## 决策",
            "",
            "Oracle 相对 Random 的提升为 8.8571 个百分点，未达到冻结的 10 个百分点。",
            "按计划 D1 失败后只能修窗口、mask、索引、cache 或回退；本次没有这类",
            "contract/runtime 故障，因此不得调 K、阈值或重跑 test，也不得进入 D2–D4。",
            "",
        ]
    )
    return "\n".join(lines)


def run(args: argparse.Namespace) -> dict[str, Any]:
    validation_review_path = args.validation_review.resolve()
    test_summary_path = args.test_summary.resolve()
    execution_lock_path = args.execution_lock.resolve()
    kernel_log_path = args.kernel_log.resolve()
    power_path = args.gpu_power_snapshot.resolve()
    runner_path = args.test_runner.resolve()
    output_dir = args.output_dir.resolve()

    validation_review = load_json(validation_review_path)
    test_summary = load_json(test_summary_path)
    execution_lock = load_json(execution_lock_path)
    outputs = test_summary.get("outputs", {})
    baseline_records_path = Path(outputs["baseline_records"]["path"])
    eventness_manifest_path = Path(outputs["eventness_feature_manifest"]["path"])
    eventness_records_path = Path(outputs["eventness_records"]["path"])
    baseline_rows = load_jsonl(baseline_records_path)
    eventness_manifest = load_jsonl(eventness_manifest_path)
    eventness_rows = load_jsonl(eventness_records_path)
    kernel_text = kernel_log_path.read_text(encoding="utf-8", errors="replace")
    kernel_matches = [
        line
        for line in kernel_text.splitlines()
        if any(pattern in line.lower() for pattern in KERNEL_ERROR_PATTERNS)
    ]
    power_lines = [
        line
        for line in power_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    tracked_runtime_assets = git_lines(
        "ls-files",
        "intersuit/harness/artifacts",
        "intersuit/checkpoints",
        "intersuit/datasets/AVE_OFFICIAL_D1_SOURCE",
    )
    staged_paths = git_lines("diff", "--cached", "--name-only")
    runner_sha = file_sha256(runner_path)
    primary_gate = test_summary["primary_gate"]
    integrity_checks = {
        "validation_review_authorized_exact_runner": (
            validation_review.get("status") == "review_passed_with_omissions"
            and validation_review.get("test_run_allowed") is True
            and validation_review.get("frozen_test_run", {}).get("runner_sha256")
            == runner_sha
            == execution_lock.get("runner_sha256")
        ),
        "one_time_execution_completed": execution_lock.get("status") == "completed"
        and execution_lock.get("one_time_run") is True
        and execution_lock.get("rerun_allowed") is False,
        "test_split_complete": test_summary.get("test_physical_video_count") == 394
        and test_summary.get("local_boundary_physical_video_count") == 140,
        "all_fixed_exact": test_summary.get("all_fixed_exact_count") == 394,
        "records_complete": len(baseline_rows) == 394 * 28
        and len(eventness_manifest) == 394
        and len(eventness_rows) == 394 * 3,
        "records_are_test_only": all(
            row.get("official_split") == "test"
            and row.get("test_accessed") is True
            for row in baseline_rows + eventness_rows
        )
        and all(
            row.get("official_split") == "test"
            and row.get("test_accessed") is True
            for row in eventness_manifest
        ),
        "output_sha_verified": (
            file_sha256(baseline_records_path)
            == outputs["baseline_records"]["sha256"]
            and file_sha256(eventness_manifest_path)
            == outputs["eventness_feature_manifest"]["sha256"]
            and file_sha256(eventness_records_path)
            == outputs["eventness_records"]["sha256"]
        ),
        "conditional_asset_omitted": test_summary.get(
            "conditional_strategies"
        )
        == {"sync_margin_cached_topk": "omitted_ineligible_asset"},
        "explicit_m4_single_gpu_recorded": (
            test_summary.get("python_executable") == sys.executable
            and test_summary.get("process_environment", {}).get(
                "cuda_visible_devices"
            )
            == "1"
            and test_summary.get("process_environment", {}).get(
                "visible_cuda_device_count"
            )
            == 1
        ),
        "kernel_error_check_clean": not kernel_matches,
        "gpu_power_recorded": len(power_lines) == 5
        and all("450.00 W" in line for line in power_lines),
        "runtime_assets_not_tracked_or_staged": not tracked_runtime_assets
        and not staged_paths,
    }
    gate_checks = {
        name: bool(value) for name, value in primary_gate["checks"].items()
    }
    status, d2_allowed = evaluate_review_status(integrity_checks, gate_checks)
    summary = {
        "stage": "D1-AVE-official-test-review",
        "status": status,
        "reviewed_at": datetime.now().astimezone().isoformat(),
        "python_environment": "M4",
        "python_executable": sys.executable,
        "integrity_checks": integrity_checks,
        "gate_checks": gate_checks,
        "primary_gate": primary_gate,
        "evidence": {
            "validation_review_sha256": file_sha256(validation_review_path),
            "test_summary_sha256": file_sha256(test_summary_path),
            "execution_lock_sha256": file_sha256(execution_lock_path),
            "runner_sha256": runner_sha,
            "kernel_error_lines": kernel_matches,
            "gpu_power_snapshot": power_lines,
            "tracked_runtime_assets": tracked_runtime_assets,
            "staged_paths": staged_paths,
        },
        "test_accessed": True,
        "test_rerun_allowed": False,
        "d2_run_allowed": d2_allowed,
        "next_required_step": (
            "prepare_D2_LongVALE" if d2_allowed else "stop_selector_main_chain_at_D1"
        ),
    }
    write_json(output_dir / "review_summary.json", summary)
    (output_dir / "review.md").write_text(
        build_report(summary),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": status,
                "d2_run_allowed": d2_allowed,
                "test_rerun_allowed": False,
            },
            ensure_ascii=False,
        )
    )
    return summary


def main() -> int:
    result = run(parse_args())
    return 0 if result["d2_run_allowed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
