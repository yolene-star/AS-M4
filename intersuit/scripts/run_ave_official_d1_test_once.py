#!/usr/bin/env python
"""凭 D1 validation review 令牌一次性运行 AVE official test 核心矩阵。"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any

import torch


REPO_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = REPO_ROOT / "intersuit"
ARTIFACT_ROOT = INTERSUIT_ROOT / "harness/artifacts"
EXPECTED_TEST_COUNT = 394
EXPECTED_LOCAL_BOUNDARY_COUNT = 140
ALLOWED_REVIEW_STATUSES = {"review_passed", "review_passed_with_omissions"}


def import_script_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


baseline = import_script_module(
    "ave_d1_test_baseline",
    INTERSUIT_ROOT / "scripts/run_ave_official_d1_validation_baselines.py",
)
eventness_features = import_script_module(
    "ave_d1_test_eventness_features",
    INTERSUIT_ROOT / "scripts/extract_ave_official_d1_validation_eventness.py",
)
eventness_metrics = import_script_module(
    "ave_d1_test_eventness_metrics",
    INTERSUIT_ROOT / "scripts/evaluate_ave_official_d1_validation_eventness.py",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--review-summary",
        type=Path,
        default=ARTIFACT_ROOT
        / "dynamic_selector_d1_ave_official/validation_review_summary.json",
    )
    parser.add_argument(
        "--validation-summary",
        type=Path,
        default=ARTIFACT_ROOT
        / "dynamic_selector_d1_ave_official/validation_summary.json",
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
        "--candidate-manifest",
        type=Path,
        default=ARTIFACT_ROOT
        / "dynamic_selector_d1_ave_official/test_candidate_manifest.sealed.jsonl",
    )
    parser.add_argument(
        "--media-inventory",
        type=Path,
        default=ARTIFACT_ROOT
        / (
            "dynamic_selector_d1_ave_official_media/"
            "test_media_inventory.sealed.jsonl"
        ),
    )
    parser.add_argument(
        "--beats-checkpoint",
        type=Path,
        default=INTERSUIT_ROOT / "checkpoints/BEATs_iter3_plus_AS2M.pt",
    )
    parser.add_argument(
        "--beats-code-root",
        type=Path,
        default=REPO_ROOT / "third_party/OmniMMI/baselines/videollama2/model",
    )
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ARTIFACT_ROOT
        / "dynamic_selector_d1_ave_official/official_test",
    )
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


def assert_review_authorized(
    review_summary: dict[str, Any],
    *,
    validation_summary_path: Path,
    runner_path: Path,
    candidate_manifest_path: Path,
    media_inventory_path: Path,
) -> None:
    frozen = review_summary.get("frozen_test_run", {})
    if (
        review_summary.get("status") not in ALLOWED_REVIEW_STATUSES
        or review_summary.get("test_run_allowed") is not True
        or review_summary.get("test_accessed") is not False
    ):
        raise ValueError("D1 validation review 未授权 official test")
    if frozen.get("validation_summary_sha256") != file_sha256(
        validation_summary_path
    ):
        raise ValueError("validation summary SHA 已变化")
    if frozen.get("runner_sha256") != file_sha256(runner_path):
        raise ValueError("official test runner SHA 已变化")
    if frozen.get("candidate_manifest_sha256") != file_sha256(
        candidate_manifest_path
    ):
        raise ValueError("official test candidate manifest SHA 已变化")
    if frozen.get("media_inventory_sha256") != file_sha256(media_inventory_path):
        raise ValueError("official test media inventory SHA 已变化")
    if frozen.get("budgets") != [2, 4, 8] or frozen.get("main_budget") != 4:
        raise ValueError("official test 预算与冻结计划不一致")
    if frozen.get("conditional_strategies") != {
        "sync_margin_cached_topk": "omitted_ineligible_asset"
    }:
        raise ValueError("official test 条件策略状态与冻结计划不一致")


def load_test_inputs(
    *,
    candidate_manifest: Path,
    media_inventory: Path,
    manifest_lock_path: Path,
    media_lock_path: Path,
) -> list[dict[str, Any]]:
    candidates = load_jsonl(candidate_manifest)
    media_rows = load_jsonl(media_inventory)
    manifest_lock = load_json(manifest_lock_path)
    media_lock = load_json(media_lock_path)
    if (
        len(candidates) != EXPECTED_TEST_COUNT
        or len(media_rows) != EXPECTED_TEST_COUNT
    ):
        raise ValueError("official test candidate/media 数量变化")
    if (
        manifest_lock.get("manifests", {})
        .get("candidate_test", {})
        .get("sha256")
        != file_sha256(candidate_manifest)
        or media_lock.get("outputs", {})
        .get("test_inventory", {})
        .get("sha256")
        != file_sha256(media_inventory)
    ):
        raise ValueError("official test manifest/media SHA 与冻结 lock 不一致")
    if (
        manifest_lock.get("test_seal", {}).get("sealed") is not True
        or manifest_lock.get("test_seal", {}).get("predictions_run") is not False
        or manifest_lock.get("test_seal", {}).get("effect_results_viewed") is not False
        or media_lock.get("test_seal", {}).get("sealed") is not True
        or media_lock.get("counts", {}).get("test", {}).get("missing_count") != 0
    ):
        raise ValueError("official test 上游 seal 或媒体完整性非法")
    candidate_by_id = {str(row["youtube_id"]): row for row in candidates}
    media_by_id = {str(row["youtube_id"]): row for row in media_rows}
    if (
        len(candidate_by_id) != EXPECTED_TEST_COUNT
        or len(media_by_id) != EXPECTED_TEST_COUNT
        or set(candidate_by_id) != set(media_by_id)
    ):
        raise ValueError("official test 含重复或 candidate/media ID 不一致")
    rows = []
    for youtube_id in sorted(candidate_by_id):
        candidate = candidate_by_id[youtube_id]
        media = media_by_id[youtube_id]
        if (
            candidate.get("official_split") != "test"
            or media.get("official_split") != "test"
            or media.get("test_sealed") is not True
            or media.get("status") != "present"
        ):
            raise ValueError("official test 输入 split/seal/status 非法")
        rows.append({**candidate, **media})
    if sum(bool(row["has_local_boundary"]) for row in rows) != EXPECTED_LOCAL_BOUNDARY_COUNT:
        raise ValueError("official test 局部边界数量变化")
    return rows


def primary_gate(metrics: dict[str, Any]) -> dict[str, Any]:
    oracle = metrics["local_boundary_primary"]["oracle_topk|K=4|seed=None"]
    random_mean = metrics["random_local_boundary_summary"]["K=4"]["mean"]
    bootstrap = metrics["oracle_minus_random_window_hit_paired_bootstrap"]["K=4"]
    oracle_hit = float(oracle["window_hit_at_k"])
    random_hit = float(random_mean["window_hit_at_k"])
    checks = {
        "oracle_hit_at_k_is_one": oracle_hit == 1.0,
        "oracle_gain_over_random_at_least_10pp": oracle_hit - random_hit >= 0.10,
        "oracle_bootstrap_lower_positive": float(bootstrap["ci95_lower"]) > 0,
        "bootstrap_protocol_frozen": bootstrap["resamples"] == 10_000
        and bootstrap["seed"] == 20260724,
    }
    return {
        "status": "pass" if all(checks.values()) else "fail",
        "checks": checks,
        "budget": 4,
        "oracle_window_hit_at_k": oracle_hit,
        "random_mean_window_hit_at_k": random_hit,
        "oracle_minus_random": oracle_hit - random_hit,
        "ci95_lower": float(bootstrap["ci95_lower"]),
        "ci95_upper": float(bootstrap["ci95_upper"]),
    }


def build_report(summary: dict[str, Any]) -> str:
    gate = summary["primary_gate"]
    return f"""# Dynamic Window Selector D1 AVE official test 报告

## 结论

状态：**{summary["status"]}**。本 test 按 validation review 冻结配置一次性运行。

- test 物理视频：{summary["test_physical_video_count"]}
- 局部边界视频：{summary["local_boundary_physical_video_count"]}
- 主预算：`K=4`
- Oracle Window Hit@K：{gate["oracle_window_hit_at_k"]:.6f}
- Random 五 seed 均值：{gate["random_mean_window_hit_at_k"]:.6f}
- Oracle - Random：{gate["oracle_minus_random"]:.6f}
- paired bootstrap 95% CI：[{gate["ci95_lower"]:.6f}, {gate["ci95_upper"]:.6f}]
- `sync_margin_cached_topk`：`omitted_ineligible_asset`
- test accessed：`true`
- 重跑允许：`false`

## 解释边界

D1 只验证 1 秒选窗 harness 和定位上界，不证明长视频 selector 或下游 QA
会提升。只有本报告通过并完成 test 后 review，才允许进入 D2 数据准备。
"""


def run(args: argparse.Namespace) -> dict[str, Any]:
    runner_path = Path(__file__).resolve()
    review_summary_path = args.review_summary.resolve()
    validation_summary_path = args.validation_summary.resolve()
    manifest_lock_path = args.manifest_lock.resolve()
    media_lock_path = args.media_lock.resolve()
    candidate_manifest = args.candidate_manifest.resolve()
    media_inventory = args.media_inventory.resolve()
    checkpoint_path = args.beats_checkpoint.resolve()
    output_dir = args.output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(
            "official test 输出目录已存在；按一次性协议拒绝覆盖或重跑"
        )
    review_summary = load_json(review_summary_path)
    assert_review_authorized(
        review_summary,
        validation_summary_path=validation_summary_path,
        runner_path=runner_path,
        candidate_manifest_path=candidate_manifest,
        media_inventory_path=media_inventory,
    )
    rows = load_test_inputs(
        candidate_manifest=candidate_manifest,
        media_inventory=media_inventory,
        manifest_lock_path=manifest_lock_path,
        media_lock_path=media_lock_path,
    )
    process_environment = eventness_features.current_process_environment(args.device)
    eventness_features.assert_fresh_extraction_environment(process_environment)
    checkpoint_sha256 = file_sha256(checkpoint_path)

    output_dir.mkdir(parents=True)
    started_at = datetime.now().astimezone()
    execution_lock = {
        "status": "running",
        "started_at": started_at.isoformat(),
        "one_time_run": True,
        "rerun_allowed": False,
        "review_summary_sha256": file_sha256(review_summary_path),
        "validation_summary_sha256": file_sha256(validation_summary_path),
        "runner_sha256": file_sha256(runner_path),
        "candidate_manifest_sha256": file_sha256(candidate_manifest),
        "media_inventory_sha256": file_sha256(media_inventory),
        "checkpoint_sha256": checkpoint_sha256,
        "process_environment": process_environment,
        "effect_results_viewed": False,
    }
    write_json(output_dir / "execution_lock.json", execution_lock)
    try:
        waveform_scores, cache_hits, newly_decoded = (
            baseline.extract_or_load_waveform_scores(
                rows,
                output_dir / "waveform_score_cache",
            )
        )
        baseline_records, baseline_metrics = baseline.run_strategies(
            rows,
            waveform_scores,
            official_split="test",
            test_accessed=True,
        )
        baseline_records_path = output_dir / "test_selection_records.jsonl"
        baseline.write_jsonl(baseline_records_path, baseline_records)

        encoder = eventness_features.beats_tools.BEATsWindowEncoder(
            checkpoint_path=checkpoint_path,
            beats_code_root=args.beats_code_root.resolve(),
            device=args.device,
        )
        feature_records = [
            eventness_features.extract_one(
                row,
                encoder=encoder,
                checkpoint_sha256=checkpoint_sha256,
                output_dir=output_dir / "eventness",
                official_split="test",
                test_accessed=True,
            )
            for row in rows
        ]
        feature_records.sort(key=lambda row: row.youtube_id)
        feature_manifest_path = output_dir / "test_eventness_feature_manifest.jsonl"
        feature_manifest_path.write_text(
            eventness_features.serialize_jsonl(feature_records),
            encoding="utf-8",
        )
        scores_by_id = {}
        for record in feature_records:
            payload = eventness_features.validate_saved_feature(
                Path(record.feature_path),
                youtube_id=record.youtube_id,
                media_sha256=record.media_sha256,
                checkpoint_sha256=checkpoint_sha256,
            )
            scores_by_id[record.youtube_id] = payload[
                "eventness_cached_scores"
            ].float()
        eventness_records, eventness_result_metrics = (
            eventness_metrics.evaluate_eventness(
                rows,
                scores_by_id,
                official_split="test",
                test_accessed=True,
            )
        )
        eventness_records_path = output_dir / "test_eventness_selection_records.jsonl"
        baseline.write_jsonl(eventness_records_path, eventness_records)
        gate = primary_gate(baseline_metrics)
        summary = {
            "stage": "D1-AVE-official-test-once",
            "status": "test_passed" if gate["status"] == "pass" else "test_failed",
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now().astimezone().isoformat(),
            "one_time_run": True,
            "rerun_allowed": False,
            "python_environment": "M4",
            "python_executable": sys.executable,
            "process_environment": process_environment,
            "test_physical_video_count": len(rows),
            "local_boundary_physical_video_count": sum(
                bool(row["has_local_boundary"]) for row in rows
            ),
            "selection_unit": "original_micro_window",
            "micro_window_sec": 1.0,
            "budgets": [2, 4, 8],
            "fixed_random_seeds": list(baseline.FIXED_CONTROL_SEEDS),
            "bootstrap_seed": baseline.BOOTSTRAP_SEED,
            "bootstrap_resamples": baseline.BOOTSTRAP_RESAMPLES,
            "all_fixed_exact_count": sum(
                record.all_fixed_exact for record in feature_records
            ),
            "waveform_cache_hit_count": cache_hits,
            "waveform_newly_decoded_count": newly_decoded,
            "primary_gate": gate,
            "metrics": {
                **baseline_metrics,
                "eventness_cached": eventness_result_metrics,
            },
            "conditional_strategies": {
                "sync_margin_cached_topk": "omitted_ineligible_asset"
            },
            "inputs": {
                "review_summary": {
                    "path": str(review_summary_path),
                    "sha256": file_sha256(review_summary_path),
                },
                "validation_summary": {
                    "path": str(validation_summary_path),
                    "sha256": file_sha256(validation_summary_path),
                },
                "candidate_manifest": {
                    "path": str(candidate_manifest),
                    "sha256": file_sha256(candidate_manifest),
                },
                "media_inventory": {
                    "path": str(media_inventory),
                    "sha256": file_sha256(media_inventory),
                },
                "checkpoint": {
                    "path": str(checkpoint_path),
                    "sha256": checkpoint_sha256,
                },
            },
            "outputs": {
                "baseline_records": {
                    "path": str(baseline_records_path),
                    "sha256": file_sha256(baseline_records_path),
                },
                "eventness_feature_manifest": {
                    "path": str(feature_manifest_path),
                    "sha256": file_sha256(feature_manifest_path),
                },
                "eventness_records": {
                    "path": str(eventness_records_path),
                    "sha256": file_sha256(eventness_records_path),
                },
            },
            "test_accessed": True,
            "effect_results_viewed": True,
        }
        write_json(output_dir / "summary.json", summary)
        (output_dir / "report.md").write_text(
            build_report(summary),
            encoding="utf-8",
        )
        execution_lock.update(
            {
                "status": "completed",
                "finished_at": summary["finished_at"],
                "result_status": summary["status"],
                "effect_results_viewed": True,
            }
        )
        write_json(output_dir / "execution_lock.json", execution_lock)
        print(
            json.dumps(
                {
                    "status": summary["status"],
                    "test_count": len(rows),
                    "local_boundary_count": summary[
                        "local_boundary_physical_video_count"
                    ],
                    "rerun_allowed": False,
                },
                ensure_ascii=False,
            )
        )
        return summary
    except BaseException as exc:
        execution_lock.update(
            {
                "status": "failed",
                "finished_at": datetime.now().astimezone().isoformat(),
                "failure_type": type(exc).__name__,
                "failure_message": str(exc),
                "effect_results_viewed": False,
            }
        )
        write_json(output_dir / "execution_lock.json", execution_lock)
        raise


def main() -> int:
    result = run(parse_args())
    return 0 if result["status"] == "test_passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
