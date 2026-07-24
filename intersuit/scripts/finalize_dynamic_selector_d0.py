#!/usr/bin/env python
"""汇总 D0 contract、CPU 单测、8 条单卡 smoke 与内核检查。"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET


KERNEL_ERROR_PATTERN = re.compile(
    r"NVRM.*Xid|Xid \(|PCIe Bus Error|GPU has fallen|hung task|AER:.*(error|fatal)",
    re.IGNORECASE,
)
REQUIRED_CPU_CONTRACT_TESTS = frozenset(
    {
        "test_validated_cache_rejects_miss_duplicate_corruption_and_input_sha_change",
        "test_validated_cache_rejects_corrupt_manifest",
        "test_all_padding_returns_empty_finite_output",
        "test_strict_causal_replay_preserves_absolute_indices_and_ignores_future_perturbation",
        "test_enable_scene_audio_zero_uses_existing_fixed_fallback",
        "test_all_micro_windows_exactly_match_production_fixed_encoder_path_and_parameter_sha",
    }
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=Path("intersuit/harness/artifacts/dynamic_selector_d0_contract"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    artifact_dir = args.artifact_dir.resolve()
    contract_path = artifact_dir / "summary.json"
    cpu_xml_path = artifact_dir / "d0_cpu_tests.xml"
    smoke_path = artifact_dir / "gpu_smokes" / "smoke_summary.json"
    kernel_path = artifact_dir / "gpu_smokes" / "kernel_since_smoke.log"

    contract = _read_json(contract_path)
    smoke = _read_json(smoke_path)
    cpu = _parse_junit(cpu_xml_path)
    kernel_text = kernel_path.read_text(encoding="utf-8", errors="replace")
    kernel_matches = [
        line for line in kernel_text.splitlines() if KERNEL_ERROR_PATTERN.search(line)
    ]
    kernel = {
        "status": "pass" if not kernel_matches else "fail",
        "source": "journalctl -k --since smoke_started_at",
        "log_path": str(kernel_path),
        "matching_error_lines": kernel_matches,
        "visible_log_lines": len(kernel_text.splitlines()),
        "note": (
            "smoke 时间段无可见内核日志条目"
            if "-- No entries --" in kernel_text
            else "已筛查可见内核日志"
        ),
    }
    checks = {
        "contract_harness_passed": contract.get("status") == "pass",
        "cpu_unit_tests_passed": cpu["failures"] == 0 and cpu["errors"] == 0,
        "cpu_unit_test_count_nonzero": cpu["tests"] > 0,
        "required_cpu_contract_tests_present": not cpu["missing_required_tests"],
        "exactly_eight_gpu_smokes_completed": (
            smoke.get("completed_smoke_count") == 8
            and smoke.get("expected_smoke_count") == 8
        ),
        "all_eight_gpu_smokes_passed": (
            smoke.get("status") == "pass"
            and smoke.get("passed_smoke_count") == 8
        ),
        "single_visible_gpu_enforced": smoke.get("matrix", {}).get("visible_gpu_count") == 1,
        "strict_causal_future_perturbation_smoke_passed": _smoke_passed(
            smoke,
            "07_strict_causal_future_perturbation",
        ),
        "gate0_rollback_smoke_passed": _smoke_passed(smoke, "08_gate0_rollback"),
        "post_smoke_kernel_check_passed": kernel["status"] == "pass",
    }
    status = "pass" if all(checks.values()) else "fail"
    summary = {
        "stage": "D0-exit",
        "status": status,
        "checks": checks,
        "contract": {
            "status": contract.get("status"),
            "check_count": len(contract.get("checks", {})),
            "summary_sha256": _file_sha256(contract_path),
        },
        "cpu_tests": cpu,
        "gpu_smokes": {
            "status": smoke.get("status"),
            "passed": smoke.get("passed_smoke_count"),
            "expected": smoke.get("expected_smoke_count"),
            "physical_gpu": smoke.get("matrix", {}).get("cuda_visible_devices"),
            "logical_device": smoke.get("matrix", {}).get("logical_device"),
            "device_name": smoke.get("matrix", {}).get("device_name"),
            "summary_sha256": _file_sha256(smoke_path),
        },
        "kernel_check": kernel,
        "finalized_at": datetime.now().astimezone().isoformat(),
    }
    _write_json(artifact_dir / "d0_exit_summary.json", summary)
    (artifact_dir / "d0_exit_report.md").write_text(
        _build_report(summary),
        encoding="utf-8",
    )
    print(json.dumps({"status": status, "checks": checks}, ensure_ascii=False))
    return 0 if status == "pass" else 1


def _parse_junit(path: Path) -> dict:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    test_names = {
        testcase.attrib.get("name", "")
        for suite in suites
        for testcase in suite.findall(".//testcase")
    }
    return {
        "status": "pass"
        if sum(int(suite.attrib.get("failures", 0)) for suite in suites) == 0
        and sum(int(suite.attrib.get("errors", 0)) for suite in suites) == 0
        else "fail",
        "tests": sum(int(suite.attrib.get("tests", 0)) for suite in suites),
        "failures": sum(int(suite.attrib.get("failures", 0)) for suite in suites),
        "errors": sum(int(suite.attrib.get("errors", 0)) for suite in suites),
        "skipped": sum(int(suite.attrib.get("skipped", 0)) for suite in suites),
        "time_seconds": sum(float(suite.attrib.get("time", 0.0)) for suite in suites),
        "junit_path": str(path),
        "junit_sha256": _file_sha256(path),
        "required_test_count": len(REQUIRED_CPU_CONTRACT_TESTS),
        "missing_required_tests": sorted(REQUIRED_CPU_CONTRACT_TESTS - test_names),
    }


def _smoke_passed(summary: dict, smoke_id: str) -> bool:
    return any(
        row.get("id") == smoke_id and row.get("status") == "pass"
        for row in summary.get("results", [])
    )


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _build_report(summary: dict) -> str:
    checks = "\n".join(
        f"- [{'x' if passed else ' '}] `{name}`"
        for name, passed in summary["checks"].items()
    )
    return f"""# Dynamic Window Selector D0 退出报告

## 结论

D0 状态：**{summary["status"]}**。

本阶段只使用合成张量和固定代码回归输入，没有下载或运行任何官方效果数据。

## 验证结果

- 独立 contract harness：{summary["contract"]["status"]}
- CPU 单元测试：{summary["cpu_tests"]["tests"]} 项，失败 {summary["cpu_tests"]["failures"]}，错误 {summary["cpu_tests"]["errors"]}
- 必需 CPU 合同测试：{summary["cpu_tests"]["required_test_count"]} 项，缺失 {len(summary["cpu_tests"]["missing_required_tests"])}
- 单卡 smoke：{summary["gpu_smokes"]["passed"]}/{summary["gpu_smokes"]["expected"]}
- 物理 GPU：{summary["gpu_smokes"]["physical_gpu"]}
- 逻辑设备：{summary["gpu_smokes"]["logical_device"]}
- GPU 型号：{summary["gpu_smokes"]["device_name"]}
- 内核异常筛查：{summary["kernel_check"]["status"]}（{summary["kernel_check"]["note"]}）

## D0 退出检查

{checks}

## 阶段边界

只有本报告状态为 `pass` 时才允许进入 D1。下一步只能生成 AVE 官方物理视频级 train/validation/test 候选清单并执行污染审计；不得提前下载 validation 媒体、解封 test 或运行效果预测。
"""


if __name__ == "__main__":
    raise SystemExit(main())
