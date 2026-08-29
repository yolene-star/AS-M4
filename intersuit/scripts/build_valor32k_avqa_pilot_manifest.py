#!/usr/bin/env python3
"""从 Valor32k-AVQA official train 构建可审计的三模态媒体 pilot。"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


DEFAULT_QUOTAS = {
    "description": 40,
    "count": 35,
    "temporal": 35,
    "action": 20,
    "location": 20,
}
MODALITIES = ("visual", "audio", "audio-visual")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_key(seed: str, *parts: str) -> str:
    value = "\0".join((seed, *parts)).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def question_signature(row: dict[str, Any]) -> str:
    payload = {
        key: row.get(key)
        for key in (
            "video_id",
            "question",
            "options",
            "correct_answer_idx",
            "rephrased_answers",
            "quality_rating",
            "modality",
            "category",
            "source_tags",
        )
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_exclusion_ids(inventory_path: Path, dev_manifest_path: Path) -> tuple[set[str], dict[str, Any]]:
    inventory = load_json(inventory_path)
    exclusion_ids = {str(value).casefold() for value in inventory.get("ids", [])}
    dev_rows = load_json(dev_manifest_path)
    dev_ids = set()
    for row in dev_rows:
        for key in ("video_id", "youtube_id", "derived_media_id"):
            value = row.get(key)
            if isinstance(value, str) and value:
                dev_ids.add(value.casefold())
    exclusion_ids.update(dev_ids)
    return exclusion_ids, {
        "inventory_path": str(inventory_path),
        "inventory_sha256": sha256_file(inventory_path),
        "inventory_declared_id_count": inventory.get("id_count"),
        "dev_manifest_path": str(dev_manifest_path),
        "dev_manifest_sha256": sha256_file(dev_manifest_path),
        "dev_manifest_rows": len(dev_rows),
        "combined_normalized_id_count": len(exclusion_ids),
    }


def choose_question(rows: list[dict[str, Any]], seed: str, video_id: str, modality: str) -> dict[str, Any]:
    return min(
        rows,
        key=lambda row: stable_key(seed, video_id, modality, str(row.get("id")), row["question"]),
    )


def build_manifest(
    train_rows: list[dict[str, Any]],
    validation_rows: list[dict[str, Any]],
    test_rows: list[dict[str, Any]],
    exclusion_ids: set[str],
    seed: str,
    quotas: dict[str, int],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    seen_signatures: set[str] = set()
    duplicate_rows_dropped = 0
    eligible_rows: list[dict[str, Any]] = []
    for row in train_rows:
        if row.get("model") != "gpt4o" or row.get("quality_rating") != "obvious":
            continue
        signature = question_signature(row)
        if signature in seen_signatures:
            duplicate_rows_dropped += 1
            continue
        seen_signatures.add(signature)
        eligible_rows.append(row)

    by_video: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in eligible_rows:
        by_video[row["video_id"]].append(row)

    tri_modal = {
        video_id: rows
        for video_id, rows in by_video.items()
        if {row["modality"] for row in rows} == set(MODALITIES)
    }
    excluded_tri_modal = {
        video_id for video_id in tri_modal if video_id.casefold() in exclusion_ids
    }
    available = {
        video_id: rows
        for video_id, rows in tri_modal.items()
        if video_id.casefold() not in exclusion_ids
    }

    selected_ids: set[str] = set()
    selected: list[dict[str, Any]] = []
    available_by_category: dict[str, int] = {}
    for category, quota in quotas.items():
        category_candidates = []
        for video_id, rows in available.items():
            audio_rows = [
                row
                for row in rows
                if row["modality"] == "audio" and row["category"] == category
            ]
            if audio_rows:
                category_candidates.append((video_id, rows, audio_rows))
        available_by_category[category] = len(category_candidates)
        category_candidates.sort(key=lambda item: stable_key(seed, category, item[0]))
        category_selected = 0
        for video_id, rows, audio_rows in category_candidates:
            if video_id in selected_ids:
                continue
            selected_ids.add(video_id)
            by_modality = {
                modality: [row for row in rows if row["modality"] == modality]
                for modality in MODALITIES
            }
            chosen_audio = choose_question(audio_rows, seed, video_id, "audio")
            chosen_questions = {
                "visual": choose_question(by_modality["visual"], seed, video_id, "visual"),
                "audio": chosen_audio,
                "audio-visual": choose_question(
                    by_modality["audio-visual"], seed, video_id, "audio-visual"
                ),
            }
            selected.append(
                {
                    "pilot_index": len(selected),
                    "video_id": video_id,
                    "official_split": "train",
                    "selection_audio_category": category,
                    "caption": chosen_audio["caption"],
                    "questions": {
                        modality: {
                            key: chosen_questions[modality][key]
                            for key in (
                                "id",
                                "oid",
                                "question",
                                "options",
                                "correct_answer_idx",
                                "rephrased_answers",
                                "modality",
                                "category",
                                "source_tags",
                                "model",
                                "quality_rating",
                            )
                        }
                        for modality in MODALITIES
                    },
                }
            )
            category_selected += 1
            if category_selected == quota:
                break
        if category_selected != quota:
            raise ValueError(
                f"类别 {category} 只能选择 {category_selected}/{quota} 个不重复视频"
            )

    validation_ids = {row["video_id"] for row in validation_rows}
    test_ids = {row["video_id"] for row in test_rows}
    selected_category_counts = Counter(item["selection_audio_category"] for item in selected)
    selected_question_categories = {
        modality: dict(Counter(item["questions"][modality]["category"] for item in selected))
        for modality in MODALITIES
    }
    selected_source_tags = {
        modality: dict(
            Counter(
                tag
                for item in selected
                for tag in item["questions"][modality]["source_tags"]
            )
        )
        for modality in MODALITIES
    }
    report = {
        "status": "pass",
        "seed": seed,
        "quotas": quotas,
        "input_train_rows": len(train_rows),
        "eligible_rows_after_filter_and_dedup": len(eligible_rows),
        "duplicate_rows_dropped": duplicate_rows_dropped,
        "eligible_unique_videos": len(by_video),
        "tri_modal_unique_videos": len(tri_modal),
        "tri_modal_excluded_by_existing_inventory": len(excluded_tri_modal),
        "tri_modal_available_after_exclusion": len(available),
        "available_by_audio_category": available_by_category,
        "selected_video_count": len(selected),
        "selected_question_count": len(selected) * len(MODALITIES),
        "selected_audio_category_counts": dict(selected_category_counts),
        "selected_question_category_counts": selected_question_categories,
        "selected_source_tag_counts": selected_source_tags,
        "selected_vs_existing_exclusion_overlap": sum(
            item["video_id"].casefold() in exclusion_ids for item in selected
        ),
        "selected_vs_official_validation_overlap": sum(
            item["video_id"] in validation_ids for item in selected
        ),
        "selected_vs_official_test_overlap": sum(
            item["video_id"] in test_ids for item in selected
        ),
    }
    if len(selected) != sum(quotas.values()):
        report["status"] = "fail"
    if any(
        report[key]
        for key in (
            "selected_vs_existing_exclusion_overlap",
            "selected_vs_official_validation_overlap",
            "selected_vs_official_test_overlap",
        )
    ):
        report["status"] = "fail"
    return selected, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", type=Path, required=True)
    parser.add_argument("--validation", type=Path, required=True)
    parser.add_argument("--test", type=Path, required=True)
    parser.add_argument("--exclusion-inventory", type=Path, required=True)
    parser.add_argument("--dev-manifest", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    parser.add_argument("--output-report", type=Path, required=True)
    parser.add_argument("--seed", default="as-m4-cgf-valor32k-pilot-v1")
    args = parser.parse_args()

    exclusion_ids, exclusion_lock = load_exclusion_ids(
        args.exclusion_inventory, args.dev_manifest
    )
    manifest, report = build_manifest(
        load_json(args.train),
        load_json(args.validation),
        load_json(args.test),
        exclusion_ids,
        args.seed,
        DEFAULT_QUOTAS,
    )
    args.output_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.output_report.parent.mkdir(parents=True, exist_ok=True)
    args.output_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report["inputs"] = {
        "train": {"path": str(args.train), "sha256": sha256_file(args.train)},
        "validation": {
            "path": str(args.validation),
            "sha256": sha256_file(args.validation),
        },
        "test": {"path": str(args.test), "sha256": sha256_file(args.test)},
        "exclusions": exclusion_lock,
    }
    report["manifest_path"] = str(args.output_manifest)
    report["manifest_sha256"] = sha256_file(args.output_manifest)
    args.output_report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"构建状态：{report['status'].upper()}")
    print(f"pilot 视频：{report['selected_video_count']}")
    print(f"pilot 问题：{report['selected_question_count']}")
    print(f"既有排除交集：{report['selected_vs_existing_exclusion_overlap']}")
    print(f"validation/test 交集：{report['selected_vs_official_validation_overlap']}/{report['selected_vs_official_test_overlap']}")
    print(f"manifest SHA-256：{report['manifest_sha256']}")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
