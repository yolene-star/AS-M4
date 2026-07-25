#!/usr/bin/env python
"""在冻结 AVE validation 特征上评估 cached Eventness 的 K=2/4/8。"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import torch

from intersuit.harness.ave_d1_protocol import (
    D1_BUDGETS,
    aggregate_evaluations,
    build_micro_window_timestamps,
    evaluate_selection,
    normalize_target_intervals,
    ranked_score_indices,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = REPO_ROOT / "intersuit"
EXPECTED_VALIDATION_COUNT = 391


def import_script_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


baseline = import_script_module(
    "ave_d1_validation_baseline_for_eventness_evaluation",
    INTERSUIT_ROOT / "scripts/run_ave_official_d1_validation_baselines.py",
)
eventness_features = import_script_module(
    "ave_d1_validation_eventness_features_for_evaluation",
    INTERSUIT_ROOT / "scripts/extract_ave_official_d1_validation_eventness.py",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidate-manifest",
        type=Path,
        default=Path(
            "intersuit/harness/artifacts/dynamic_selector_d1_ave_official/"
            "validation_candidate_manifest.jsonl"
        ),
    )
    parser.add_argument(
        "--media-inventory",
        type=Path,
        default=Path(
            "intersuit/harness/artifacts/dynamic_selector_d1_ave_official_media/"
            "validation_media_inventory.jsonl"
        ),
    )
    parser.add_argument(
        "--decode-preflight-summary",
        type=Path,
        default=Path(
            "intersuit/harness/artifacts/"
            "dynamic_selector_d1_ave_validation_media_preflight/summary.json"
        ),
    )
    parser.add_argument(
        "--feature-dir",
        type=Path,
        default=Path(
            "intersuit/harness/artifacts/"
            "dynamic_selector_d1_ave_validation_eventness"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(
            "intersuit/harness/artifacts/"
            "dynamic_selector_d1_ave_validation_eventness_metrics"
        ),
    )
    parser.add_argument("--expected-count", type=int, default=EXPECTED_VALIDATION_COUNT)
    return parser.parse_args()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_frozen_scores(
    rows: list[dict],
    feature_dir: Path,
) -> tuple[dict[str, torch.Tensor], dict]:
    manifest_path = feature_dir / "validation_eventness_feature_manifest.jsonl"
    summary_path = feature_dir / "summary.json"
    manifest = baseline.load_jsonl(manifest_path)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if (
        summary.get("status") != "pass"
        or summary.get("smoke_only") is not False
        or summary.get("sample_count") != len(rows)
        or summary.get("all_fixed_exact_count") != len(rows)
        or summary.get("test_accessed") is not False
    ):
        raise ValueError("cached Eventness 正式特征 summary 未通过")
    manifest_by_id = {record["youtube_id"]: record for record in manifest}
    row_by_id = {row["youtube_id"]: row for row in rows}
    if len(manifest_by_id) != len(rows) or set(manifest_by_id) != set(row_by_id):
        raise ValueError("cached Eventness manifest 与冻结 validation 不一致")
    scores_by_id = {}
    for youtube_id in sorted(row_by_id):
        row = row_by_id[youtube_id]
        record = manifest_by_id[youtube_id]
        if (
            record.get("official_split") != "validation"
            or record.get("media_sha256") != row["media_sha256"]
            or record.get("all_fixed_exact") is not True
            or record.get("test_accessed") is not False
        ):
            raise ValueError(f"cached Eventness record 身份非法：{youtube_id}")
        feature_path = Path(record["feature_path"])
        if file_sha256(feature_path) != record["feature_sha256"]:
            raise ValueError(f"cached Eventness feature SHA 变化：{youtube_id}")
        payload = eventness_features.validate_saved_feature(
            feature_path,
            youtube_id=youtube_id,
            media_sha256=row["media_sha256"],
            checkpoint_sha256=record["checkpoint_sha256"],
        )
        scores_by_id[youtube_id] = payload["eventness_cached_scores"].float()
    return scores_by_id, {
        "manifest_path": str(manifest_path.resolve()),
        "manifest_sha256": file_sha256(manifest_path),
        "feature_summary_path": str(summary_path.resolve()),
        "feature_summary_sha256": file_sha256(summary_path),
        "checkpoint": summary["checkpoint"],
    }


def evaluate_eventness(
    rows: list[dict],
    scores_by_id: dict[str, torch.Tensor],
    *,
    official_split: str = "validation",
    test_accessed: bool = False,
) -> tuple[list[dict], dict]:
    timestamps = build_micro_window_timestamps()
    records = []
    all_values = defaultdict(list)
    local_values = defaultdict(list)
    for row in rows:
        youtube_id = row["youtube_id"]
        intervals = normalize_target_intervals(row["annotations"])
        local = bool(row["has_local_boundary"])
        ranking = ranked_score_indices(scores_by_id[youtube_id], timestamps)
        for budget in D1_BUDGETS:
            evaluation = evaluate_selection(
                strategy="eventness_cached_topk",
                budget=budget,
                ranked_selected_indices=ranking,
                timestamps=timestamps,
                target_intervals=intervals,
            )
            records.append(
                baseline.evaluation_record(
                    youtube_id=youtube_id,
                    has_local_boundary=local,
                    target_intervals=intervals,
                    evaluation=evaluation,
                    official_split=official_split,
                    test_accessed=test_accessed,
                )
            )
            all_values[budget].append(evaluation)
            if local:
                local_values[budget].append(evaluation)

    def aggregate(values: dict) -> dict:
        return {
            f"eventness_cached_topk|K={budget}|seed=None": aggregate_evaluations(
                values[budget]
            )
            for budget in D1_BUDGETS
        }

    return records, {
        "all_physical_diagnostic": aggregate(all_values),
        "local_boundary_primary": aggregate(local_values),
    }


def write_json(path: Path, value) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def build_report(summary: dict) -> str:
    primary = summary["metrics"]["local_boundary_primary"]
    lines = [
        "# Dynamic Window Selector D1 AVE validation cached Eventness 指标",
        "",
        "## 边界",
        "",
        "- 仅读取冻结 validation 的 BEATs 特征和 cached Eventness 分数。",
        "- 使用完整 clip 的 non-causal 排名；test 未访问。",
        "- 不节省 BEATs 编码计算，不表述为前置压缩。",
        "- localization 主结果只统计 143 个局部边界物理视频。",
        "",
        "## 局部边界主结果",
        "",
    ]
    for budget in D1_BUDGETS:
        value = primary[
            f"eventness_cached_topk|K={budget}|seed=None"
        ]
        lines.append(
            f"- K={budget}：Hit {value['window_hit_at_k']:.6f}，"
            f"Recall {value['positive_window_recall_at_k']:.6f}，"
            f"首命中排名 {value['mean_first_hit_rank']:.6f}，"
            f"max tIoU {value['mean_max_tiou']:.6f}"
        )
    lines.extend(
        [
            "",
            "## 下一阶段",
            "",
            "- 单独审计冻结 Temporal scorer 的来源、状态和 per-window margin 定义。",
            "- Temporal 未通过审计前，不作 D1 总退出判断。",
        ]
    )
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> dict:
    started_at = datetime.now().astimezone()
    candidate_manifest = args.candidate_manifest.resolve()
    media_inventory = args.media_inventory.resolve()
    decode_preflight_summary = args.decode_preflight_summary.resolve()
    feature_dir = args.feature_dir.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = baseline.load_validation_inputs(
        candidate_manifest,
        media_inventory,
        decode_preflight_summary,
        args.expected_count,
    )
    scores_by_id, feature_inputs = load_frozen_scores(rows, feature_dir)
    records, metrics = evaluate_eventness(rows, scores_by_id)
    records_path = output_dir / "validation_eventness_selection_records.jsonl"
    baseline.write_jsonl(records_path, records)
    summary = {
        "stage": "D1-AVE-validation-eventness-cached-metrics",
        "status": "pass",
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now().astimezone().isoformat(),
        "python_environment": "M4",
        "validation_physical_video_count": len(rows),
        "local_boundary_physical_video_count": sum(
            bool(row["has_local_boundary"]) for row in rows
        ),
        "selection_unit": "original_micro_window",
        "micro_window_sec": 1.0,
        "budgets": list(D1_BUDGETS),
        "noncausal_full_clip_ranking": True,
        "saves_beats_compute": False,
        "all_fixed_equivalence_status": "pass",
        "metrics": metrics,
        "inputs": feature_inputs,
        "outputs": {
            "selection_records": {
                "path": str(records_path.resolve()),
                "sha256": file_sha256(records_path),
            }
        },
        "test_accessed": False,
    }
    write_json(output_dir / "summary.json", summary)
    (output_dir / "report.md").write_text(build_report(summary), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": summary["status"],
                "validation_count": len(rows),
                "local_boundary_count": summary[
                    "local_boundary_physical_video_count"
                ],
                "selection_record_count": len(records),
            },
            ensure_ascii=False,
        )
    )
    return summary


def main() -> int:
    summary = run(parse_args())
    return 0 if summary["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
