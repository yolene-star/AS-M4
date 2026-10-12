#!/usr/bin/env python
"""汇总并冻结 Dynamic Selector D1 AVE validation，不解封 test。"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_ROOT = REPO_ROOT / "intersuit/harness/artifacts"
DEFAULT_OUTPUT_DIR = ARTIFACT_ROOT / "dynamic_selector_d1_ave_official"
EXPECTED_VALIDATION_COUNT = 391
EXPECTED_LOCAL_BOUNDARY_COUNT = 143
EXPECTED_BUDGETS = [2, 4, 8]
EXPECTED_RANDOM_SEEDS = [20260720, 20260721, 20260722, 20260723, 20260724]
EXPECTED_CONDITION_STATUSES = [
    "diagnostic_failed",
    "schema_incompatible",
    "missing_inputs",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--d0-summary",
        type=Path,
        default=ARTIFACT_ROOT
        / "dynamic_selector_d0_contract/d0_exit_summary.json",
    )
    parser.add_argument(
        "--manifest-lock",
        type=Path,
        default=ARTIFACT_ROOT
        / "dynamic_selector_d1_ave_official/manifest_lock.json",
    )
    parser.add_argument(
        "--media-lock",
        type=Path,
        default=ARTIFACT_ROOT
        / "dynamic_selector_d1_ave_official_media/media_lock.json",
    )
    parser.add_argument(
        "--decode-preflight-summary",
        type=Path,
        default=ARTIFACT_ROOT
        / "dynamic_selector_d1_ave_validation_media_preflight/summary.json",
    )
    parser.add_argument(
        "--baseline-summary",
        type=Path,
        default=ARTIFACT_ROOT
        / "dynamic_selector_d1_ave_validation_baselines/summary.json",
    )
    parser.add_argument(
        "--baseline-records",
        type=Path,
        default=ARTIFACT_ROOT
        / (
            "dynamic_selector_d1_ave_validation_baselines/"
            "validation_selection_records.jsonl"
        ),
    )
    parser.add_argument(
        "--baseline-repro-summary",
        type=Path,
        default=ARTIFACT_ROOT
        / "dynamic_selector_d1_ave_validation_baselines_repro2/summary.json",
    )
    parser.add_argument(
        "--baseline-repro-records",
        type=Path,
        default=ARTIFACT_ROOT
        / (
            "dynamic_selector_d1_ave_validation_baselines_repro2/"
            "validation_selection_records.jsonl"
        ),
    )
    parser.add_argument(
        "--eventness-feature-summary",
        type=Path,
        default=ARTIFACT_ROOT
        / "dynamic_selector_d1_ave_validation_eventness_v2/summary.json",
    )
    parser.add_argument(
        "--eventness-summary",
        type=Path,
        default=ARTIFACT_ROOT
        / "dynamic_selector_d1_ave_validation_eventness_metrics_v2/summary.json",
    )
    parser.add_argument(
        "--eventness-records",
        type=Path,
        default=ARTIFACT_ROOT
        / (
            "dynamic_selector_d1_ave_validation_eventness_metrics_v2/"
            "validation_eventness_selection_records.jsonl"
        ),
    )
    parser.add_argument(
        "--eventness-repro-summary",
        type=Path,
        default=ARTIFACT_ROOT
        / "dynamic_selector_d1_ave_validation_eventness_metrics_v2_repro2/summary.json",
    )
    parser.add_argument(
        "--eventness-repro-records",
        type=Path,
        default=ARTIFACT_ROOT
        / (
            "dynamic_selector_d1_ave_validation_eventness_metrics_v2_repro2/"
            "validation_eventness_selection_records.jsonl"
        ),
    )
    parser.add_argument(
        "--qualification-summary",
        type=Path,
        default=ARTIFACT_ROOT
        / (
            "dynamic_selector_t0_temporal_asset_recovery/qualification_v2/"
            "summary.json"
        ),
    )
    parser.add_argument(
        "--asset-qualification",
        type=Path,
        default=ARTIFACT_ROOT
        / (
            "dynamic_selector_t0_temporal_asset_recovery/qualification_v2/"
            "asset_qualification.json"
        ),
    )
    parser.add_argument(
        "--asset-registry",
        type=Path,
        default=ARTIFACT_ROOT
        / (
            "dynamic_selector_t0_temporal_asset_recovery/qualification_v2/"
            "asset_registry.json"
        ),
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层必须为 object：{path}")
    return value


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows:
        raise ValueError(f"JSONL 为空：{path}")
    return rows


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _metric(summary: dict, section: str, key: str) -> dict:
    value = summary.get("metrics", {}).get(section, {}).get(key)
    if not isinstance(value, dict):
        return {}
    return value


def build_exit_checks(
    *,
    d0_summary: dict,
    manifest_lock: dict,
    media_lock: dict,
    preflight_summary: dict,
    baseline_summary: dict,
    eventness_feature_summary: dict,
    eventness_summary: dict,
    qualification_summary: dict,
    baseline_reproduced: bool,
    eventness_reproduced: bool,
) -> dict[str, bool]:
    oracle = _metric(
        baseline_summary,
        "local_boundary_primary",
        "oracle_topk|K=4|seed=None",
    )
    random_summary = (
        baseline_summary.get("metrics", {})
        .get("random_local_boundary_summary", {})
        .get("K=4", {})
    )
    random_mean = random_summary.get("mean", {})
    bootstrap = (
        baseline_summary.get("metrics", {})
        .get("oracle_minus_random_window_hit_paired_bootstrap", {})
        .get("K=4", {})
    )
    oracle_hit = oracle.get("window_hit_at_k")
    random_hit = random_mean.get("window_hit_at_k")
    oracle_gain = (
        float(oracle_hit) - float(random_hit)
        if isinstance(oracle_hit, (int, float))
        and isinstance(random_hit, (int, float))
        else float("-inf")
    )
    qualification_statuses = qualification_summary.get("qualification_statuses")
    return {
        "d0_exit_passed": d0_summary.get("status") == "pass",
        "official_manifest_frozen": manifest_lock.get("status") == "frozen",
        "candidate_splits_physically_disjoint": all(
            value == 0
            for value in manifest_lock.get("audits", {})
            .get("candidate_cross_split_overlap", {})
            .values()
        ),
        "historical_overlap_zero": manifest_lock.get("audits", {}).get(
            "candidate_historical_overlap"
        )
        == 0,
        "manifest_test_sealed": manifest_lock.get("test_seal", {}).get("sealed")
        is True
        and manifest_lock.get("test_seal", {}).get("predictions_run") is False
        and manifest_lock.get("test_seal", {}).get("effect_results_viewed")
        is False,
        "media_frozen_complete": media_lock.get("status") == "frozen"
        and all(
            split.get("missing_count") == 0
            for split in media_lock.get("counts", {}).values()
        ),
        "media_test_sealed": media_lock.get("test_seal", {}).get("sealed")
        is True
        and media_lock.get("effect_evaluation_performed") is False,
        "validation_decode_preflight_passed": preflight_summary.get("status")
        == "pass"
        and preflight_summary.get("sample_count") == EXPECTED_VALIDATION_COUNT
        and preflight_summary.get("invalid_count") == 0
        and preflight_summary.get("test_accessed") is False,
        "baseline_matrix_complete": baseline_summary.get("status")
        == "validation_baselines_frozen"
        and baseline_summary.get("validation_physical_video_count")
        == EXPECTED_VALIDATION_COUNT
        and baseline_summary.get("local_boundary_physical_video_count")
        == EXPECTED_LOCAL_BOUNDARY_COUNT
        and baseline_summary.get("budgets") == EXPECTED_BUDGETS
        and baseline_summary.get("fixed_random_seeds") == EXPECTED_RANDOM_SEEDS,
        "baseline_double_reproduction_exact": baseline_reproduced,
        "all_fixed_exact": eventness_feature_summary.get("status") == "pass"
        and eventness_feature_summary.get("execution_mode") == "extraction"
        and eventness_feature_summary.get("newly_extracted_count")
        == EXPECTED_VALIDATION_COUNT
        and eventness_feature_summary.get("all_fixed_exact_count")
        == EXPECTED_VALIDATION_COUNT
        and eventness_feature_summary.get("audit_pass_count")
        == EXPECTED_VALIDATION_COUNT,
        "eventness_generation_environment_recorded": (
            eventness_feature_summary.get("python_environment") == "M4"
            and eventness_feature_summary.get("visible_cuda_device_count") == 1
            and eventness_feature_summary.get("asset_generation_environment", {}).get(
                "cuda_visible_devices"
            )
            not in {None, ""}
            and eventness_feature_summary.get("asset_generation_environment", {}).get(
                "python_executable"
            )
            == sys.executable
        ),
        "eventness_matrix_complete": eventness_summary.get("status") == "pass"
        and eventness_summary.get("validation_physical_video_count")
        == EXPECTED_VALIDATION_COUNT
        and eventness_summary.get("local_boundary_physical_video_count")
        == EXPECTED_LOCAL_BOUNDARY_COUNT
        and eventness_summary.get("budgets") == EXPECTED_BUDGETS
        and eventness_summary.get("all_fixed_equivalence_status") == "pass",
        "eventness_double_reproduction_exact": eventness_reproduced,
        "oracle_hit_at_k_is_one": oracle_hit == 1.0,
        "oracle_gain_over_random_at_least_10pp": oracle_gain >= 0.10,
        "oracle_bootstrap_lower_positive": bootstrap.get("ci95_lower", 0) > 0
        and bootstrap.get("resamples") == 10_000
        and bootstrap.get("seed") == 20260724,
        "conditional_asset_resolved": qualification_summary.get("status")
        == "omitted_ineligible_asset"
        and qualification_summary.get("formal_eligible") is False
        and qualification_statuses == EXPECTED_CONDITION_STATUSES,
        "validation_only_no_test_access": all(
            value.get("test_accessed") is False
            for value in (
                baseline_summary,
                eventness_feature_summary,
                eventness_summary,
                qualification_summary,
            )
        ),
    }


def validate_selection_records(
    baseline_rows: list[dict],
    eventness_rows: list[dict],
) -> dict[str, Any]:
    expected_timestamps = {
        index: [float(index), float(index + 1)] for index in range(10)
    }
    strategies_by_video: dict[str, Counter] = defaultdict(Counter)
    local_ids = set()
    all_rows = baseline_rows + eventness_rows
    for row in all_rows:
        if (
            row.get("official_split") != "validation"
            or row.get("test_accessed") is not False
            or row.get("selection_unit") != "original_micro_window"
            or row.get("micro_window_sec") != 1.0
            or row.get("valid_window_count") != 10
        ):
            raise ValueError("逐样本记录违反 validation/micro-window contract")
        ranked = row.get("selected_ranked_indices")
        time_order = row.get("selected_time_order_indices")
        timestamps = row.get("selected_timestamps")
        budget = int(row["budget"])
        if (
            not isinstance(ranked, list)
            or len(ranked) != min(budget, 10)
            or len(ranked) != len(set(ranked))
            or any(not isinstance(index, int) or index not in range(10) for index in ranked)
        ):
            raise ValueError("逐样本 selected source index 非法")
        if time_order != sorted(ranked):
            raise ValueError("选中窗口未按原始时间排序")
        if timestamps != [expected_timestamps[index] for index in time_order]:
            raise ValueError("source index 与绝对 timestamp 不一致")
        if abs(float(row["compression_ratio"]) - len(ranked) / 10.0) > 1e-12:
            raise ValueError("compression ratio 与实际窗口数不一致")
        youtube_id = str(row["youtube_id"])
        key = (str(row["strategy"]), budget, row.get("random_seed"))
        strategies_by_video[youtube_id][key] += 1
        if row.get("has_local_boundary") is True:
            local_ids.add(youtube_id)

    baseline_ids = {str(row["youtube_id"]) for row in baseline_rows}
    eventness_ids = {str(row["youtube_id"]) for row in eventness_rows}
    if (
        len(baseline_ids) != EXPECTED_VALIDATION_COUNT
        or baseline_ids != eventness_ids
        or len(local_ids) != EXPECTED_LOCAL_BOUNDARY_COUNT
    ):
        raise ValueError("逐样本 validation ID 或局部边界数量不一致")
    expected_keys = {("all", 10, None)}
    expected_keys.update(
        (strategy, budget, None)
        for strategy in ("uniform_topk", "energy_topk", "onset_topk", "oracle_topk")
        for budget in EXPECTED_BUDGETS
    )
    expected_keys.update(
        ("random_topk", budget, seed)
        for budget in EXPECTED_BUDGETS
        for seed in EXPECTED_RANDOM_SEEDS
    )
    expected_eventness_keys = {
        ("eventness_cached_topk", budget, None) for budget in EXPECTED_BUDGETS
    }
    for youtube_id in baseline_ids:
        observed = strategies_by_video[youtube_id]
        if any(observed[key] != 1 for key in expected_keys | expected_eventness_keys):
            raise ValueError(f"逐样本策略矩阵不完整：{youtube_id}")
        if set(observed) != expected_keys | expected_eventness_keys:
            raise ValueError(f"逐样本策略矩阵包含未注册策略：{youtube_id}")
    return {
        "validation_video_count": len(baseline_ids),
        "local_boundary_video_count": len(local_ids),
        "baseline_record_count": len(baseline_rows),
        "eventness_record_count": len(eventness_rows),
        "strategy_keys_per_video": len(expected_keys | expected_eventness_keys),
        "status": "pass",
    }


def build_report(summary: dict[str, Any]) -> str:
    checks = summary["checks"]
    failed = [name for name, passed in checks.items() if not passed]
    gate = summary["primary_gate"]
    return f"""# Dynamic Window Selector D1 AVE validation 汇总

## 结论

状态：**{summary["status"]}**。本汇总只冻结 validation 证据，不解封 test。

- 核心检查：{sum(checks.values())}/{len(checks)}
- 未通过检查：{failed or "无"}
- validation 物理视频：{summary["record_audit"]["validation_video_count"]}
- 局部边界视频：{summary["record_audit"]["local_boundary_video_count"]}
- 主预算：`K=4`
- Oracle Window Hit@K：{gate["oracle_window_hit_at_k"]:.6f}
- Random 五 seed 均值：{gate["random_mean_window_hit_at_k"]:.6f}
- Oracle - Random：{gate["oracle_minus_random"]:.6f}
- paired bootstrap 95% CI：[{gate["ci95_lower"]:.6f}, {gate["ci95_upper"]:.6f}]
- 条件策略 `sync_margin_cached_topk`：`omitted_ineligible_asset`
- test accessed：`false`

## 阶段边界

下一步必须执行第 14 节独立阶段 review。只有 review 结论为
`review_passed` 或 `review_passed_with_omissions`，才允许冻结并执行一次性
D1 official test。
"""


def run(args: argparse.Namespace) -> dict[str, Any]:
    paths = {
        name: Path(value).resolve()
        for name, value in vars(args).items()
        if name != "output_dir"
    }
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    loaded = {
        name: load_json(path)
        for name, path in paths.items()
        if name
        not in {
            "baseline_records",
            "baseline_repro_records",
            "eventness_records",
            "eventness_repro_records",
            "asset_qualification",
            "asset_registry",
        }
    }
    baseline_reproduced = (
        file_sha256(paths["baseline_records"])
        == file_sha256(paths["baseline_repro_records"])
        == loaded["baseline_summary"]["outputs"]["selection_records"]["sha256"]
        == loaded["baseline_repro_summary"]["outputs"]["selection_records"]["sha256"]
    )
    eventness_reproduced = (
        file_sha256(paths["eventness_records"])
        == file_sha256(paths["eventness_repro_records"])
        == loaded["eventness_summary"]["outputs"]["selection_records"]["sha256"]
        == loaded["eventness_repro_summary"]["outputs"]["selection_records"]["sha256"]
    )
    baseline_rows = load_jsonl(paths["baseline_records"])
    eventness_rows = load_jsonl(paths["eventness_records"])
    record_audit = validate_selection_records(baseline_rows, eventness_rows)
    checks = build_exit_checks(
        d0_summary=loaded["d0_summary"],
        manifest_lock=loaded["manifest_lock"],
        media_lock=loaded["media_lock"],
        preflight_summary=loaded["decode_preflight_summary"],
        baseline_summary=loaded["baseline_summary"],
        eventness_feature_summary=loaded["eventness_feature_summary"],
        eventness_summary=loaded["eventness_summary"],
        qualification_summary=loaded["qualification_summary"],
        baseline_reproduced=baseline_reproduced,
        eventness_reproduced=eventness_reproduced,
    )
    oracle = _metric(
        loaded["baseline_summary"],
        "local_boundary_primary",
        "oracle_topk|K=4|seed=None",
    )
    random_mean = loaded["baseline_summary"]["metrics"][
        "random_local_boundary_summary"
    ]["K=4"]["mean"]
    bootstrap = loaded["baseline_summary"]["metrics"][
        "oracle_minus_random_window_hit_paired_bootstrap"
    ]["K=4"]
    oracle_hit = float(oracle["window_hit_at_k"])
    random_hit = float(random_mean["window_hit_at_k"])
    summary = {
        "stage": "D1-AVE-validation-finalization",
        "status": (
            "validation_ready_for_review"
            if all(checks.values())
            else "validation_not_ready"
        ),
        "finalized_at": datetime.now().astimezone().isoformat(),
        "python_environment": "M4",
        "python_executable": sys.executable,
        "checks": checks,
        "record_audit": record_audit,
        "primary_gate": {
            "budget": 4,
            "oracle_window_hit_at_k": oracle_hit,
            "random_mean_window_hit_at_k": random_hit,
            "oracle_minus_random": oracle_hit - random_hit,
            "ci95_lower": float(bootstrap["ci95_lower"]),
            "ci95_upper": float(bootstrap["ci95_upper"]),
            "bootstrap_resamples": int(bootstrap["resamples"]),
            "bootstrap_seed": int(bootstrap["seed"]),
        },
        "core_strategies": [
            "all",
            "uniform_topk",
            "energy_topk",
            "onset_topk",
            "eventness_cached_topk",
            "oracle_topk",
            "random_topk",
        ],
        "conditional_strategies": {
            "sync_margin_cached_topk": {
                "status": "omitted_ineligible_asset",
                "asset_id": loaded["qualification_summary"]["asset_id"],
                "qualification_statuses": loaded["qualification_summary"][
                    "qualification_statuses"
                ],
            }
        },
        "inputs": {
            name: {"path": str(path), "sha256": file_sha256(path)}
            for name, path in paths.items()
        },
        "test_accessed": False,
        "test_run_allowed": False,
        "next_required_step": "D1_validation_stage_review",
    }
    write_json(output_dir / "validation_summary.json", summary)
    (output_dir / "validation_report.md").write_text(
        build_report(summary),
        encoding="utf-8",
    )
    shutil.copyfile(
        paths["asset_qualification"],
        output_dir / "asset_qualification.json",
    )
    shutil.copyfile(paths["asset_registry"], output_dir / "asset_registry.json")
    print(
        json.dumps(
            {
                "status": summary["status"],
                "checks_passed": sum(checks.values()),
                "check_count": len(checks),
                "test_run_allowed": False,
            },
            ensure_ascii=False,
        )
    )
    return summary


def main() -> int:
    result = run(parse_args())
    return 0 if result["status"] == "validation_ready_for_review" else 1


if __name__ == "__main__":
    raise SystemExit(main())
