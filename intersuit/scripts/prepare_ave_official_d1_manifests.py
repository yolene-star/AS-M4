#!/usr/bin/env python
"""生成 AVE 官方物理视频级 D1 候选清单并执行历史污染审计。"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Iterable


SPLITS = ("train", "validation", "test")
ORDER_FILENAMES = {
    "train": "train_order.h5",
    "validation": "val_order.h5",
    "test": "test_order.h5",
}
HISTORICAL_ARTIFACT_MARKERS = (
    "ave_hf",
    "stage2",
    "dev760",
    "frozen_av",
    "video_window_weighting_ave",
    "semantic_contrast",
)
EXPOSURE_CATEGORIES = (
    "ave_hf_local_assets",
    "ave_hf_manifests",
    "stage2",
    "historical_harness",
    "m4_it",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ave-repo", type=Path, default=Path("intersuit/datasets/AVE"))
    parser.add_argument("--datasets-root", type=Path, default=Path("intersuit/datasets"))
    parser.add_argument("--artifacts-root", type=Path, default=Path("intersuit/harness/artifacts"))
    parser.add_argument("--m4-it", type=Path, default=Path("intersuit/inputs/texts/m4-it-qwen.json"))
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("intersuit/harness/artifacts/dynamic_selector_d1_ave_official"),
    )
    return parser.parse_args()


def load_annotations(path: Path) -> list[dict]:
    rows = []
    for annotation_index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        fields = line.split("&")
        if len(fields) != 5:
            raise ValueError(f"Annotations.txt 第 {annotation_index + 1} 行不是 5 字段")
        event_label, youtube_id, quality, start_text, end_text = fields
        start = int(start_text)
        end = int(end_text)
        if not youtube_id or start < 0 or end > 10 or start > end:
            raise ValueError(f"Annotations.txt 第 {annotation_index + 1} 行边界或 ID 非法")
        rows.append(
            {
                "annotation_index": annotation_index,
                "event_label": event_label,
                "youtube_id": youtube_id,
                "quality": quality,
                "event_start_sec": start,
                "event_end_sec": end,
                "has_local_boundary": start > 0 or end < 10,
            }
        )
    return rows


def validate_orders(orders: dict[str, list[int]], annotation_count: int) -> None:
    flattened = []
    for split in SPLITS:
        values = orders[split]
        if len(values) != len(set(values)):
            raise ValueError(f"{split} order 含重复 annotation index")
        if any(index < 0 or index >= annotation_count for index in values):
            raise ValueError(f"{split} order 含越界 annotation index")
        flattened.extend(values)
    if len(flattened) != annotation_count or set(flattened) != set(range(annotation_count)):
        raise ValueError("train/validation/test order 未精确覆盖全部 annotation 行")


def build_split_rows(
    annotations: list[dict],
    orders: dict[str, list[int]],
) -> tuple[dict[str, list[dict]], dict[str, dict]]:
    raw_by_split = {}
    physical: dict[str, dict] = {}
    for split in SPLITS:
        raw_rows = []
        for order_position, annotation_index in enumerate(orders[split]):
            row = {
                **annotations[annotation_index],
                "official_split": split,
                "official_order_position": order_position,
            }
            raw_rows.append(row)
            group = physical.setdefault(
                row["youtube_id"],
                {
                    "youtube_id": row["youtube_id"],
                    "official_splits": set(),
                    "annotations": [],
                },
            )
            group["official_splits"].add(split)
            group["annotations"].append(row)
        raw_by_split[split] = raw_rows
    return raw_by_split, physical


def collect_historical_exposures(
    official_ids: set[str],
    datasets_root: Path,
    artifacts_root: Path,
    m4_it_path: Path,
) -> tuple[dict[str, dict], list[dict]]:
    evidence: dict[str, dict] = {}
    parse_failures: list[dict] = []

    def record(youtube_id: str, category: str, source_path: Path) -> None:
        if youtube_id not in official_ids:
            return
        row = evidence.setdefault(
            youtube_id,
            {"youtube_id": youtube_id, "categories": set(), "source_paths": set()},
        )
        row["categories"].add(category)
        if len(row["source_paths"]) < 20:
            row["source_paths"].add(str(source_path.resolve()))

    for dataset_dir in sorted(datasets_root.glob("AVE_HF*")):
        if not dataset_dir.is_dir():
            continue
        for media_dir_name, suffix in (("videos", ".mp4"), ("audio_16k_mono", ".wav")):
            media_dir = dataset_dir / media_dir_name
            if media_dir.is_dir():
                for path in media_dir.glob(f"*{suffix}"):
                    record(path.stem, "ave_hf_local_assets", path)
        for path in _json_paths(dataset_dir):
            _extract_ids_from_json_path(
                path,
                official_ids,
                lambda value, source=path: record(value, "ave_hf_manifests", source),
                parse_failures,
            )

    stage2_dir = datasets_root / "AS_M4_STAGE2"
    if stage2_dir.is_dir():
        for path in _json_paths(stage2_dir):
            _extract_ids_from_json_path(
                path,
                official_ids,
                lambda value, source=path: record(value, "stage2", source),
                parse_failures,
            )

    if artifacts_root.is_dir():
        for path in _json_paths(artifacts_root):
            lowered = str(path.relative_to(artifacts_root)).lower()
            if not any(marker in lowered for marker in HISTORICAL_ARTIFACT_MARKERS):
                continue
            _extract_ids_from_json_path(
                path,
                official_ids,
                lambda value, source=path: record(value, "historical_harness", source),
                parse_failures,
            )

    if m4_it_path.exists():
        _extract_ids_from_json_path(
            m4_it_path,
            official_ids,
            lambda value, source=m4_it_path: record(value, "m4_it", source),
            parse_failures,
        )

    normalized = {
        youtube_id: {
            "youtube_id": youtube_id,
            "categories": sorted(row["categories"]),
            "source_paths": sorted(row["source_paths"]),
        }
        for youtube_id, row in evidence.items()
    }
    return normalized, parse_failures


def freeze_manifests(
    raw_by_split: dict[str, list[dict]],
    physical: dict[str, dict],
    exposures: dict[str, dict],
) -> tuple[dict[str, list[dict]], list[dict], list[dict]]:
    candidates = {split: [] for split in SPLITS}
    excluded = []
    cross_split = []
    for youtube_id in sorted(physical):
        group = physical[youtube_id]
        official_splits = sorted(group["official_splits"])
        annotations = sorted(group["annotations"], key=lambda row: row["annotation_index"])
        base = {
            "youtube_id": youtube_id,
            "official_split": official_splits[0] if len(official_splits) == 1 else None,
            "official_splits": official_splits,
            "annotation_indices": [row["annotation_index"] for row in annotations],
            "annotations": [
                {
                    "annotation_index": row["annotation_index"],
                    "event_label": row["event_label"],
                    "quality": row["quality"],
                    "event_start_sec": row["event_start_sec"],
                    "event_end_sec": row["event_end_sec"],
                    "has_local_boundary": row["has_local_boundary"],
                }
                for row in annotations
            ],
            "has_local_boundary": any(row["has_local_boundary"] for row in annotations),
        }
        reasons = []
        if len(official_splits) > 1:
            reasons.append("cross_split_physical_duplicate")
            cross_split.append(base)
        if youtube_id in exposures:
            reasons.append("historically_touched")
        if reasons:
            excluded.append(
                {
                    **base,
                    "exclusion_reasons": reasons,
                    "historical_evidence": exposures.get(youtube_id),
                }
            )
            continue
        candidates[official_splits[0]].append(base)
    return candidates, excluded, cross_split


def main() -> int:
    args = parse_args()
    ave_repo = args.ave_repo.resolve()
    data_dir = ave_repo / "data"
    annotation_path = data_dir / "Annotations.txt"
    order_paths = {
        split: data_dir / filename for split, filename in ORDER_FILENAMES.items()
    }
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now().astimezone()

    import h5py

    annotations = load_annotations(annotation_path)
    orders = {}
    for split, path in order_paths.items():
        with h5py.File(path, "r") as handle:
            orders[split] = [int(value) for value in handle["order"][:].tolist()]
    validate_orders(orders, len(annotations))
    raw_by_split, physical = build_split_rows(annotations, orders)
    exposures, parse_failures = collect_historical_exposures(
        set(physical),
        args.datasets_root.resolve(),
        args.artifacts_root.resolve(),
        args.m4_it.resolve(),
    )
    if parse_failures:
        _write_json(output_dir / "historical_inventory_parse_failures.json", parse_failures)
        raise ValueError(
            f"历史污染清单有 {len(parse_failures)} 个 JSON/JSONL 无法解析，禁止冻结 manifest"
        )
    candidates, excluded, cross_split = freeze_manifests(
        raw_by_split,
        physical,
        exposures,
    )
    candidate_id_sets = {
        split: {row["youtube_id"] for row in candidates[split]}
        for split in SPLITS
    }
    candidate_cross_split_overlap = {
        "train_validation": len(candidate_id_sets["train"] & candidate_id_sets["validation"]),
        "train_test": len(candidate_id_sets["train"] & candidate_id_sets["test"]),
        "validation_test": len(candidate_id_sets["validation"] & candidate_id_sets["test"]),
    }
    candidate_historical_overlap = sum(
        len(values & set(exposures)) for values in candidate_id_sets.values()
    )
    if any(candidate_cross_split_overlap.values()):
        raise ValueError("冻结候选仍含跨 split 物理视频重叠")
    if candidate_historical_overlap:
        raise ValueError("冻结候选仍含历史已触及物理视频")

    output_paths = {}
    for split in SPLITS:
        raw_path = output_dir / f"official_raw_{split}.jsonl"
        candidate_name = (
            "test_candidate_manifest.sealed.jsonl"
            if split == "test"
            else f"{split}_candidate_manifest.jsonl"
        )
        candidate_path = output_dir / candidate_name
        _write_jsonl(raw_path, raw_by_split[split])
        _write_jsonl(candidate_path, candidates[split])
        output_paths[f"official_raw_{split}"] = raw_path
        output_paths[f"candidate_{split}"] = candidate_path
    excluded_path = output_dir / "excluded_physical_videos.jsonl"
    cross_split_path = output_dir / "cross_split_physical_duplicates.jsonl"
    inventory_path = output_dir / "historical_exposure_inventory.json"
    _write_jsonl(excluded_path, excluded)
    _write_jsonl(cross_split_path, cross_split)
    _write_json(
        inventory_path,
        {
            "touched_physical_video_count": len(exposures),
            "category_counts": _category_counts(exposures.values()),
            "entries": [exposures[key] for key in sorted(exposures)],
        },
    )
    output_paths.update(
        {
            "excluded": excluded_path,
            "cross_split": cross_split_path,
            "historical_inventory": inventory_path,
        }
    )

    counts = {
        split: {
            "official_annotation_rows": len(raw_by_split[split]),
            "official_unique_physical_videos": len(
                {row["youtube_id"] for row in raw_by_split[split]}
            ),
            "candidate_physical_videos": len(candidates[split]),
            "candidate_local_boundary_videos": sum(
                bool(row["has_local_boundary"]) for row in candidates[split]
            ),
        }
        for split in SPLITS
    }
    source_metadata = {
        "official_repo": str(ave_repo),
        "official_remote": _git_output(ave_repo, ["remote", "get-url", "origin"]),
        "official_commit": _git_output(ave_repo, ["rev-parse", "HEAD"]),
        "annotation_path": str(annotation_path),
        "annotation_sha256": _file_sha256(annotation_path),
        "order_files": {
            split: {"path": str(path), "sha256": _file_sha256(path)}
            for split, path in order_paths.items()
        },
    }
    manifest_hashes = {
        name: {"path": str(path), "sha256": _file_sha256(path)}
        for name, path in output_paths.items()
    }
    lock = {
        "stage": "D1-AVE-official-data-freeze",
        "status": "frozen",
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now().astimezone().isoformat(),
        "source": source_metadata,
        "counts": counts,
        "official_annotation_count": len(annotations),
        "official_unique_physical_video_count": len(physical),
        "cross_split_physical_video_count": len(cross_split),
        "historically_touched_official_video_count": len(exposures),
        "excluded_physical_video_count": len(excluded),
        "audits": {
            "official_order_exact_partition": True,
            "candidate_cross_split_overlap": candidate_cross_split_overlap,
            "candidate_historical_overlap": candidate_historical_overlap,
            "historical_inventory_parse_failure_count": 0,
        },
        "manifests": manifest_hashes,
        "test_seal": {
            "sealed": True,
            "predictions_run": False,
            "effect_results_viewed": False,
            "unseal_condition": "D1 validation 完成并冻结全部策略与阈值",
        },
        "media_download_performed": False,
        "effect_evaluation_performed": False,
        "python_environment": "M4",
        "h5py_version": h5py.__version__,
        "h5py_required_for_official_order": True,
    }
    _write_json(output_dir / "manifest_lock.json", lock)
    (output_dir / "report.md").write_text(_build_report(lock), encoding="utf-8")
    (output_dir / "run.started_at").write_text(started_at.isoformat() + "\n", encoding="utf-8")
    print(json.dumps({"status": "frozen", "counts": counts}, ensure_ascii=False))
    return 0


def _json_paths(root: Path) -> Iterable[Path]:
    yield from sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in {".json", ".jsonl"}
    )


def _extract_ids_from_json_path(
    path: Path,
    official_ids: set[str],
    callback,
    parse_failures: list[dict],
) -> None:
    try:
        if path.suffix.lower() == ".jsonl":
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(),
                start=1,
            ):
                if not line.strip():
                    continue
                _walk_json(json.loads(line), official_ids, callback)
        else:
            _walk_json(json.loads(path.read_text(encoding="utf-8")), official_ids, callback)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        parse_failures.append({"path": str(path.resolve()), "error": str(exc)})


def _walk_json(value, official_ids: set[str], callback) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _walk_json(item, official_ids, callback)
    elif isinstance(value, list):
        for item in value:
            _walk_json(item, official_ids, callback)
    elif isinstance(value, str):
        candidates = {value, Path(value).stem}
        for candidate in candidates & official_ids:
            callback(candidate)


def _category_counts(rows: Iterable[dict]) -> dict[str, int]:
    counts = defaultdict(int, {category: 0 for category in EXPOSURE_CATEGORIES})
    for row in rows:
        for category in row["categories"]:
            counts[category] += 1
    return dict(sorted(counts.items()))


def _write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_output(repo: Path, args: list[str]) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _build_report(lock: dict) -> str:
    count_rows = "\n".join(
        (
            f"- `{split}`：官方 annotation {values['official_annotation_rows']}，"
            f"官方物理视频 {values['official_unique_physical_videos']}，"
            f"冻结候选 {values['candidate_physical_videos']}，"
            f"其中局部边界 {values['candidate_local_boundary_videos']}"
        )
        for split, values in lock["counts"].items()
    )
    return f"""# Dynamic Window Selector D1 AVE 官方数据冻结报告

## 结论

状态：**{lock["status"]}**。本步骤只生成候选清单和污染审计，没有下载媒体或运行效果评测。

## 官方来源

- 仓库：`{lock["source"]["official_remote"]}`
- commit：`{lock["source"]["official_commit"]}`
- annotation SHA256：`{lock["source"]["annotation_sha256"]}`
- 官方 annotation：{lock["official_annotation_count"]}
- 官方唯一物理视频：{lock["official_unique_physical_video_count"]}

## 冻结数量

{count_rows}

- 跨 split 物理重复：{lock["cross_split_physical_video_count"]}
- 历史已触及的官方物理视频：{lock["historically_touched_official_video_count"]}
- 合计排除物理视频：{lock["excluded_physical_video_count"]}

## Test 封存

- sealed：`{str(lock["test_seal"]["sealed"]).lower()}`
- 已运行预测：`{str(lock["test_seal"]["predictions_run"]).lower()}`
- 已查看效果：`{str(lock["test_seal"]["effect_results_viewed"]).lower()}`
- 解封条件：{lock["test_seal"]["unseal_condition"]}

## 下一阶段边界

下一步只能接入并完成 D1 validation，冻结全部策略、预算与阈值。validation 完成前不得运行官方 test。
"""


if __name__ == "__main__":
    raise SystemExit(main())
