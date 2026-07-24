#!/usr/bin/env python
"""冻结 AVE 官方 D1 候选媒体覆盖、缺失清单与内容 SHA。"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Iterable


SPLIT_MANIFEST_NAMES = {
    "train": "train_candidate_manifest.jsonl",
    "validation": "validation_candidate_manifest.jsonl",
    "test": "test_candidate_manifest.sealed.jsonl",
}
RAW_MANIFEST_NAMES = {
    "train": "official_raw_train.jsonl",
    "validation": "official_raw_validation.jsonl",
    "test": "official_raw_test.jsonl",
}
PACKAGE_SPLIT_NAMES = {
    "train": "trainSet.txt",
    "validation": "valSet.txt",
    "test": "testSet.txt",
}
ANNOTATION_HEADER = "Category&VideoID&Quality&StartTime&EndTime"
MEDIA_SUFFIXES = {".mp4", ".avi", ".mkv", ".webm", ".mov"}
SOURCE_URL = (
    "https://drive.usercontent.google.com/download"
    "?id=1FjKwe79e0u96vdjIVwfRQ1V6SoDHe7kK&export=download&confirm=t"
)
EXPECTED_ARCHIVE_SIZE_BYTES = 5_665_119_358


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest-dir",
        type=Path,
        default=Path("intersuit/harness/artifacts/dynamic_selector_d1_ave_official"),
    )
    parser.add_argument(
        "--media-root",
        type=Path,
        default=Path("intersuit/datasets/AVE_OFFICIAL_D1_SOURCE/extracted"),
    )
    parser.add_argument(
        "--source-archive",
        type=Path,
        default=Path("intersuit/datasets/AVE_OFFICIAL_D1_SOURCE/AVE_Dataset.zip"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("intersuit/harness/artifacts/dynamic_selector_d1_ave_official_media"),
    )
    return parser.parse_args()


def load_jsonl(path: Path, *, require_unique_ids: bool = True) -> list[dict]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        youtube_id = row.get("youtube_id")
        if not isinstance(youtube_id, str) or not youtube_id:
            raise ValueError(f"{path} 第 {line_number} 行缺少 youtube_id")
        rows.append(row)
    if require_unique_ids and len(rows) != len({row["youtube_id"] for row in rows}):
        raise ValueError(f"{path} 含重复物理视频 youtube_id")
    return rows


def index_media(media_root: Path) -> tuple[dict[str, Path], dict[str, list[str]]]:
    paths_by_id: dict[str, list[Path]] = {}
    for path in sorted(media_root.rglob("*")):
        if path.is_file() and path.suffix.lower() in MEDIA_SUFFIXES:
            paths_by_id.setdefault(path.stem, []).append(path.resolve())
    duplicates = {
        youtube_id: [str(path) for path in paths]
        for youtube_id, paths in paths_by_id.items()
        if len(paths) > 1
    }
    unique = {
        youtube_id: paths[0]
        for youtube_id, paths in paths_by_id.items()
        if len(paths) == 1
    }
    return unique, duplicates


def audit_split(
    split: str,
    rows: list[dict],
    media_by_id: dict[str, Path],
    duplicate_ids: set[str],
) -> tuple[list[dict], list[dict]]:
    inventory = []
    missing = []
    for row in rows:
        youtube_id = row["youtube_id"]
        media_path = media_by_id.get(youtube_id)
        reason = None
        if youtube_id in duplicate_ids:
            reason = "duplicate_media_paths"
        elif media_path is None:
            reason = "missing_media"
        elif media_path.stat().st_size <= 0:
            reason = "zero_byte_media"
        record = {
            "youtube_id": youtube_id,
            "official_split": split,
            "status": "present" if reason is None else "missing",
            "failure_reason": reason,
            "media_path": str(media_path) if media_path is not None else None,
            "media_size_bytes": media_path.stat().st_size if media_path is not None else None,
            "media_sha256": file_sha256(media_path) if reason is None else None,
            "test_sealed": split == "test",
        }
        inventory.append(record)
        if reason is not None:
            missing.append(record)
    return inventory, missing


def annotation_line(row: dict) -> str:
    return "&".join(
        [
            str(row["event_label"]),
            str(row["youtube_id"]),
            str(row["quality"]),
            str(row["event_start_sec"]),
            str(row["event_end_sec"]),
        ]
    )


def verify_package_metadata(
    media_root: Path,
    raw_by_split: dict[str, list[dict]],
    indexed_media_ids: set[str],
) -> dict:
    metadata_roots = sorted({path.parent for path in media_root.rglob("trainSet.txt")})
    if len(metadata_roots) != 1:
        raise ValueError(f"官方包元数据根目录数量应为 1，实际为 {len(metadata_roots)}")
    package_root = metadata_roots[0]
    expected_by_split = {
        split: [annotation_line(row) for row in rows]
        for split, rows in raw_by_split.items()
    }
    for split, filename in PACKAGE_SPLIT_NAMES.items():
        actual = (package_root / filename).read_text(encoding="utf-8").splitlines()
        if actual != expected_by_split[split]:
            raise ValueError(f"官方包 {filename} 与冻结 {split} annotation 不一致")
    annotation_lines = (package_root / "Annotations.txt").read_text(
        encoding="utf-8"
    ).splitlines()
    if (
        not annotation_lines
        or annotation_lines[0] != ANNOTATION_HEADER
        or Counter(annotation_lines[1:])
        != Counter(
            [
                *expected_by_split["train"],
                *expected_by_split["validation"],
                *expected_by_split["test"],
            ]
        )
    ):
        raise ValueError("官方包 Annotations.txt 与三个冻结 split 标注多重集不一致")
    official_ids = {
        row["youtube_id"]
        for rows in raw_by_split.values()
        for row in rows
    }
    if indexed_media_ids != official_ids:
        raise ValueError(
            "官方包媒体 ID 与 annotation 物理视频集合不一致："
            f"missing={len(official_ids - indexed_media_ids)}, "
            f"extra={len(indexed_media_ids - official_ids)}"
        )
    return {
        "package_root": str(package_root.resolve()),
        "split_exact_order_match": {split: True for split in SPLIT_MANIFEST_NAMES},
        "annotation_header_removed_multiset_match": True,
        "media_id_exact_match": True,
        "official_unique_physical_video_count": len(official_ids),
    }


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def build_report(lock: dict) -> str:
    split_lines = "\n".join(
        (
            f"- `{split}`：候选 {counts['candidate_count']}，"
            f"存在 {counts['present_count']}，缺失 {counts['missing_count']}"
        )
        for split, counts in lock["counts"].items()
    )
    return f"""# Dynamic Window Selector D1 AVE 官方媒体冻结报告

## 结论

状态：**{lock["status"]}**。本步骤只核对候选媒体文件、大小与 SHA，没有运行窗口预测或效果评测。

## 媒体覆盖

{split_lines}

- 重复媒体路径 ID：{lock["duplicate_media_id_count"]}
- 官方 ZIP 字节数：{lock["source_archive"]["size_bytes"]}
- 官方 ZIP SHA256：`{lock["source_archive"]["sha256"]}`

## Test 封存

- sealed：`true`
- 已运行预测：`false`
- 已查看效果：`false`

## 下一阶段边界

下一步只能使用 validation 媒体接入 D1 策略矩阵。validation 冻结全部策略与阈值前，不得运行 test 预测。
"""


def run(args: argparse.Namespace) -> dict:
    started_at = datetime.now().astimezone()
    manifest_dir = args.manifest_dir.resolve()
    media_root = args.media_root.resolve()
    source_archive = args.source_archive.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if not source_archive.is_file():
        raise FileNotFoundError(f"官方源 ZIP 不存在：{source_archive}")
    if source_archive.stat().st_size != EXPECTED_ARCHIVE_SIZE_BYTES:
        raise ValueError(
            "官方源 ZIP 字节数不符："
            f"expected={EXPECTED_ARCHIVE_SIZE_BYTES}, actual={source_archive.stat().st_size}"
        )
    if not media_root.is_dir():
        raise FileNotFoundError(f"官方媒体目录不存在：{media_root}")

    media_by_id, duplicates = index_media(media_root)
    raw_by_split = {
        split: load_jsonl(manifest_dir / filename, require_unique_ids=False)
        for split, filename in RAW_MANIFEST_NAMES.items()
    }
    package_verification = verify_package_metadata(
        media_root,
        raw_by_split,
        set(media_by_id),
    )
    counts = {}
    outputs = {}
    for split, filename in SPLIT_MANIFEST_NAMES.items():
        manifest_path = manifest_dir / filename
        rows = load_jsonl(manifest_path)
        inventory, missing = audit_split(split, rows, media_by_id, set(duplicates))
        inventory_name = (
            "test_media_inventory.sealed.jsonl"
            if split == "test"
            else f"{split}_media_inventory.jsonl"
        )
        missing_name = (
            "test_missing_media.sealed.jsonl"
            if split == "test"
            else f"{split}_missing_media.jsonl"
        )
        inventory_path = output_dir / inventory_name
        missing_path = output_dir / missing_name
        write_jsonl(inventory_path, inventory)
        write_jsonl(missing_path, missing)
        counts[split] = {
            "candidate_count": len(rows),
            "present_count": len(inventory) - len(missing),
            "missing_count": len(missing),
        }
        outputs[f"{split}_inventory"] = inventory_path
        outputs[f"{split}_missing"] = missing_path

    duplicate_path = output_dir / "duplicate_media_paths.json"
    write_json(duplicate_path, duplicates)
    outputs["duplicate_media_paths"] = duplicate_path
    lock = {
        "stage": "D1-AVE-official-media-freeze",
        "status": "frozen",
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now().astimezone().isoformat(),
        "python_environment": "M4",
        "source_archive": {
            "url": SOURCE_URL,
            "path": str(source_archive),
            "size_bytes": source_archive.stat().st_size,
            "sha256": file_sha256(source_archive),
        },
        "media_root": str(media_root),
        "indexed_unique_media_count": len(media_by_id),
        "duplicate_media_id_count": len(duplicates),
        "package_verification": package_verification,
        "counts": counts,
        "input_manifests": {
            split: {
                "path": str((manifest_dir / filename).resolve()),
                "sha256": file_sha256(manifest_dir / filename),
            }
            for split, filename in SPLIT_MANIFEST_NAMES.items()
        },
        "outputs": {
            name: {"path": str(path), "sha256": file_sha256(path)}
            for name, path in outputs.items()
        },
        "test_seal": {
            "sealed": True,
            "predictions_run": False,
            "effect_results_viewed": False,
            "unseal_condition": "D1 validation 完成并冻结全部策略与阈值",
        },
        "effect_evaluation_performed": False,
    }
    lock_path = output_dir / "media_lock.json"
    write_json(lock_path, lock)
    (output_dir / "report.md").write_text(build_report(lock), encoding="utf-8")
    print(json.dumps({"status": lock["status"], "counts": counts}, ensure_ascii=False))
    return lock


def main() -> int:
    run(parse_args())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
