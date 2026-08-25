#!/usr/bin/env python3
"""冻结 UnAV-100 S1 历史排除 ledger 与 train/validation 媒体 canary 计划。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = REPO_ROOT / "intersuit"
DEFAULT_CONTRACT = INTERSUIT_ROOT / "configs/m4_ptpa_unav_s1_reconstruction_v1.json"
DEFAULT_OUTPUT = INTERSUIT_ROOT / "datasets/UNAV_S1_V1/qualification_v1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_repo(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def stable_key(seed: int, namespace: str, value: str) -> str:
    return hashlib.sha256(f"{namespace}\0{seed}\0{value}".encode("utf-8")).hexdigest()


def load_contract(path: Path) -> tuple[dict[str, Any], dict[str, Path]]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    if contract.get("status") not in {
        "metadata_locked_media_probe_only",
        "canary2_media_download_authorized_effect_locked",
        "blocked_media_canary2_youtube_bot_auth",
    }:
        raise ValueError("UnAV S1 contract status 漂移")
    permissions = contract["permissions"]
    if permissions.get("metadata_audit_allowed") is not True:
        raise PermissionError("UnAV metadata audit 未授权")
    for key in (
        "train_validation_media_download_allowed", "feature_extraction_allowed",
        "effect_access_allowed", "test_media_download_allowed",
        "test_feature_extraction_allowed", "test_effect_access_allowed",
        "training_allowed", "redistribution_allowed",
    ):
        if permissions.get(key) is not False:
            raise PermissionError(f"UnAV 禁止项未 fail-closed：{key}")
    root = resolve_repo(contract["metadata"]["root"])
    paths = {}
    for name, expected in contract["metadata"]["files"].items():
        path_value = root / name
        if (
            not path_value.is_file()
            or path_value.stat().st_size != expected["size"]
            or sha256(path_value) != expected["sha256"]
        ):
            raise ValueError(f"UnAV 官方 metadata 缺失或漂移：{name}")
        paths[name] = path_value
    return contract, paths


def load_source_csv(path: Path) -> dict[str, dict[str, Any]]:
    output = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.reader(handle):
            if len(row) != 4:
                raise ValueError(f"UnAV source CSV 列数异常：{row}")
            video_id, start, end, split = row
            canonical_split = "validation" if split == "validation" else split
            if video_id in output:
                raise ValueError(f"UnAV source CSV video_id 重复：{video_id}")
            output[video_id] = {
                "source_start_sec": float(start),
                "source_end_sec": float(end),
                "split": canonical_split,
            }
    return output


def load_exclusions(contract: dict[str, Any]) -> dict[str, set[str]]:
    known = contract["known_exclusions"]
    historical_path = resolve_repo(known["historical_unav_inventory"]["path"])
    historical = set(
        json.loads(historical_path.read_text(encoding="utf-8"))[
            "historical_overlap_by_dataset"
        ]["unav100"]
    )
    selector_path = resolve_repo(known["used_unav_validation_media"]["path"])
    selector = {
        str(row["video_id"])
        for row in json.loads(selector_path.read_text(encoding="utf-8"))["entries"]
    }
    llp_path = resolve_repo(known["llp_pilot150"]["path"])
    llp = {str(row["physical_media_id"]) for row in read_jsonl(llp_path)}
    if len(historical) != known["historical_unav_inventory"]["count"]:
        raise ValueError("UnAV historical exclusion count 漂移")
    if len(selector) != known["used_unav_validation_media"]["count"]:
        raise ValueError("UnAV selector exclusion count 漂移")
    if len(llp) != known["llp_pilot150"]["count"]:
        raise ValueError("LLP pilot exclusion count 漂移")
    return {
        "historical_inventory": historical,
        "used_unav_validation_selector": selector,
        "llp_pilot150": llp,
    }


def load_official_rows(
    paths: dict[str, Path], source: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    all_rows = []
    ids_by_split = {}
    split_files = {
        "train": "train.parquet",
        "validation": "validation.parquet",
        "test": "test.parquet",
    }
    for split, filename in split_files.items():
        frame = pd.read_parquet(paths[filename])
        required = {"video_id", "duration", "timestamp", "event_label"}
        if set(frame.columns) != required:
            raise ValueError(f"UnAV {split} schema 漂移：{list(frame.columns)}")
        grouped = []
        for video_id, group in frame.groupby("video_id", sort=True):
            video_id = str(video_id)
            durations = {float(value) for value in group["duration"]}
            if len(durations) != 1:
                raise ValueError(f"UnAV duration 不唯一：{video_id}")
            duration = next(iter(durations))
            events = []
            for _, item in group.iterrows():
                timestamp = list(item["timestamp"])
                if len(timestamp) != 2:
                    raise ValueError(f"UnAV timestamp 不是二元组：{video_id}")
                onset, offset = map(float, timestamp)
                if onset < 0 or offset <= onset or offset > duration + 1e-6:
                    raise ValueError(f"UnAV timestamp 非法：{video_id}/{timestamp}/{duration}")
                events.append(
                    {
                        "onset": onset,
                        "offset": offset,
                        "event_label": str(item["event_label"]),
                    }
                )
            source_row = source.get(video_id)
            if source_row is None or source_row["split"] != split:
                raise ValueError(f"UnAV source CSV 缺失或 split 漂移：{video_id}")
            source_duration = source_row["source_end_sec"] - source_row["source_start_sec"]
            if not math.isclose(source_duration, duration, abs_tol=0.11):
                raise ValueError(
                    f"UnAV source duration 漂移：{video_id}/{source_duration}/{duration}"
                )
            grouped.append(
                {
                    "physical_media_id": video_id,
                    "official_split": split,
                    "duration_sec": duration,
                    "source_start_sec": source_row["source_start_sec"],
                    "source_end_sec": source_row["source_end_sec"],
                    "event_count": len(events),
                    "event_labels": sorted({event["event_label"] for event in events}),
                    "events": sorted(
                        events,
                        key=lambda event: (
                            event["onset"], event["offset"], event["event_label"]
                        ),
                    ),
                }
            )
        ids_by_split[split] = {row["physical_media_id"] for row in grouped}
        all_rows.extend(grouped)
    if (
        ids_by_split["train"] & ids_by_split["validation"]
        or ids_by_split["train"] & ids_by_split["test"]
        or ids_by_split["validation"] & ids_by_split["test"]
    ):
        raise ValueError("UnAV official split 物理ID重叠")
    if set(source) != set().union(*ids_by_split.values()):
        raise ValueError("UnAV source CSV 与 parquet 物理ID集合不一致")
    return all_rows


def duration_bin(duration: float) -> str:
    if duration < 30:
        return "short_lt30"
    if duration < 45:
        return "medium_30_45"
    return "long_ge45"


def complexity_bin(event_count: int) -> str:
    return "single_event" if event_count == 1 else "multi_event"


def select_split_pilot(
    rows: list[dict[str, Any]], split: str, limit: int, seed: int
) -> list[dict[str, Any]]:
    candidates = [row for row in rows if row["official_split"] == split and row["eligible"]]
    strata = [
        (duration_name, complexity_name)
        for duration_name in ("short_lt30", "medium_30_45", "long_ge45")
        for complexity_name in ("single_event", "multi_event")
    ]
    selected = []
    used = set()
    covered_labels = set()
    while len(selected) < limit:
        stratum = strata[len(selected) % len(strata)]
        pool = [
            row for row in candidates
            if row["physical_media_id"] not in used
            and (duration_bin(row["duration_sec"]), complexity_bin(row["event_count"]))
            == stratum
        ]
        if not pool:
            pool = [row for row in candidates if row["physical_media_id"] not in used]
        if not pool:
            raise ValueError(f"UnAV {split} canary 候选不足")
        chosen = sorted(
            pool,
            key=lambda row: (
                -len(set(row["event_labels"]) - covered_labels),
                stable_key(seed, f"{split}:{stratum}", row["physical_media_id"]),
            ),
        )[0]
        selected.append(chosen)
        used.add(chosen["physical_media_id"])
        covered_labels.update(chosen["event_labels"])
    return selected


def build_outputs(contract_path: Path) -> dict[str, Any]:
    contract, paths = load_contract(contract_path)
    source = load_source_csv(paths["download_video.csv"])
    exclusions = load_exclusions(contract)
    official_rows = load_official_rows(paths, source)
    for row in official_rows:
        reasons = sorted(
            name for name, ids in exclusions.items() if row["physical_media_id"] in ids
        )
        row["exclusion_reasons"] = reasons
        row["eligible"] = not reasons
        row["test_effect_locked"] = row["official_split"] == "test"

    expected_official = contract["official_counts"]
    official_summary = {}
    eligible_summary = {}
    for split in ("train", "validation", "test"):
        split_rows = [row for row in official_rows if row["official_split"] == split]
        eligible_rows = [row for row in split_rows if row["eligible"]]
        labels = {label for row in split_rows for label in row["event_labels"]}
        eligible_labels = {label for row in eligible_rows for label in row["event_labels"]}
        per_label_videos = Counter(
            label for row in eligible_rows for label in row["event_labels"]
        )
        official_summary[split] = {
            "videos": len(split_rows),
            "events": sum(row["event_count"] for row in split_rows),
            "labels": len(labels),
        }
        eligible_summary[split] = {
            "excluded": len(split_rows) - len(eligible_rows),
            "remaining": len(eligible_rows),
            "labels": len(eligible_labels),
            "min_unique_videos_per_label": min(per_label_videos.values()),
        }
        if official_summary[split] != expected_official[split]:
            raise ValueError(f"UnAV official count 漂移：{split}")
        if eligible_summary[split] != contract["known_exclusions"]["expected_after_union"][split]:
            raise ValueError(f"UnAV eligible count 漂移：{split}")

    plan_rows = []
    pilot_config = contract["pilot"]
    for split in pilot_config["splits"]:
        selected = select_split_pilot(
            official_rows,
            split,
            int(pilot_config["videos_per_split"]),
            int(pilot_config["selection_seed"]),
        )
        for row in selected:
            plan_rows.append(
                {
                    "selection_rank": len(plan_rows) + 1,
                    "physical_media_id": row["physical_media_id"],
                    "official_split": split,
                    "source_url": f"https://www.youtube.com/watch?v={row['physical_media_id']}",
                    "source_start_sec": row["source_start_sec"],
                    "source_end_sec": row["source_end_sec"],
                    "duration_sec": row["duration_sec"],
                    "event_count": row["event_count"],
                    "event_labels": row["event_labels"],
                    "duration_bin": duration_bin(row["duration_sec"]),
                    "complexity_bin": complexity_bin(row["event_count"]),
                    "historical_overlap": False,
                    "anonymous_probe_allowed": True,
                    "media_download_allowed": False,
                    "feature_extraction_allowed": False,
                    "effect_access_allowed": False,
                    "test_effect_access_allowed": False,
                }
            )
    if len(plan_rows) != 20 or {row["official_split"] for row in plan_rows} != {
        "train", "validation"
    }:
        raise ValueError("UnAV canary20 计划数量/split 异常")
    return {
        "contract": contract,
        "paths": paths,
        "official_rows": official_rows,
        "plan_rows": plan_rows,
        "summary": {
            "status": "PASS_METADATA_LOCKED_PROBE_ONLY",
            "official": official_summary,
            "eligible": eligible_summary,
            "exclusion_reason_counts": dict(
                sorted(
                    Counter(
                        reason for row in official_rows for reason in row["exclusion_reasons"]
                    ).items()
                )
            ),
            "pilot": {
                "rows": len(plan_rows),
                "split_counts": dict(sorted(Counter(row["official_split"] for row in plan_rows).items())),
                "unique_labels": len({label for row in plan_rows for label in row["event_labels"]}),
                "duration_bins": dict(sorted(Counter(row["duration_bin"] for row in plan_rows).items())),
                "complexity_bins": dict(sorted(Counter(row["complexity_bin"] for row in plan_rows).items())),
                "test_rows": 0,
                "media_download_allowed": False,
            },
            "formal_test_effect_accessed": False,
            "training_performed": False,
        },
    }


def build_report(summary: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# UnAV-100 S1 metadata 与媒体 canary 计划审查", "",
            f"- 状态：`{summary['status']}`。",
            "- 官方 train/validation/test 均为强时间标注；物理 split 交集为0。",
            f"- 排除后 train/validation/test：{summary['eligible']['train']['remaining']}/"
            f"{summary['eligible']['validation']['remaining']}/"
            f"{summary['eligible']['test']['remaining']} 个视频。",
            "- 三个 split 排除后仍覆盖100类。",
            f"- canary：{summary['pilot']['rows']}条，train/validation各10，"
            f"覆盖{summary['pilot']['unique_labels']}类；test 0条。",
            "- 当前只允许匿名metadata probe；媒体下载、特征、效果、训练均关闭。",
            "- Hugging Face媒体为13分卷、约65.78GB，未执行整包下载。", "",
        ]
    )


def run(args: argparse.Namespace) -> dict[str, Any]:
    if os.environ.get("CONDA_DEFAULT_ENV") != "M4":
        raise RuntimeError("必须通过 conda run -n M4 启动")
    result = build_outputs(args.contract)
    args.output.mkdir(parents=True, exist_ok=True)
    ledger_path = args.output / "unav_s1_physical_ledger.jsonl"
    plan_path = args.output / "unav_s1_media_canary20_plan_locked.jsonl"
    summary_path = args.output / "summary.json"
    report_path = args.output / "REVIEW_ZH.md"
    write_jsonl(ledger_path, result["official_rows"])
    write_jsonl(plan_path, result["plan_rows"])
    write_json(summary_path, result["summary"])
    report_path.write_text(build_report(result["summary"]), encoding="utf-8")
    write_json(
        args.output / "manifest_lock.json",
        {
            "inputs": {
                "contract": {"path": str(args.contract.resolve()), "sha256": sha256(args.contract)},
                **{
                    name: {"path": str(path.resolve()), "sha256": sha256(path)}
                    for name, path in result["paths"].items()
                },
            },
            "outputs": {
                path.name: sha256(path)
                for path in (ledger_path, plan_path, summary_path, report_path)
            },
            "implementation_sha256": sha256(Path(__file__).resolve()),
        },
    )
    return result["summary"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


if __name__ == "__main__":
    print(json.dumps(run(parse_args()), ensure_ascii=False, indent=2))
