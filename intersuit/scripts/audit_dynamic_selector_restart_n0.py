#!/usr/bin/env python
"""审计长视频候选数据的窗口动态范围，不读取媒体或运行模型。"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Iterable

import pandas as pd


FIXED_K = (2, 4, 8)
RATIO_BUDGETS = (0.10, 0.25)
MICRO_WINDOW_SEC = 1.0
MIN_OVERALL_HEADROOM = 0.15
MIN_STRATUM_HEADROOM = 0.10
MIN_PILOT_EVENTS = 100
MIN_PILOT_VIDEOS = 50
BOUNDARY_TOLERANCE_SEC = 0.025


@dataclass(frozen=True)
class EventRow:
    dataset: str
    split: str
    video_id: str
    duration: float
    event_start: float
    event_end: float
    caption_or_label: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--longvale-training", type=Path, required=True)
    parser.add_argument("--longvale-eval", type=Path, required=True)
    parser.add_argument("--unav-train", type=Path, required=True)
    parser.add_argument("--unav-validation", type=Path, required=True)
    parser.add_argument("--unav-test", type=Path, required=True)
    parser.add_argument(
        "--exposure-json",
        action="append",
        default=[],
        type=Path,
        help="只从显式历史 JSON/JSONL 中提取与候选物理 ID 完全相等的字符串。",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("intersuit/harness/artifacts/dynamic_selector_restart_n0"),
    )
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_longvale(path: Path, split: str) -> list[EventRow]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"LongVALE {split} 顶层必须是对象")
    rows: list[EventRow] = []
    for video_id, item in payload.items():
        duration = float(item["duration"])
        pairs = _longvale_segment_pairs(item, split, str(video_id))
        for timestamp, caption in pairs:
            event_start, event_end = _parse_longvale_timestamp(timestamp)
            rows.append(
                _validated_event(
                    "longvale",
                    split,
                    str(video_id),
                    duration,
                    event_start,
                    event_end,
                    str(caption),
                )
            )
    return rows


def longvale_data_quality(path: Path, split: str) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    overruns = []
    for video_id, item in payload.items():
        duration = float(item["duration"])
        for timestamp, _ in _longvale_segment_pairs(item, split, str(video_id)):
            _, event_end = _parse_longvale_timestamp(timestamp)
            if event_end > duration:
                overruns.append(
                    {
                        "video_id": str(video_id),
                        "duration": duration,
                        "original_event_end": event_end,
                        "overrun_sec": event_end - duration,
                    }
                )
    return {
        "boundary_tolerance_sec": BOUNDARY_TOLERANCE_SEC,
        "clamped_event_count": len(overruns),
        "clamped_video_count": len({item["video_id"] for item in overruns}),
        "maximum_overrun_sec": max((item["overrun_sec"] for item in overruns), default=0.0),
        "clamped_events": overruns,
    }


def _longvale_segment_pairs(item: dict, split: str, video_id: str) -> list[tuple[object, str]]:
    if "segments" in item:
        segments = item["segments"]
        if not isinstance(segments, dict) or not segments:
            raise ValueError(f"LongVALE {split}/{video_id} 缺少非空 segments")
        return [(timestamp, str(caption)) for timestamp, caption in segments.items()]
    timestamps = item.get("timestamps")
    sentences = item.get("sentences")
    if not isinstance(timestamps, list) or not isinstance(sentences, list) or not timestamps:
        raise ValueError(f"LongVALE {split}/{video_id} 缺少非空 timestamps/sentences")
    if len(timestamps) != len(sentences):
        raise ValueError(f"LongVALE {split}/{video_id} timestamps/sentences 数量不一致")
    return [(timestamp, str(sentence)) for timestamp, sentence in zip(timestamps, sentences)]


def _parse_longvale_timestamp(value: object) -> tuple[float, float]:
    if isinstance(value, str):
        fields = value.split("_")
    elif isinstance(value, (list, tuple)):
        fields = list(value)
    else:
        fields = []
    if len(fields) != 2:
        raise ValueError(f"LongVALE timestamp 非法：{value}")
    return float(fields[0]), float(fields[1])


def load_unav(path: Path, split: str) -> list[EventRow]:
    frame = pd.read_parquet(path)
    required = {"video_id", "duration", "timestamp", "event_label"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"UnAV-100 {split} 缺字段：{sorted(missing)}")
    rows = []
    for item in frame.to_dict(orient="records"):
        timestamp = list(item["timestamp"])
        if len(timestamp) != 2:
            raise ValueError(f"UnAV-100 {split}/{item['video_id']} timestamp 非法")
        rows.append(
            _validated_event(
                "unav100",
                split,
                str(item["video_id"]),
                float(item["duration"]),
                float(timestamp[0]),
                float(timestamp[1]),
                str(item["event_label"]),
            )
        )
    return rows


def _validated_event(
    dataset: str,
    split: str,
    video_id: str,
    duration: float,
    event_start: float,
    event_end: float,
    caption_or_label: str,
) -> EventRow:
    values = (duration, event_start, event_end)
    if not video_id or not all(math.isfinite(value) for value in values):
        raise ValueError(f"{dataset}/{split} 存在空 ID 或非有限边界")
    if (
        duration <= 0
        or event_start < 0
        or event_end <= event_start
        or event_end > duration + BOUNDARY_TOLERANCE_SEC
    ):
        raise ValueError(
            f"{dataset}/{split}/{video_id} 边界非法："
            f"duration={duration}, segment=[{event_start}, {event_end}]"
        )
    if not caption_or_label.strip():
        raise ValueError(f"{dataset}/{split}/{video_id} 缺少 caption/label")
    return EventRow(
        dataset=dataset,
        split=split,
        video_id=video_id,
        duration=duration,
        event_start=event_start,
        event_end=min(event_end, duration),
        caption_or_label=caption_or_label,
    )


def window_counts(row: EventRow) -> tuple[int, int]:
    total = math.ceil(row.duration / MICRO_WINDOW_SEC)
    positive = 0
    for index in range(total):
        start = index * MICRO_WINDOW_SEC
        end = min(row.duration, (index + 1) * MICRO_WINDOW_SEC)
        if min(end, row.event_end) > max(start, row.event_start):
            positive += 1
    if not 1 <= positive <= total:
        raise ValueError(f"{row.dataset}/{row.split}/{row.video_id} 正窗口数非法")
    return total, positive


def theoretical_random_hit(total: int, positive: int, budget: int) -> float:
    selected = min(max(int(budget), 0), total)
    if selected == 0:
        return 0.0
    negatives = total - positive
    if negatives < selected:
        return 1.0
    return 1.0 - math.comb(negatives, selected) / math.comb(total, selected)


def event_metrics(row: EventRow) -> dict:
    total, positive = window_counts(row)
    metrics = {
        "dataset": row.dataset,
        "split": row.split,
        "video_id": row.video_id,
        "duration": row.duration,
        "event_start": row.event_start,
        "event_end": row.event_end,
        "event_duration": row.event_end - row.event_start,
        "event_duration_ratio": (row.event_end - row.event_start) / row.duration,
        "valid_window_count": total,
        "positive_window_count": positive,
    }
    for budget in FIXED_K:
        hit = theoretical_random_hit(total, positive, budget)
        metrics[f"random_hit_k{budget}"] = hit
        metrics[f"headroom_k{budget}"] = 1.0 - hit
    for ratio in RATIO_BUDGETS:
        budget = max(1, math.ceil(total * ratio))
        key = f"ratio_{int(ratio * 100)}"
        hit = theoretical_random_hit(total, positive, budget)
        metrics[f"budget_{key}"] = budget
        metrics[f"random_hit_{key}"] = hit
        metrics[f"headroom_{key}"] = 1.0 - hit
    return metrics


def summarize(rows: list[EventRow]) -> dict:
    metrics = [event_metrics(row) for row in rows]
    video_ids = {row.video_id for row in rows}
    by_video = Counter(row.video_id for row in rows)
    duration_ratios = [item["event_duration_ratio"] for item in metrics]
    quartile_bounds = _quartile_bounds(duration_ratios)
    strata = {}
    for name, lower, upper in quartile_bounds:
        selected = [
            item
            for item in metrics
            if item["event_duration_ratio"] >= lower
            and (item["event_duration_ratio"] <= upper if name == "q4" else item["event_duration_ratio"] < upper)
        ]
        strata[name] = _aggregate(selected)
    aggregate = _aggregate(metrics)
    aggregate.update(
        {
            "video_count": len(video_ids),
            "event_count": len(rows),
            "single_event_video_count": sum(count == 1 for count in by_video.values()),
            "multi_event_video_count": sum(count > 1 for count in by_video.values()),
            "event_ratio_quartile_strata": strata,
        }
    )
    return aggregate


def _aggregate(metrics: list[dict]) -> dict:
    if not metrics:
        return {"event_count": 0}
    numeric = (
        "duration",
        "event_duration",
        "event_duration_ratio",
        "valid_window_count",
        "positive_window_count",
        "random_hit_k2",
        "random_hit_k4",
        "random_hit_k8",
        "random_hit_ratio_10",
        "random_hit_ratio_25",
        "headroom_k2",
        "headroom_k4",
        "headroom_k8",
        "headroom_ratio_10",
        "headroom_ratio_25",
    )
    result = {"event_count": len(metrics)}
    for key in numeric:
        values = sorted(float(item[key]) for item in metrics)
        result[key] = {
            "mean": sum(values) / len(values),
            "median": _quantile(values, 0.5),
            "q1": _quantile(values, 0.25),
            "q3": _quantile(values, 0.75),
            "min": values[0],
            "max": values[-1],
        }
    return result


def _quartile_bounds(values: list[float]) -> list[tuple[str, float, float]]:
    ordered = sorted(values)
    return [
        ("q1", float("-inf"), _quantile(ordered, 0.25)),
        ("q2", _quantile(ordered, 0.25), _quantile(ordered, 0.50)),
        ("q3", _quantile(ordered, 0.50), _quantile(ordered, 0.75)),
        ("q4", _quantile(ordered, 0.75), float("inf")),
    ]


def _quantile(values: list[float], probability: float) -> float:
    if not values:
        raise ValueError("不能对空集合计算分位数")
    position = (len(values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]
    fraction = position - lower
    return values[lower] * (1.0 - fraction) + values[upper] * fraction


def collect_exact_exposures(paths: Iterable[Path], candidate_ids: set[str]) -> dict[str, list[str]]:
    evidence: dict[str, set[str]] = defaultdict(set)
    for path in paths:
        if not path.exists():
            continue
        if path.suffix == ".jsonl":
            payloads = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
        else:
            payloads = [json.loads(path.read_text(encoding="utf-8"))]
        for payload in payloads:
            for value in _walk_strings(payload):
                for video_id in _candidate_ids_from_string(value, candidate_ids):
                    evidence[video_id].add(str(path.resolve()))
    return {key: sorted(value) for key, value in sorted(evidence.items())}


def _candidate_ids_from_string(value: str, candidate_ids: set[str]) -> set[str]:
    matches = {value} & candidate_ids
    normalized = value.split("?", 1)[0].rstrip("/")
    basename = normalized.rsplit("/", 1)[-1]
    stem = basename.rsplit(".", 1)[0] if "." in basename else basename
    if stem in candidate_ids:
        matches.add(stem)
    for token in re.findall(r"(?<![A-Za-z0-9_-])([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])", value):
        if token in candidate_ids:
            matches.add(token)
    return matches


def _walk_strings(value) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _walk_strings(key)
            yield from _walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)


def qualification(dataset: str, summaries: dict[str, dict], overlap_count: int) -> dict:
    development_splits = [name for name in summaries if name not in {"eval", "test"}]
    test_split = "eval" if dataset == "longvale" else "test"
    test_present = test_split in summaries
    combined_events = sum(summaries[name]["event_count"] for name in development_splits)
    combined_videos = sum(summaries[name]["video_count"] for name in development_splits)
    primary = summaries[development_splits[0]] if development_splits else None
    overall_headroom = primary["headroom_ratio_25"]["mean"] if primary else 0.0
    stratum_headrooms = (
        [
            item["headroom_ratio_25"]["mean"]
            for item in primary["event_ratio_quartile_strata"].values()
            if item.get("event_count", 0)
        ]
        if primary
        else []
    )
    checks = {
        "development_and_test_splits_present": bool(development_splits and test_present),
        "pilot_scale_sufficient": combined_events >= MIN_PILOT_EVENTS
        and combined_videos >= MIN_PILOT_VIDEOS,
        "overall_25pct_headroom_at_least_15pp": overall_headroom >= MIN_OVERALL_HEADROOM,
        "all_event_ratio_quartiles_headroom_at_least_10pp": bool(stratum_headrooms)
        and min(stratum_headrooms) >= MIN_STRATUM_HEADROOM,
        "historical_overlap_zero": overlap_count == 0,
    }
    return {
        "status": "n0_passed_metadata_and_dynamic_range" if all(checks.values()) else "n0_failed_data_or_metric_incompatible",
        "checks": checks,
        "development_event_count": combined_events,
        "development_video_count": combined_videos,
        "primary_development_split": development_splits[0] if development_splits else None,
        "overall_25pct_headroom": overall_headroom,
        "minimum_event_ratio_quartile_25pct_headroom": min(stratum_headrooms) if stratum_headrooms else None,
        "historical_overlap_count": overlap_count,
    }


def write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> None:
    args = parse_args()
    sources = {
        "longvale": {
            "official_repository": "https://github.com/ttgeng233/LongVALE",
            "official_dataset": "https://huggingface.co/datasets/ttgeng233/LongVALE",
            "repository_commit": "2eb88ce3f72458faea6f3db8f841b41e6485a814",
            "files": {
                "training": {
                    "path": str(args.longvale_training),
                    "sha256": sha256_file(args.longvale_training),
                    "data_quality": longvale_data_quality(args.longvale_training, "training"),
                },
                "eval": {
                    "path": str(args.longvale_eval),
                    "sha256": sha256_file(args.longvale_eval),
                    "data_quality": longvale_data_quality(args.longvale_eval, "eval"),
                },
            },
        },
        "unav100": {
            "official_repository": "https://github.com/ttgeng233/UnAV",
            "official_dataset": "https://huggingface.co/datasets/ttgeng233/UnAV-100",
            "repository_commit": "1609deba4fbaf25222c6ee6c7d43b0d0ddfab20d",
            "files": {
                "train": {"path": str(args.unav_train), "sha256": sha256_file(args.unav_train)},
                "validation": {"path": str(args.unav_validation), "sha256": sha256_file(args.unav_validation)},
                "test": {"path": str(args.unav_test), "sha256": sha256_file(args.unav_test)},
            },
        },
        "media_or_features_downloaded": False,
        "audit_kind": "metadata_only",
        "audited_at": datetime.now().astimezone().isoformat(),
    }
    groups = {
        ("longvale", "training"): load_longvale(args.longvale_training, "training"),
        ("longvale", "eval"): load_longvale(args.longvale_eval, "eval"),
        ("unav100", "train"): load_unav(args.unav_train, "train"),
        ("unav100", "validation"): load_unav(args.unav_validation, "validation"),
        ("unav100", "test"): load_unav(args.unav_test, "test"),
    }
    candidate_ids = {row.video_id for rows in groups.values() for row in rows}
    exact_exposures = collect_exact_exposures(args.exposure_json, candidate_ids)
    ids_by_dataset = {
        dataset: {row.video_id for (name, _), rows in groups.items() if name == dataset for row in rows}
        for dataset in ("longvale", "unav100")
    }
    cross_dataset = sorted(ids_by_dataset["longvale"] & ids_by_dataset["unav100"])
    summaries: dict[str, dict[str, dict]] = defaultdict(dict)
    manifest_rows = []
    for (dataset, split), rows in groups.items():
        summaries[dataset][split] = summarize(rows)
        by_video: dict[str, list[EventRow]] = defaultdict(list)
        for row in rows:
            by_video[row.video_id].append(row)
        for video_id, events in sorted(by_video.items()):
            manifest_rows.append(
                {
                    "dataset": dataset,
                    "split": split,
                    "video_id": video_id,
                    "duration": events[0].duration,
                    "event_count": len(events),
                    "historically_exposed": video_id in exact_exposures,
                }
            )
    overlap_by_dataset = {
        dataset: sorted(ids_by_dataset[dataset] & set(exact_exposures)) for dataset in ids_by_dataset
    }
    qualifications = {
        dataset: qualification(dataset, summaries[dataset], len(overlap_by_dataset[dataset]))
        for dataset in summaries
    }
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "candidate_sources.json", sources)
    write_jsonl(output / "metadata_manifest.jsonl", manifest_rows)
    write_json(
        output / "physical_id_cross_audit.json",
        {
            "longvale_unav100_overlap_count": len(cross_dataset),
            "longvale_unav100_overlap_ids": cross_dataset,
            "historical_overlap_by_dataset": overlap_by_dataset,
            "historical_evidence": exact_exposures,
            "exposure_sources": [str(path.resolve()) for path in args.exposure_json],
        },
    )
    write_json(
        output / "dynamic_range_report.json",
        {
            "micro_window_sec": MICRO_WINDOW_SEC,
            "fixed_k": FIXED_K,
            "ratio_budgets": RATIO_BUDGETS,
            "ratio_budget_rounding": "ceil",
            "boundary_tolerance_sec": BOUNDARY_TOLERANCE_SEC,
            "summaries": summaries,
            "qualification": qualifications,
        },
    )
    print(json.dumps(qualifications, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
