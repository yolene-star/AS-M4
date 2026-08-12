"""Shared fail-closed evaluation helpers for Dynamic Selector V3 P3."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any


RANDOM_PREFIX = "random_top8_seed_"
MISMATCH_PREFIX = "oracle_mismatch_seed_"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def finite_tree(value: Any) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(finite_tree(item) for item in value.values())
    if isinstance(value, list):
        return all(finite_tree(item) for item in value)
    return True


def has_degenerate_repetition(text: str) -> bool:
    normalized = " ".join(text.lower().split())
    sentences = [part.strip(" .,!?:;\t\n") for part in re.split(r"[.!?\n]+", normalized)]
    sentences = [part for part in sentences if len(part.split()) >= 4]
    if any(left == right for left, right in zip(sentences, sentences[1:])):
        return True
    tokens = normalized.split()
    spans = [tuple(tokens[index : index + 8]) for index in range(max(0, len(tokens) - 7))]
    return len(spans) != len(set(spans))


def official_predictions(
    rows: list[dict[str, Any]],
    annotation_video_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Map one-event rows to the ordering contract assumed by LongVALE.

    The frozen official metric assigns integer slots to predictions and ground
    truths independently, so callers must submit predictions in annotation
    dictionary order.  It does not join the two sides by ``video_id``.
    """

    converted = [
        {
            "video_id": str(row["physical_media_id"]),
            "task": "seg_captioning",
            "query_id": 0,
            "answer": str(row["prediction"]),
            "info": {"sentence_id": 0},
        }
        for row in rows
    ]
    if annotation_video_ids is None:
        return converted
    by_video = {row["video_id"]: row for row in converted}
    if len(by_video) != len(converted):
        raise ValueError("LongVALE official predictions physical video 重复")
    if set(by_video) != set(annotation_video_ids):
        raise ValueError("LongVALE official predictions 与 annotation 视频集合漂移")
    return [by_video[video_id] for video_id in annotation_video_ids]


def anomaly_counts(rows: list[dict[str, Any]], max_new_tokens: int) -> dict[str, int]:
    def token_debug(row: dict[str, Any]) -> dict[str, Any]:
        return ((row.get("generation_debug") or {}).get("tokens") or {})

    return {
        "empty_output": sum(not str(row.get("prediction", "")).strip() for row in rows),
        "first_token_eos": sum(bool(token_debug(row).get("first_token_is_eos")) for row in rows),
        "max_length_without_eos": sum(
            int(token_debug(row).get("new_token_count") or 0) == max_new_tokens
            and int(token_debug(row).get("eos_token_id") or -1)
            not in list(token_debug(row).get("last_20_new_token_ids") or [])
            for row in rows
        ),
        "repeated_text": sum(has_degenerate_repetition(str(row.get("prediction", ""))) for row in rows),
        "nonfinite_output": sum(not finite_tree(row) for row in rows),
    }


def validate_matrix(
    conditions: list[str],
    rows_by_condition: dict[str, list[dict[str, Any]]],
    expected_count: int,
) -> None:
    reference_ids = None
    for condition in conditions:
        rows = rows_by_condition.get(condition)
        if rows is None or len(rows) != expected_count:
            raise ValueError(f"{condition} prediction 数量异常")
        ids = [str(row["sample_id"]) for row in rows]
        if len(set(ids)) != expected_count:
            raise ValueError(f"{condition} sample_id 不唯一")
        if any(row.get("experiment_id") != condition for row in rows):
            raise ValueError(f"{condition} experiment_id 漂移")
        if reference_ids is None:
            reference_ids = ids
        elif ids != reference_ids:
            raise ValueError(f"{condition} 样本顺序/集合漂移")


def operational_checks(rows_by_condition: dict[str, list[dict[str, Any]]]) -> dict[str, bool]:
    all_rows = rows_by_condition["fixed_all"]
    oracle_rows = rows_by_condition["oracle_top8"]
    mismatch_names = sorted(name for name in rows_by_condition if name.startswith(MISMATCH_PREFIX))
    all_true = all(
        row["generation_debug"]["tokens"]["audio_input"]["selection_trace"]["selected_window_count"]
        == row["generation_debug"]["tokens"]["audio_input"]["full_window_count"]
        for row in all_rows
    )
    oracle_eight = all(
        row["generation_debug"]["tokens"]["audio_input"]["selection_trace"]["selected_window_count"] == 8
        for row in oracle_rows
    )
    mismatch_nonself = all(
        row.get("mismatch_source_physical_media_id")
        and row.get("mismatch_source_physical_media_id") != row.get("physical_media_id")
        for name in mismatch_names
        for row in rows_by_condition[name]
    )
    return {
        "true_all_selected_equals_full": all_true,
        "oracle_selected_count_is_eight": oracle_eight,
        "mismatch_physical_nonself": mismatch_nonself,
        "no_empty_output": all(
            str(row.get("prediction", "")).strip()
            for rows in rows_by_condition.values()
            for row in rows
        ),
        "all_rows_finite": all(
            finite_tree(row) for rows in rows_by_condition.values() for row in rows
        ),
    }


def summarize_metrics(metrics: dict[str, dict[str, float]]) -> dict[str, Any]:
    random_names = sorted(name for name in metrics if name.startswith(RANDOM_PREFIX))
    mismatch_names = sorted(name for name in metrics if name.startswith(MISMATCH_PREFIX))
    if len(random_names) != 5 or len(mismatch_names) != 5:
        raise ValueError("Random/mismatch 条件必须各有五个 seed")
    oracle = metrics["oracle_top8"]["METEOR"]
    random_mean = sum(metrics[name]["METEOR"] for name in random_names) / 5
    mismatch_mean = sum(metrics[name]["METEOR"] for name in mismatch_names) / 5
    return {
        "oracle": oracle,
        "fixed_all": metrics["fixed_all"]["METEOR"],
        "current_micro_top8": metrics["current_micro_top8"]["METEOR"],
        "behavior_off": metrics["behavior_off"]["METEOR"],
        "random_mean": random_mean,
        "mismatch_mean": mismatch_mean,
        "oracle_minus_fixed_all": oracle - metrics["fixed_all"]["METEOR"],
        "oracle_minus_current_micro_top8": oracle - metrics["current_micro_top8"]["METEOR"],
        "oracle_minus_behavior_off": oracle - metrics["behavior_off"]["METEOR"],
        "oracle_minus_random_mean": oracle - random_mean,
        "oracle_minus_mismatch_mean": oracle - mismatch_mean,
    }
