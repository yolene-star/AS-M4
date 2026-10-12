#!/usr/bin/env python
"""按诊断计划第 14 节只读 review D1 AVE validation 并冻结 test 入口。"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any
import xml.etree.ElementTree as ET


REPO_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_ROOT = REPO_ROOT / "intersuit/harness/artifacts"
DEFAULT_STAGE_ROOT = ARTIFACT_ROOT / "dynamic_selector_d1_ave_official"
KERNEL_ERROR_PATTERNS = (
    "nvrm",
    "xid",
    "aer",
    "pcie bus error",
    "gpu has fallen",
    "hung task",
)
ALLOWED_DIFF_PATHS = {
    "intersuit/scripts/audit_dynamic_selector_temporal_asset.py",
    "intersuit/scripts/build_ave_hf_frozen_offset_runtime_bundle.py",
    "intersuit/scripts/evaluate_ave_official_d1_validation_eventness.py",
    "intersuit/scripts/extract_ave_official_d1_validation_eventness.py",
    "intersuit/scripts/finalize_ave_official_d1_validation.py",
    "intersuit/scripts/review_ave_official_d1_validation.py",
    "intersuit/scripts/run_ave_official_d1_test_once.py",
    "intersuit/scripts/run_ave_official_d1_validation_baselines.py",
    "intersuit/tests/test_audit_dynamic_selector_temporal_asset.py",
    "intersuit/tests/test_build_ave_hf_frozen_offset_runtime_bundle.py",
    "intersuit/tests/test_finalize_ave_official_d1_validation.py",
    "intersuit/tests/test_review_ave_official_d1_validation.py",
    "intersuit/tests/test_run_ave_official_d1_test_once.py",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--validation-summary",
        type=Path,
        default=DEFAULT_STAGE_ROOT / "validation_summary.json",
    )
    parser.add_argument(
        "--asset-qualification",
        type=Path,
        default=DEFAULT_STAGE_ROOT / "asset_qualification.json",
    )
    parser.add_argument(
        "--asset-registry",
        type=Path,
        default=DEFAULT_STAGE_ROOT / "asset_registry.json",
    )
    parser.add_argument(
        "--test-junit",
        type=Path,
        default=DEFAULT_STAGE_ROOT / "validation_review_tests.xml",
    )
    parser.add_argument(
        "--eventness-kernel-log",
        type=Path,
        default=ARTIFACT_ROOT
        / (
            "dynamic_selector_d1_ave_validation_eventness_v2/"
            "kernel_since_extraction.log"
        ),
    )
    parser.add_argument(
        "--gpu-power-snapshot",
        type=Path,
        default=ARTIFACT_ROOT
        / (
            "dynamic_selector_d1_ave_validation_eventness_v2/"
            "gpu_power_snapshot.csv"
        ),
    )
    parser.add_argument(
        "--test-runner",
        type=Path,
        default=REPO_ROOT / "intersuit/scripts/run_ave_official_d1_test_once.py",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_STAGE_ROOT)
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


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def parse_junit(path: Path) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    values = {
        "tests": sum(int(suite.attrib.get("tests", 0)) for suite in suites),
        "failures": sum(int(suite.attrib.get("failures", 0)) for suite in suites),
        "errors": sum(int(suite.attrib.get("errors", 0)) for suite in suites),
        "skipped": sum(int(suite.attrib.get("skipped", 0)) for suite in suites),
    }
    values["status"] = (
        "pass"
        if values["tests"] > 0
        and values["failures"] == 0
        and values["errors"] == 0
        else "fail"
    )
    values["path"] = str(path)
    values["sha256"] = file_sha256(path)
    return values


def git_lines(*args: str) -> list[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def verify_frozen_inputs(validation_summary: dict[str, Any]) -> list[str]:
    changed = []
    for name, record in validation_summary.get("inputs", {}).items():
        path = Path(record["path"])
        if not path.is_file() or file_sha256(path) != record["sha256"]:
            changed.append(name)
    return changed


def status_item(passed: bool, evidence: Any, note: str = "") -> dict[str, Any]:
    return {
        "status": "pass" if passed else "fail",
        "evidence": evidence,
        "note": note,
    }


def review_status(sections: dict[str, dict[str, dict]], *, omitted: bool) -> str:
    core_passed = all(
        item["status"] == "pass"
        for section in sections.values()
        for item in section.values()
    )
    if not core_passed:
        return "review_failed"
    return "review_passed_with_omissions" if omitted else "review_passed"


def build_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Dynamic Window Selector D1 AVE validation 阶段 Review",
        "",
        "## 结论",
        "",
        f"状态：**{summary['status']}**。",
        "",
    ]
    for title, section in (
        ("Contract", summary["sections"]["contract"]),
        ("数据与 Test", summary["sections"]["data_and_test"]),
        ("指标与公平性", summary["sections"]["metrics_and_fairness"]),
        ("运行与产物", summary["sections"]["runtime_and_artifacts"]),
    ):
        lines.extend([f"## {title}", ""])
        for name, item in section.items():
            mark = "PASS" if item["status"] == "pass" else "FAIL"
            note = f"；{item['note']}" if item.get("note") else ""
            lines.append(f"- [{mark}] `{name}`{note}")
        lines.append("")
    lines.extend(
        [
            "## Test 冻结",
            "",
            f"- test run allowed：`{str(summary['test_run_allowed']).lower()}`",
            f"- runner SHA：`{summary['frozen_test_run']['runner_sha256']}`",
            "- 主预算：`K=4`；补充预算：`K=2/8`",
            "- 条件策略：`sync_margin_cached_topk=omitted_ineligible_asset`",
            "- test 只能运行一次，输出目录存在时入口必须拒绝覆盖或重跑。",
            "",
            "D1 official test 结束后必须再次 review test summary、execution lock、内核日志和 Git 范围，之后才能进入 D2。",
            "",
        ]
    )
    return "\n".join(lines)


def run(args: argparse.Namespace) -> dict[str, Any]:
    validation_summary_path = args.validation_summary.resolve()
    qualification_path = args.asset_qualification.resolve()
    registry_path = args.asset_registry.resolve()
    junit_path = args.test_junit.resolve()
    kernel_log_path = args.eventness_kernel_log.resolve()
    power_path = args.gpu_power_snapshot.resolve()
    runner_path = args.test_runner.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    validation = load_json(validation_summary_path)
    manifest_lock = load_json(Path(validation["inputs"]["manifest_lock"]["path"]))
    media_lock = load_json(Path(validation["inputs"]["media_lock"]["path"]))
    qualification = load_json(qualification_path)
    registry = load_json(registry_path)
    junit = parse_junit(junit_path)
    frozen_input_changes = verify_frozen_inputs(validation)
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
    unstaged_diff = set(git_lines("diff", "--name-only"))
    staged_diff = set(git_lines("diff", "--cached", "--name-only"))
    untracked_stage_files = set(
        git_lines(
            "ls-files",
            "--others",
            "--exclude-standard",
            "intersuit/scripts",
            "intersuit/tests",
        )
    )
    tracked_runtime_assets = git_lines(
        "ls-files",
        "intersuit/harness/artifacts",
        "intersuit/checkpoints",
        "intersuit/datasets/AVE_OFFICIAL_D1_SOURCE",
    )
    unexpected_diff = sorted(
        (unstaged_diff | untracked_stage_files) - ALLOWED_DIFF_PATHS
    )
    checks = validation.get("checks", {})
    conditional = validation.get("conditional_strategies", {}).get(
        "sync_margin_cached_topk", {}
    )
    registry_assets = registry.get("assets", [])
    qualification_ok = (
        qualification.get("formal_eligible") is False
        and qualification.get("qualification_statuses")
        == ["diagnostic_failed", "schema_incompatible", "missing_inputs"]
        and qualification.get("data_boundary", {}).get(
            "physical_video_overlap_count"
        )
        == 0
        and qualification.get("data_boundary", {}).get("d1_test_accessed")
        is False
        and len(registry_assets) == 1
        and registry_assets[0].get("formal_eligible") is False
    )
    log_paths = [
        output_dir / "validation_finalize.stdout.log",
        output_dir / "validation_finalize.stderr.log",
        output_dir / "validation_review_tests.stdout.log",
        output_dir / "validation_review_tests.stderr.log",
        ARTIFACT_ROOT
        / "dynamic_selector_d1_ave_validation_eventness_v2/stdout.log",
        ARTIFACT_ROOT
        / "dynamic_selector_d1_ave_validation_eventness_v2/stderr.log",
    ]
    sections = {
        "contract": {
            "same_original_micro_window_contract": status_item(
                validation.get("record_audit", {}).get("status") == "pass"
                and validation.get("record_audit", {}).get(
                    "strategy_keys_per_video"
                )
                == 31,
                validation.get("record_audit"),
            ),
            "source_index_timestamp_preserved": status_item(
                validation.get("record_audit", {}).get("status") == "pass",
                "逐样本记录由 finalizer 校验 source index、时间排序和绝对 timestamp",
            ),
            "all_fixed_and_behavior_rollback": status_item(
                checks.get("all_fixed_exact") is True
                and checks.get("d0_exit_passed") is True,
                {
                    "all_fixed": checks.get("all_fixed_exact"),
                    "d0": checks.get("d0_exit_passed"),
                },
            ),
            "double_reproduction_exact": status_item(
                checks.get("baseline_double_reproduction_exact") is True
                and checks.get("eventness_double_reproduction_exact") is True,
                {
                    "baseline": validation["inputs"]["baseline_records"]["sha256"],
                    "eventness": validation["inputs"]["eventness_records"]["sha256"],
                },
            ),
            "conditional_asset_fail_closed": status_item(
                qualification_ok
                and conditional.get("status") == "omitted_ineligible_asset",
                {
                    "qualification": qualification.get("qualification_statuses"),
                    "conditional": conditional,
                },
            ),
            "strategy_semantics_named_correctly": status_item(
                "sync_margin_cached_topk"
                in validation.get("conditional_strategies", {})
                and "temporal_conf_cached_topk"
                not in validation.get("conditional_strategies", {}),
                list(validation.get("conditional_strategies", {})),
            ),
            "contract_tests_passed": status_item(
                junit["status"] == "pass",
                junit,
            ),
        },
        "data_and_test": {
            "official_split_and_physical_dedup_frozen": status_item(
                checks.get("official_manifest_frozen") is True
                and checks.get("candidate_splits_physically_disjoint") is True
                and checks.get("historical_overlap_zero") is True,
                {
                    key: checks.get(key)
                    for key in (
                        "official_manifest_frozen",
                        "candidate_splits_physically_disjoint",
                        "historical_overlap_zero",
                    )
                },
            ),
            "media_complete_and_decodable": status_item(
                checks.get("media_frozen_complete") is True
                and checks.get("validation_decode_preflight_passed") is True,
                {
                    "media": checks.get("media_frozen_complete"),
                    "decode": checks.get("validation_decode_preflight_passed"),
                },
            ),
            "validation_only_role_respected": status_item(
                validation.get("stage") == "D1-AVE-validation-finalization",
                validation.get("stage"),
            ),
            "test_remained_sealed_and_unseen": status_item(
                checks.get("manifest_test_sealed") is True
                and checks.get("media_test_sealed") is True
                and checks.get("validation_only_no_test_access") is True
                and validation.get("test_accessed") is False,
                {
                    "manifest": checks.get("manifest_test_sealed"),
                    "media": checks.get("media_test_sealed"),
                    "validation": checks.get("validation_only_no_test_access"),
                },
            ),
            "conditional_training_data_isolated": status_item(
                qualification.get("data_boundary", {}).get(
                    "physical_video_overlap_count"
                )
                == 0,
                qualification.get("data_boundary"),
            ),
            "frozen_inputs_unchanged": status_item(
                not frozen_input_changes,
                frozen_input_changes,
            ),
        },
        "metrics_and_fairness": {
            "fixed_k_protocol_separate": status_item(
                validation.get("primary_gate", {}).get("budget") == 4
                and conditional.get("status") == "omitted_ineligible_asset",
                {
                    "budgets": [2, 4, 8],
                    "conditional": conditional.get("status"),
                },
                "本阶段没有阈值后少于 K 的正式策略，matched-count Random 不适用",
            ),
            "localization_uses_local_boundaries_only": status_item(
                validation.get("record_audit", {}).get(
                    "local_boundary_video_count"
                )
                == 143,
                validation.get("record_audit"),
            ),
            "bootstrap_protocol_frozen": status_item(
                validation.get("primary_gate", {}).get("bootstrap_resamples")
                == 10_000
                and validation.get("primary_gate", {}).get("bootstrap_seed")
                == 20260724,
                validation.get("primary_gate"),
            ),
            "oracle_exit_gate_passed": status_item(
                checks.get("oracle_hit_at_k_is_one") is True
                and checks.get("oracle_gain_over_random_at_least_10pp") is True
                and checks.get("oracle_bootstrap_lower_positive") is True,
                validation.get("primary_gate"),
            ),
            "token_budget_fairness_not_applicable": status_item(
                True,
                "D1 只评估窗口定位，没有执行 M4 token 生成或比较策略相关截断",
            ),
        },
        "runtime_and_artifacts": {
            "m4_environment_and_explicit_gpu": status_item(
                validation.get("python_executable") == sys.executable
                and checks.get("eventness_generation_environment_recorded")
                is True,
                {
                    "python": validation.get("python_executable"),
                    "eventness_environment": checks.get(
                        "eventness_generation_environment_recorded"
                    ),
                },
            ),
            "logs_present": status_item(
                all(path.is_file() for path in log_paths),
                [str(path) for path in log_paths],
            ),
            "gpu_power_recorded": status_item(
                len(power_lines) == 5 and all("450.00 W" in line for line in power_lines),
                power_lines,
            ),
            "kernel_error_check_clean": status_item(
                not kernel_matches,
                kernel_matches,
                "可见 journal 范围内无 NVRM/Xid/AER/PCIe/GPU fallen/hung task",
            ),
            "runtime_assets_not_tracked_or_staged": status_item(
                not tracked_runtime_assets and not staged_diff,
                {
                    "tracked_runtime_assets": tracked_runtime_assets,
                    "staged_diff": sorted(staged_diff),
                },
            ),
            "git_diff_scope_is_stage_only": status_item(
                not unexpected_diff,
                {
                    "unstaged_diff": sorted(unstaged_diff),
                    "untracked_stage_files": sorted(untracked_stage_files),
                    "unexpected": unexpected_diff,
                },
            ),
            "required_stage_artifacts_present": status_item(
                all(
                    path.is_file()
                    for path in (
                        validation_summary_path,
                        qualification_path,
                        registry_path,
                        output_dir / "validation_report.md",
                        runner_path,
                    )
                ),
                [
                    str(validation_summary_path),
                    str(qualification_path),
                    str(registry_path),
                    str(output_dir / "validation_report.md"),
                    str(runner_path),
                ],
            ),
        },
    }
    omitted = conditional.get("status") == "omitted_ineligible_asset"
    status = review_status(sections, omitted=omitted)
    test_run_allowed = status in {
        "review_passed",
        "review_passed_with_omissions",
    }
    frozen_test_run = {
        "validation_summary_sha256": file_sha256(validation_summary_path),
        "runner_path": str(runner_path),
        "runner_sha256": file_sha256(runner_path),
        "candidate_manifest_sha256": manifest_lock["manifests"]["candidate_test"][
            "sha256"
        ],
        "media_inventory_sha256": media_lock["outputs"]["test_inventory"][
            "sha256"
        ],
        "budgets": [2, 4, 8],
        "main_budget": 4,
        "selection_unit": "original_micro_window",
        "micro_window_sec": 1.0,
        "random_seeds": [20260720, 20260721, 20260722, 20260723, 20260724],
        "bootstrap_resamples": 10_000,
        "bootstrap_seed": 20260724,
        "conditional_strategies": {
            "sync_margin_cached_topk": "omitted_ineligible_asset"
        },
        "rerun_allowed": False,
    }
    summary = {
        "stage": "D1-AVE-validation-stage-review",
        "status": status,
        "reviewed_at": datetime.now().astimezone().isoformat(),
        "python_environment": "M4",
        "python_executable": sys.executable,
        "sections": sections,
        "frozen_test_run": frozen_test_run,
        "test_accessed": False,
        "test_run_allowed": test_run_allowed,
        "next_required_step": (
            "run_D1_official_test_once" if test_run_allowed else "repair_D1_review"
        ),
    }
    locked_config = {
        "stage": "D1-AVE",
        "validation_review_status": status,
        "core_strategies": validation["core_strategies"],
        "conditional_strategies": frozen_test_run["conditional_strategies"],
        "budgets": frozen_test_run["budgets"],
        "main_budget": frozen_test_run["main_budget"],
        "selection_unit": frozen_test_run["selection_unit"],
        "micro_window_sec": frozen_test_run["micro_window_sec"],
        "random_seeds": frozen_test_run["random_seeds"],
        "bootstrap_resamples": frozen_test_run["bootstrap_resamples"],
        "bootstrap_seed": frozen_test_run["bootstrap_seed"],
        "runner_sha256": frozen_test_run["runner_sha256"],
        "test_rerun_allowed": False,
    }
    write_json(output_dir / "validation_review_summary.json", summary)
    write_json(output_dir / "validation_locked_config.json", locked_config)
    (output_dir / "review.md").write_text(
        build_report(summary),
        encoding="utf-8",
    )
    (output_dir / "validation_failures.jsonl").write_text("", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": status,
                "test_run_allowed": test_run_allowed,
                "runner_sha256": frozen_test_run["runner_sha256"],
            },
            ensure_ascii=False,
        )
    )
    return summary


def main() -> int:
    result = run(parse_args())
    return 0 if result["test_run_allowed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
