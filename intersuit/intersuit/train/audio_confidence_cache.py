"""P3 全量冻结特征的去重缓存计划与 CPU 审计。"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence


CACHE_PLAN_SCHEMA = "as_m4_audio_confidence_feature_cache_plan_v1"


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _audio_payload(row: Mapping[str, Any], source_sha: str) -> dict[str, Any]:
    return {
        "encoder_source_sha256": source_sha,
        "source_media_path": row["audio_source_relative_media_path"],
        "source_media_sha256": row["audio_source_sha256"],
        "recipe": row["corruption_recipe"],
        "seed": row.get("corruption_seed"),
        "source_sample_rate_hz": row["target_audio_sample_rate_hz"],
        "channels": row["target_audio_channels"],
        "duration_sec": row["target_audio_duration_sec"],
        "window_sec": row["window_sec"],
        "hop_sec": row["hop_sec"],
        "window_count": row["target_stream_window_count"],
    }


def _video_payload(row: Mapping[str, Any], source_sha: str) -> dict[str, Any]:
    return {
        "encoder_source_sha256": source_sha,
        "source_media_path": row["video_relative_media_path"],
        "source_media_sha256": row["video_sha256"],
        "window_sec": row["window_sec"],
        "hop_sec": row["hop_sec"],
        "window_count": row["target_stream_window_count"],
    }


def _question_payload(row: Mapping[str, Any], source_sha: str) -> dict[str, Any]:
    return {
        "encoder_source_sha256": source_sha,
        "question_id": str(row["question_id"]),
        "question": row["question"],
    }


def build_cache_plan(
    rows: Sequence[Mapping[str, Any]],
    *,
    p2_manifest_sha256: str,
    encoder_source_sha256: Mapping[str, str],
    hidden_size: int = 3584,
    dtype_bytes: int = 4,
) -> dict[str, Any]:
    """为首阶段记录生成 content-addressed audio/video/question 引用。"""

    required_sources = {"audio", "video", "question"}
    if set(encoder_source_sha256) != required_sources:
        raise ValueError("encoder source SHA 必须精确包含 audio/video/question")
    selected = [row for row in rows if row.get("first_stage_training") is True]
    if not selected:
        raise ValueError("没有首阶段 P2 记录")
    stores: dict[str, dict[str, dict[str, Any]]] = {
        "audio": {}, "video": {}, "question": {},
    }
    records = []
    for row in selected:
        payloads = {
            "audio": _audio_payload(row, encoder_source_sha256["audio"]),
            "video": _video_payload(row, encoder_source_sha256["video"]),
            "question": _question_payload(row, encoder_source_sha256["question"]),
        }
        keys = {name: _canonical_hash(payload) for name, payload in payloads.items()}
        for name, key in keys.items():
            existing = stores[name].setdefault(key, payloads[name])
            if existing != payloads[name]:
                raise RuntimeError(f"{name} cache key 碰撞")
        records.append({
            "record_id": str(row["record_id"]),
            "pair_group_id": str(row["pair_group_id"]),
            "audio_key": keys["audio"],
            "video_key": keys["video"],
            "question_key": keys["question"],
            "router_target": int(row["router_target"]),
            "quality_rank": row.get("quality_rank"),
            "relevance_rank": row.get("relevance_rank"),
            "sync_rank": row.get("sync_rank"),
            "condition": row["condition"],
            "modality": row["modality"],
        })
    audio_bytes = sum(
        int(payload["window_count"]) * hidden_size * dtype_bytes
        for payload in stores["audio"].values()
    )
    signal_bytes = sum(
        int(payload["window_count"]) * 4 * dtype_bytes
        for payload in stores["audio"].values()
    )
    video_bytes = sum(
        int(payload["window_count"]) * hidden_size * dtype_bytes
        for payload in stores["video"].values()
    )
    question_bytes = len(stores["question"]) * hidden_size * dtype_bytes
    uncached_bytes = sum(
        (2 * int(row["target_stream_window_count"]) * hidden_size + hidden_size
         + int(row["target_stream_window_count"]) * 4) * dtype_bytes
        for row in selected
    )
    deduplicated_bytes = audio_bytes + signal_bytes + video_bytes + question_bytes
    return {
        "schema": CACHE_PLAN_SCHEMA,
        "p2_manifest_sha256": p2_manifest_sha256,
        "encoder_source_sha256": dict(encoder_source_sha256),
        "hidden_size": hidden_size,
        "dtype": "float32",
        "dtype_bytes": dtype_bytes,
        "record_count": len(records),
        "unique_counts": {name: len(values) for name, values in stores.items()},
        "estimated_tensor_bytes": {
            "audio": audio_bytes,
            "audio_signal": signal_bytes,
            "video": video_bytes,
            "question": question_bytes,
            "deduplicated_total": deduplicated_bytes,
            "uncached_total": uncached_bytes,
            "saving_ratio": 1.0 - deduplicated_bytes / uncached_bytes,
        },
        "stores": stores,
        "records": records,
    }


def audit_cache_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    if plan.get("schema") != CACHE_PLAN_SCHEMA:
        raise ValueError("P3 cache plan schema 不匹配")
    records = plan.get("records") or []
    stores = plan.get("stores") or {}
    if len(records) != int(plan.get("record_count", -1)) or not records:
        raise ValueError("P3 cache plan record_count 不一致")
    if set(stores) != {"audio", "video", "question"}:
        raise ValueError("P3 cache stores 不完整")
    seen = set()
    for record in records:
        if record["record_id"] in seen:
            raise ValueError("P3 cache plan record_id 重复")
        seen.add(record["record_id"])
        for name in stores:
            key = record[f"{name}_key"]
            if key not in stores[name]:
                raise ValueError(f"P3 record 引用缺失的 {name} key")
            if _canonical_hash(stores[name][key]) != key:
                raise ValueError(f"P3 {name} cache key 与 payload 不一致")
    expected_counts = {name: len(values) for name, values in stores.items()}
    if plan.get("unique_counts") != expected_counts:
        raise ValueError("P3 unique_counts 不一致")
    sizes = plan["estimated_tensor_bytes"]
    if not 0.0 < float(sizes["saving_ratio"]) < 1.0:
        raise ValueError("P3 cache saving ratio 无效")
    return {
        "status": "PASS_P3_FEATURE_CACHE_PLAN_AUDIT",
        "record_count": len(records),
        "unique_counts": expected_counts,
        "deduplicated_total_bytes": int(sizes["deduplicated_total"]),
        "uncached_total_bytes": int(sizes["uncached_total"]),
        "saving_ratio": float(sizes["saving_ratio"]),
    }
