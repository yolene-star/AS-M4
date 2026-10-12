#!/usr/bin/env python3
"""审计 Valor32k-AVQA v2.0 官方 flattened/grouped JSON 标注。"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


SPLITS = {"train": "train", "validation": "val", "test": "test"}
VALID_MODALITIES = {"visual", "audio", "audio-visual"}
VALID_CATEGORIES = {
    "relative-position",
    "description",
    "action",
    "temporal",
    "count",
    "location",
}
VALID_MODELS = {"gpt4o", "gpt3.5"}
VALID_QUALITY = {"obvious", "guess", None, ""}
FLAT_REQUIRED = {
    "caption",
    "video_id",
    "question",
    "options",
    "correct_answer_idx",
    "rephrased_answers",
    "quality_rating",
    "modality",
    "category",
    "source_tags",
    "id",
    "oid",
    "model",
}
GROUP_REQUIRED = {"video_id", "caption", "prompt", "questions", "oid", "model"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        raise ValueError(f"顶层不是 list：{path}")
    return data


def normalized_signature(row: dict[str, Any]) -> str:
    payload = {
        "video_id": row.get("video_id"),
        "question": row.get("question"),
        "options": row.get("options"),
        "correct_answer_idx": row.get("correct_answer_idx"),
        "rephrased_answers": row.get("rephrased_answers"),
        "quality_rating": row.get("quality_rating"),
        "modality": row.get("modality"),
        "category": row.get("category"),
        "source_tags": row.get("source_tags"),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def inspect_flat(rows: list[dict[str, Any]]) -> dict[str, Any]:
    errors: Counter[str] = Counter()
    modalities: Counter[str] = Counter()
    categories: Counter[str] = Counter()
    models: Counter[str] = Counter()
    quality: Counter[str] = Counter()
    source_tags: Counter[str] = Counter()
    videos: Counter[str] = Counter()
    ids: Counter[str] = Counter()
    oids: Counter[str] = Counter()
    signatures: Counter[str] = Counter()
    questions: Counter[str] = Counter()

    for row in rows:
        if not isinstance(row, dict):
            errors["row_not_object"] += 1
            continue
        missing = FLAT_REQUIRED - set(row)
        errors["missing_required_fields"] += bool(missing)
        video_id = row.get("video_id")
        if not isinstance(video_id, str) or not video_id.strip():
            errors["invalid_video_id"] += 1
        else:
            videos[video_id] += 1
        question = row.get("question")
        if not isinstance(question, str) or not question.strip():
            errors["invalid_question"] += 1
        else:
            questions[question.strip()] += 1
        options = row.get("options")
        if not isinstance(options, list) or len(options) != 4:
            errors["options_not_four"] += 1
        elif any(not isinstance(option, str) or not option.strip() for option in options):
            errors["empty_or_nonstring_option"] += 1
        elif len({option.strip().casefold() for option in options}) != 4:
            errors["duplicate_options_casefold"] += 1
        answer_idx = row.get("correct_answer_idx")
        if not isinstance(answer_idx, int) or isinstance(answer_idx, bool) or not 0 <= answer_idx < 4:
            errors["invalid_correct_answer_idx"] += 1
        rephrased = row.get("rephrased_answers")
        if not isinstance(rephrased, list) or len(rephrased) != 3:
            errors["rephrased_answers_not_three"] += 1
        elif any(not isinstance(answer, str) or not answer.strip() for answer in rephrased):
            errors["empty_or_nonstring_rephrased_answer"] += 1
        modality = row.get("modality")
        modalities[str(modality)] += 1
        if modality not in VALID_MODALITIES:
            errors["invalid_modality"] += 1
        category = row.get("category")
        categories[str(category)] += 1
        if category not in VALID_CATEGORIES:
            errors["invalid_category"] += 1
        model = row.get("model")
        models[str(model)] += 1
        if model not in VALID_MODELS:
            errors["invalid_model"] += 1
        quality_rating = row.get("quality_rating")
        quality["<null>" if quality_rating is None else str(quality_rating)] += 1
        if quality_rating not in VALID_QUALITY:
            errors["invalid_quality_rating"] += 1
        tags = row.get("source_tags")
        if not isinstance(tags, list) or not tags:
            errors["invalid_source_tags"] += 1
        else:
            for tag in tags:
                source_tags[str(tag)] += 1
        ids[str(row.get("id"))] += 1
        oids[str(row.get("oid"))] += 1
        signatures[normalized_signature(row)] += 1

    return {
        "rows": len(rows),
        "unique_videos": len(videos),
        "modalities": dict(modalities),
        "categories": dict(categories),
        "models": dict(models),
        "quality_rating": dict(quality),
        "source_tags": dict(source_tags),
        "errors": dict(errors),
        "duplicate_ids": sum(count - 1 for count in ids.values() if count > 1),
        "duplicate_exact_signatures": sum(count - 1 for count in signatures.values() if count > 1),
        "duplicate_question_texts": sum(count - 1 for count in questions.values() if count > 1),
        "video_ids": set(videos),
        "signature_counter": signatures,
        "questions_per_video": videos,
        "oid_count": len(oids),
    }


def inspect_grouped(rows: list[dict[str, Any]]) -> dict[str, Any]:
    errors: Counter[str] = Counter()
    video_ids: Counter[str] = Counter()
    signatures: Counter[str] = Counter()
    question_total = 0

    for group in rows:
        if not isinstance(group, dict):
            errors["group_not_object"] += 1
            continue
        errors["missing_required_fields"] += bool(GROUP_REQUIRED - set(group))
        video_id = group.get("video_id")
        if not isinstance(video_id, str) or not video_id.strip():
            errors["invalid_video_id"] += 1
            continue
        video_ids[video_id] += 1
        questions = group.get("questions")
        if not isinstance(questions, list):
            errors["questions_not_list"] += 1
            continue
        question_total += len(questions)
        for question in questions:
            if not isinstance(question, dict):
                errors["question_not_object"] += 1
                continue
            merged = dict(question)
            merged["video_id"] = video_id
            signatures[normalized_signature(merged)] += 1

    return {
        "groups": len(rows),
        "unique_videos": len(video_ids),
        "duplicate_video_groups": sum(count - 1 for count in video_ids.values() if count > 1),
        "questions": question_total,
        "errors": dict(errors),
        "video_ids": set(video_ids),
        "signature_counter": signatures,
    }


def clean_for_json(value: Any) -> Any:
    if isinstance(value, set):
        return sorted(value)
    if isinstance(value, Counter):
        return dict(value)
    if isinstance(value, dict):
        return {key: clean_for_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [clean_for_json(item) for item in value]
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    report: dict[str, Any] = {"status": "pass", "splits": {}, "cross_split": {}}
    flat_results: dict[str, dict[str, Any]] = {}
    group_results: dict[str, dict[str, Any]] = {}

    for split, filename_split in SPLITS.items():
        flat_path = args.data_dir / f"combined_dataset_{filename_split}_flattened.json"
        group_path = args.data_dir / f"combined_dataset_{filename_split}.json"
        flat_rows = load_json(flat_path)
        group_rows = load_json(group_path)
        flat = inspect_flat(flat_rows)
        eligible_rows = [
            row
            for row in flat_rows
            if row.get("model") == "gpt4o" and row.get("quality_rating") == "obvious"
        ]
        eligible = inspect_flat(eligible_rows)
        grouped = inspect_grouped(group_rows)
        flat_results[split] = flat
        group_results[split] = grouped

        only_flat = flat["signature_counter"] - grouped["signature_counter"]
        only_grouped = grouped["signature_counter"] - flat["signature_counter"]
        split_report = {
            "files": {
                "flattened": {"path": str(flat_path), "sha256": sha256(flat_path)},
                "grouped": {"path": str(group_path), "sha256": sha256(group_path)},
            },
            "flattened": {k: v for k, v in flat.items() if k not in {"video_ids", "signature_counter", "questions_per_video"}},
            "eligible_gpt4o_obvious": {
                k: v
                for k, v in eligible.items()
                if k not in {"video_ids", "signature_counter", "questions_per_video"}
            },
            "grouped": {k: v for k, v in grouped.items() if k not in {"video_ids", "signature_counter"}},
            "consistency": {
                "same_video_id_set": flat["video_ids"] == grouped["video_ids"],
                "only_flattened_signatures": sum(only_flat.values()),
                "only_grouped_signatures": sum(only_grouped.values()),
            },
        }
        report["splits"][split] = split_report

    for left, right in (("train", "validation"), ("train", "test"), ("validation", "test")):
        overlap = flat_results[left]["video_ids"] & flat_results[right]["video_ids"]
        report["cross_split"][f"{left}_vs_{right}"] = {
            "overlap_count": len(overlap),
            "examples": sorted(overlap)[:20],
        }

    fatal_errors = []
    warnings = []
    for split, split_report in report["splits"].items():
        flat = split_report["flattened"]
        eligible = split_report["eligible_gpt4o_obvious"]
        grouped = split_report["grouped"]
        consistency = split_report["consistency"]
        if any(eligible["errors"].values()):
            fatal_errors.append(f"{split}: gpt4o+obvious 候选子集字段合同错误")
        if any(flat["errors"].values()):
            warnings.append(f"{split}: 全量 flattened 含字段异常，需排除不合格记录")
        if eligible["duplicate_exact_signatures"]:
            warnings.append(
                f"{split}: gpt4o+obvious 含 {eligible['duplicate_exact_signatures']} 条精确重复，生成 manifest 时去重"
            )
        if any(grouped["errors"].values()):
            warnings.append(f"{split}: grouped 缺少部分可选发布字段，不作为首阶段 canonical 标注")
        if grouped["duplicate_video_groups"]:
            fatal_errors.append(f"{split}: grouped 重复视频")
        if not consistency["same_video_id_set"]:
            fatal_errors.append(f"{split}: flattened/grouped 视频集合不一致")
        if consistency["only_flattened_signatures"] or consistency["only_grouped_signatures"]:
            warnings.append(f"{split}: flattened/grouped 问题内容不完全一致，首阶段固定使用 flattened")
    if any(item["overlap_count"] for item in report["cross_split"].values()):
        fatal_errors.append("物理 video_id 跨 split 重叠")

    report["summary"] = {
        "total_questions": sum(result["rows"] for result in flat_results.values()),
        "total_unique_videos": sum(len(result["video_ids"]) for result in flat_results.values()),
        "eligible_gpt4o_obvious_questions_before_dedup": sum(
            split["eligible_gpt4o_obvious"]["rows"] for split in report["splits"].values()
        ),
        "eligible_exact_duplicates_to_drop": sum(
            split["eligible_gpt4o_obvious"]["duplicate_exact_signatures"]
            for split in report["splits"].values()
        ),
        "fatal_errors": fatal_errors,
        "warnings": warnings,
    }
    report["status"] = "pass_with_exclusions" if not fatal_errors else "fail"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(clean_for_json(report), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"审计状态：{report['status'].upper()}")
    print(f"问题总数：{report['summary']['total_questions']}")
    print(f"视频总数：{report['summary']['total_unique_videos']}")
    print(
        "gpt4o+obvious 候选数（去重前）："
        f"{report['summary']['eligible_gpt4o_obvious_questions_before_dedup']}"
    )
    print(f"候选精确重复待剔除：{report['summary']['eligible_exact_duplicates_to_drop']}")
    print(f"致命问题：{len(fatal_errors)}")
    for error in fatal_errors:
        print(f"- {error}")
    print(f"报告：{args.output}")
    print(f"警告：{len(warnings)}")
    for warning in warnings:
        print(f"- {warning}")
    return 0 if not fatal_errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
