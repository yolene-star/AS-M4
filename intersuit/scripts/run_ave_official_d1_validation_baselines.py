#!/usr/bin/env python
"""运行 AVE D1 validation-only 基础窗口策略并冻结 waveform 分数与指标。"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import statistics
from typing import Iterable

import av
import numpy as np
import torch

from intersuit.harness.ave_d1_protocol import (
    BOOTSTRAP_RESAMPLES,
    BOOTSTRAP_SEED,
    D1_BUDGETS,
    WINDOW_COUNT,
    aggregate_evaluations,
    build_five_seed_sample_random_rankings,
    build_micro_window_timestamps,
    evaluate_selection,
    normalize_target_intervals,
    oracle_ranked_indices,
    paired_bootstrap_mean_difference,
    ranked_score_indices,
    uniform_topk_indices,
    waveform_energy_onset_scores,
)
from intersuit.harness.dynamic_selector_contract import (
    FIXED_CONTROL_SEEDS,
    ValidatedTensorCache,
)


EXPECTED_VALIDATION_COUNT = 391
EXPECTED_LOCAL_BOUNDARY_COUNT = 143
SAMPLE_RATE = 16_000
WAVEFORM_POLICY_VERSION = "rms25ms-positive-flux-v1"


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
        "--output-dir",
        type=Path,
        default=Path(
            "intersuit/harness/artifacts/"
            "dynamic_selector_d1_ave_validation_baselines"
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


def load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_validation_inputs(
    candidate_manifest: Path,
    media_inventory: Path,
    decode_preflight_summary: Path,
    expected_count: int,
) -> list[dict]:
    candidates = load_jsonl(candidate_manifest)
    media_rows = load_jsonl(media_inventory)
    preflight = json.loads(decode_preflight_summary.read_text(encoding="utf-8"))
    if len(candidates) != expected_count or len(media_rows) != expected_count:
        raise ValueError("validation candidate/media 数量与冻结数量不一致")
    if (
        preflight.get("status") != "pass"
        or preflight.get("sample_count") != expected_count
        or preflight.get("invalid_count") != 0
        or preflight.get("test_accessed") is not False
    ):
        raise ValueError("validation decode preflight 未通过或越过 test 边界")
    candidate_by_id = {row["youtube_id"]: row for row in candidates}
    media_by_id = {row["youtube_id"]: row for row in media_rows}
    if len(candidate_by_id) != expected_count or len(media_by_id) != expected_count:
        raise ValueError("validation 输入含重复物理视频")
    if set(candidate_by_id) != set(media_by_id):
        raise ValueError("validation candidate 与 media 物理视频集合不一致")
    rows = []
    for youtube_id in sorted(candidate_by_id):
        candidate = candidate_by_id[youtube_id]
        media = media_by_id[youtube_id]
        if (
            candidate.get("official_split") != "validation"
            or media.get("official_split") != "validation"
            or media.get("test_sealed") is not False
            or media.get("status") != "present"
        ):
            raise ValueError("输入含非 validation、sealed 或缺失媒体")
        rows.append({**candidate, **media})
    local_count = sum(bool(row["has_local_boundary"]) for row in rows)
    if local_count != EXPECTED_LOCAL_BOUNDARY_COUNT:
        raise ValueError(
            f"局部边界物理视频数变化：expected={EXPECTED_LOCAL_BOUNDARY_COUNT}, "
            f"actual={local_count}"
        )
    return rows


def decode_audio_mono_16k(path: Path) -> torch.Tensor:
    resampler = av.AudioResampler(format="fltp", layout="mono", rate=SAMPLE_RATE)
    arrays = []
    with av.open(str(path), mode="r") as container:
        for frame in container.decode(audio=0):
            for converted in resampler.resample(frame):
                arrays.append(converted.to_ndarray())
        for converted in resampler.resample(None):
            arrays.append(converted.to_ndarray())
    if not arrays:
        raise ValueError(f"PyAV 未解码出音频：{path}")
    waveform = np.concatenate(arrays, axis=-1).reshape(-1)
    value = torch.from_numpy(np.ascontiguousarray(waveform)).float()
    if value.ndim != 1 or value.numel() == 0 or not torch.isfinite(value).all():
        raise ValueError(f"PyAV 音频非法：{path}")
    return value


def load_cache_audits(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("waveform cache audit manifest 损坏")
    return payload


def write_cache_audits(path: Path, audits: dict[str, dict]) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(audits, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def extract_or_load_waveform_scores(
    rows: list[dict],
    cache_root: Path,
) -> tuple[dict[str, dict], int, int]:
    cache = ValidatedTensorCache(cache_root / "tensors")
    audit_path = cache_root / "audio_audits.json"
    audits = load_cache_audits(audit_path)
    results = {}
    hit_count = 0
    new_count = 0
    for row in rows:
        youtube_id = row["youtube_id"]
        input_sha = row["media_sha256"]
        if youtube_id in cache.entries:
            if youtube_id not in audits:
                raise ValueError(f"cache audit missing: {youtube_id}")
            scores = cache.get(youtube_id, input_sha256=input_sha)
            audit = audits[youtube_id]
            hit_count += 1
        else:
            waveform = decode_audio_mono_16k(Path(row["media_path"]))
            energy, onset, audit = waveform_energy_onset_scores(
                waveform,
                SAMPLE_RATE,
            )
            scores = torch.stack([energy, onset], dim=0)
            cache.put(youtube_id, scores, input_sha256=input_sha)
            audits[youtube_id] = {
                **audit,
                "media_sha256": input_sha,
                "policy_version": WAVEFORM_POLICY_VERSION,
            }
            audit = audits[youtube_id]
            write_cache_audits(audit_path, audits)
            new_count += 1
        if scores.shape != (2, WINDOW_COUNT) or not torch.isfinite(scores).all():
            raise ValueError(f"waveform score cache 形状或数值非法：{youtube_id}")
        if (
            audit.get("media_sha256") != input_sha
            or audit.get("policy_version") != WAVEFORM_POLICY_VERSION
        ):
            raise ValueError(f"waveform score cache audit 版本或 SHA 不一致：{youtube_id}")
        results[youtube_id] = {
            "energy_scores": scores[0].tolist(),
            "onset_scores": scores[1].tolist(),
            "audio_audit": audit,
        }
    return results, hit_count, new_count


def evaluation_record(
    *,
    youtube_id: str,
    has_local_boundary: bool,
    target_intervals: list[tuple[float, float]],
    evaluation,
    random_seed: int | None = None,
    official_split: str = "validation",
    test_accessed: bool = False,
) -> dict:
    return {
        "youtube_id": youtube_id,
        "official_split": official_split,
        "has_local_boundary": has_local_boundary,
        "target_intervals": [list(value) for value in target_intervals],
        **asdict(evaluation),
        "random_seed": random_seed,
        "selection_unit": "original_micro_window",
        "micro_window_sec": 1.0,
        "test_accessed": test_accessed,
    }


def run_strategies(
    rows: list[dict],
    scores_by_id: dict[str, dict],
    *,
    official_split: str = "validation",
    test_accessed: bool = False,
) -> tuple[list[dict], dict]:
    timestamps = build_micro_window_timestamps()
    valid_indices = list(range(WINDOW_COUNT))
    records = []
    typed_by_key = defaultdict(list)
    typed_local_by_key = defaultdict(list)
    oracle_hits_by_budget = defaultdict(list)
    random_hits_by_budget_sample = defaultdict(lambda: defaultdict(list))

    for row in rows:
        youtube_id = row["youtube_id"]
        intervals = normalize_target_intervals(row["annotations"])
        local = bool(row["has_local_boundary"])
        energy = torch.tensor(scores_by_id[youtube_id]["energy_scores"])
        onset = torch.tensor(scores_by_id[youtube_id]["onset_scores"])
        full_rankings = {
            "energy_topk": ranked_score_indices(energy, timestamps),
            "onset_topk": ranked_score_indices(onset, timestamps),
            "oracle_topk": oracle_ranked_indices(timestamps, intervals),
        }

        all_evaluation = evaluate_selection(
            strategy="all",
            budget=WINDOW_COUNT,
            ranked_selected_indices=valid_indices,
            timestamps=timestamps,
            target_intervals=intervals,
        )
        records.append(
            evaluation_record(
                youtube_id=youtube_id,
                has_local_boundary=local,
                target_intervals=intervals,
                evaluation=all_evaluation,
                official_split=official_split,
                test_accessed=test_accessed,
            )
        )
        typed_by_key[("all", WINDOW_COUNT, None)].append(all_evaluation)
        if local:
            typed_local_by_key[("all", WINDOW_COUNT, None)].append(all_evaluation)

        random_rankings = build_five_seed_sample_random_rankings(
            valid_indices,
            youtube_id,
        )
        for budget in D1_BUDGETS:
            uniform_selected = uniform_topk_indices(valid_indices, budget)
            uniform_evaluation = evaluate_selection(
                strategy="uniform_topk",
                budget=budget,
                ranked_selected_indices=uniform_selected,
                timestamps=timestamps,
                target_intervals=intervals,
            )
            records.append(
                evaluation_record(
                    youtube_id=youtube_id,
                    has_local_boundary=local,
                    target_intervals=intervals,
                    evaluation=uniform_evaluation,
                    official_split=official_split,
                    test_accessed=test_accessed,
                )
            )
            typed_by_key[("uniform_topk", budget, None)].append(uniform_evaluation)
            if local:
                typed_local_by_key[("uniform_topk", budget, None)].append(
                    uniform_evaluation
                )

            for strategy, ranking in full_rankings.items():
                evaluation = evaluate_selection(
                    strategy=strategy,
                    budget=budget,
                    ranked_selected_indices=ranking,
                    timestamps=timestamps,
                    target_intervals=intervals,
                )
                records.append(
                    evaluation_record(
                        youtube_id=youtube_id,
                        has_local_boundary=local,
                        target_intervals=intervals,
                        evaluation=evaluation,
                        official_split=official_split,
                        test_accessed=test_accessed,
                    )
                )
                typed_by_key[(strategy, budget, None)].append(evaluation)
                if local:
                    typed_local_by_key[(strategy, budget, None)].append(evaluation)
                    if strategy == "oracle_topk":
                        oracle_hits_by_budget[budget].append(evaluation.window_hit)

            for seed_text, ranking in random_rankings.items():
                seed = int(seed_text)
                evaluation = evaluate_selection(
                    strategy="random_topk",
                    budget=budget,
                    ranked_selected_indices=ranking,
                    timestamps=timestamps,
                    target_intervals=intervals,
                )
                records.append(
                    evaluation_record(
                        youtube_id=youtube_id,
                        has_local_boundary=local,
                        target_intervals=intervals,
                        evaluation=evaluation,
                        random_seed=seed,
                        official_split=official_split,
                        test_accessed=test_accessed,
                    )
                )
                typed_by_key[("random_topk", budget, seed)].append(evaluation)
                if local:
                    typed_local_by_key[("random_topk", budget, seed)].append(
                        evaluation
                    )
                    random_hits_by_budget_sample[budget][youtube_id].append(
                        evaluation.window_hit
                    )

    def aggregate_map(values_by_key) -> dict:
        return {
            f"{strategy}|K={budget}|seed={seed}": aggregate_evaluations(values)
            for (strategy, budget, seed), values in sorted(
                values_by_key.items(),
                key=lambda item: (
                    item[0][0],
                    item[0][1],
                    -1 if item[0][2] is None else item[0][2],
                ),
            )
        }

    random_seed_summary = {}
    for budget in D1_BUDGETS:
        per_seed = {
            str(seed): aggregate_evaluations(
                typed_local_by_key[("random_topk", budget, seed)]
            )
            for seed in FIXED_CONTROL_SEEDS
        }
        metric_names = (
            "window_hit_at_k",
            "positive_window_recall_at_k",
            "mean_first_hit_rank",
            "mean_max_tiou",
            "mean_compression_ratio",
        )
        def numeric_values(metric: str) -> list[float]:
            return [
                float(value[metric])
                for value in per_seed.values()
                if value[metric] is not None
            ]

        random_seed_summary[f"K={budget}"] = {
            "per_seed": per_seed,
            "mean": {
                metric: (
                    statistics.fmean(numeric_values(metric))
                    if numeric_values(metric)
                    else None
                )
                for metric in metric_names
            },
            "population_std": {
                metric: (
                    statistics.pstdev(numeric_values(metric))
                    if numeric_values(metric)
                    else None
                )
                for metric in metric_names
            },
        }

    oracle_vs_random = {}
    local_ids = [row["youtube_id"] for row in rows if row["has_local_boundary"]]
    for budget in D1_BUDGETS:
        random_means = [
            statistics.fmean(random_hits_by_budget_sample[budget][youtube_id])
            for youtube_id in local_ids
        ]
        oracle_vs_random[f"K={budget}"] = paired_bootstrap_mean_difference(
            oracle_hits_by_budget[budget],
            random_means,
            resamples=BOOTSTRAP_RESAMPLES,
            seed=BOOTSTRAP_SEED,
        )
    return records, {
        "all_physical_diagnostic": aggregate_map(typed_by_key),
        "local_boundary_primary": aggregate_map(typed_local_by_key),
        "random_local_boundary_summary": random_seed_summary,
        "oracle_minus_random_window_hit_paired_bootstrap": oracle_vs_random,
    }


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def build_report(summary: dict) -> str:
    primary = summary["metrics"]["local_boundary_primary"]
    lines = [
        "# Dynamic Window Selector D1 AVE validation 基础策略报告",
        "",
        "## 边界",
        "",
        "- 仅运行冻结 validation；test 未访问。",
        "- 原子选择单位固定为 10 个原始 1 秒 micro-window。",
        "- localization 主结果只统计 143 个局部边界物理视频。",
        "- cached Eventness 与 Temporal scorer 尚未接入，本报告不作 D1 最终退出判断。",
        "",
        "## K=4 局部边界主结果",
        "",
    ]
    for strategy in (
        "uniform_topk",
        "energy_topk",
        "onset_topk",
        "oracle_topk",
    ):
        value = primary[f"{strategy}|K=4|seed=None"]
        lines.append(
            f"- `{strategy}`：Hit {value['window_hit_at_k']:.6f}，"
            f"Recall {value['positive_window_recall_at_k']:.6f}，"
            f"首命中排名 {value['mean_first_hit_rank']:.6f}，"
            f"max tIoU {value['mean_max_tiou']:.6f}"
        )
    random = summary["metrics"]["random_local_boundary_summary"]["K=4"]
    lines.extend(
        [
            (
                "- `random_topk` 五 seed 均值："
                f"Hit {random['mean']['window_hit_at_k']:.6f}，"
                f"Recall {random['mean']['positive_window_recall_at_k']:.6f}，"
                f"Hit std {random['population_std']['window_hit_at_k']:.6f}"
            ),
            "",
            "## 当前门禁状态",
            "",
            f"- Oracle Hit@4：{primary['oracle_topk|K=4|seed=None']['window_hit_at_k']:.6f}",
            (
                "- Oracle-Random Hit@4 paired bootstrap："
                f"`{summary['metrics']['oracle_minus_random_window_hit_paired_bootstrap']['K=4']}`"
            ),
            f"- test accessed：`{str(summary['test_accessed']).lower()}`",
            "- D1 总门禁：`pending_cached_strategies_and_all_fixed_equivalence`",
        ]
    )
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> dict:
    started_at = datetime.now().astimezone()
    candidate_manifest = args.candidate_manifest.resolve()
    media_inventory = args.media_inventory.resolve()
    decode_preflight_summary = args.decode_preflight_summary.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = load_validation_inputs(
        candidate_manifest,
        media_inventory,
        decode_preflight_summary,
        args.expected_count,
    )
    scores_by_id, cache_hit_count, newly_extracted_count = (
        extract_or_load_waveform_scores(rows, output_dir / "waveform_score_cache")
    )
    score_rows = [
        {
            "youtube_id": row["youtube_id"],
            "official_split": "validation",
            "media_sha256": row["media_sha256"],
            **scores_by_id[row["youtube_id"]],
            "test_accessed": False,
        }
        for row in rows
    ]
    score_path = output_dir / "validation_waveform_scores.jsonl"
    write_jsonl(score_path, score_rows)
    selection_records, metrics = run_strategies(rows, scores_by_id)
    selection_path = output_dir / "validation_selection_records.jsonl"
    write_jsonl(selection_path, selection_records)
    summary = {
        "stage": "D1-AVE-validation-baseline-strategies",
        "status": "validation_baselines_frozen",
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now().astimezone().isoformat(),
        "python_environment": "M4",
        "pyav_version": av.__version__,
        "sample_rate": SAMPLE_RATE,
        "waveform_policy_version": WAVEFORM_POLICY_VERSION,
        "selection_unit": "original_micro_window",
        "micro_window_sec": 1.0,
        "window_count": WINDOW_COUNT,
        "budgets": list(D1_BUDGETS),
        "validation_physical_video_count": len(rows),
        "local_boundary_physical_video_count": sum(
            bool(row["has_local_boundary"]) for row in rows
        ),
        "cache_hit_count": cache_hit_count,
        "newly_extracted_count": newly_extracted_count,
        "fixed_random_seeds": list(FIXED_CONTROL_SEEDS),
        "bootstrap_seed": BOOTSTRAP_SEED,
        "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
        "metrics": metrics,
        "inputs": {
            "candidate_manifest": {
                "path": str(candidate_manifest),
                "sha256": file_sha256(candidate_manifest),
            },
            "media_inventory": {
                "path": str(media_inventory),
                "sha256": file_sha256(media_inventory),
            },
            "decode_preflight_summary": {
                "path": str(decode_preflight_summary),
                "sha256": file_sha256(decode_preflight_summary),
            },
        },
        "outputs": {
            "waveform_scores": {
                "path": str(score_path),
                "sha256": file_sha256(score_path),
            },
            "selection_records": {
                "path": str(selection_path),
                "sha256": file_sha256(selection_path),
            },
        },
        "test_accessed": False,
        "cached_eventness_run": False,
        "temporal_conf_run": False,
        "all_fixed_equivalence_status": "pending",
        "d1_exit_status": "pending_cached_strategies_and_all_fixed_equivalence",
    }
    summary_path = output_dir / "summary.json"
    write_json(summary_path, summary)
    (output_dir / "report.md").write_text(build_report(summary), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": summary["status"],
                "validation_count": len(rows),
                "local_boundary_count": summary[
                    "local_boundary_physical_video_count"
                ],
                "cache_hit_count": cache_hit_count,
                "newly_extracted_count": newly_extracted_count,
            },
            ensure_ascii=False,
        )
    )
    return summary


def main() -> int:
    run(parse_args())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
