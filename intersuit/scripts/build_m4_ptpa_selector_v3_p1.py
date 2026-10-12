#!/usr/bin/env python3
"""构建并审计 M4-PTPA Dynamic Selector V3 的 P1 离线矩阵。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
from statistics import mean
from typing import Any

import torch

from intersuit.model.streaming_av.selection_contract import (
    DEFAULT_SELECTION_POLICY_REGISTRY,
    SelectionRequest,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
V2_ROOT = PROJECT_ROOT / "intersuit/harness/artifacts/dynamic_selector_v2_v2"
DEFAULT_OUTPUT = PROJECT_ROOT / "intersuit/harness/artifacts/m4_ptpa_audio_dynamic_selector_v3/p1_offline"
PREFLIGHT_LOCK = V2_ROOT / "preflight/preflight_lock.json"
PILOT_MANIFEST = V2_ROOT / "preflight/oracle_pilot.jsonl"
CONFIRM_MANIFEST = V2_ROOT / "preflight/oracle_confirm.jsonl"
PILOT_MEDIA_AUDIT = V2_ROOT / "preflight/oracle_pilot_media_audit.json"
SCORE_SUMMARY = V2_ROOT / "pilot_scores/summary.json"
SCORE_MANIFEST = V2_ROOT / "pilot_scores/score_manifest.jsonl"
PILOT_MEDIA_ROOT = PROJECT_ROOT / "intersuit/datasets/LongVALE_V2/oracle_pilot_media"

EXPECTED_SHA256 = {
    PREFLIGHT_LOCK: "c5169e286e8f11f83af576078ff39a2b4fbb0a997aaadb4d98fbd816c034935e",
    PILOT_MANIFEST: "b4f04bcb1190e80882dae35ad9d8a461106832a95a4c7486c5354a602f6ea162",
    CONFIRM_MANIFEST: "a19fb20ee5440ac888e1fcd387f46f643053ae66820f35c97eccdd2afe21e926",
    PILOT_MEDIA_AUDIT: "68114ba9a26ed7659d939ba30ac56edd10d9acd3dccc90526d017ff6e0091e9a",
    SCORE_SUMMARY: "376b557e7060685a418f49a8167c7087ccd00486d0b9ecee134ce7a385b35b34",
    SCORE_MANIFEST: "a9ca652b8cd792ff3b1be9d64bee126ef0a520e603984459370e5db4c1317678",
}
CONTROL_SEEDS = (20260720, 20260721, 20260722, 20260723, 20260724)
BOOTSTRAP_SEED = 20260724
BOOTSTRAP_REPEATS = 10_000
EVENT_NAMESPACE = "m4-ptpa-dynamic-selector-v3-one-sparse-event-20260811"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--skip-media-rehash", action="store_true")
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def verify_input_locks() -> dict[str, str]:
    actual: dict[str, str] = {}
    for path, expected in EXPECTED_SHA256.items():
        if not path.is_file():
            raise FileNotFoundError(path)
        digest = sha256_file(path)
        if digest != expected:
            raise ValueError(f"P1 input SHA 漂移: {path}, expected={expected}, actual={digest}")
        actual[str(path.relative_to(PROJECT_ROOT))] = digest
    return actual


def freeze_one_sparse_event_per_video(rows: list[dict[str, Any]], expected_videos: int) -> list[dict[str, Any]]:
    by_video: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if bool(row.get("is_sparse")):
            by_video.setdefault(str(row["video_id"]), []).append(row)
    if len(by_video) != expected_videos:
        raise ValueError(f"稀疏视频数不一致: expected={expected_videos}, actual={len(by_video)}")

    selected = []
    for video_id in sorted(by_video):
        candidates = by_video[video_id]
        chosen = min(
            candidates,
            key=lambda row: (
                hashlib.sha256(f"{EVENT_NAMESPACE}:{row['sample_id']}".encode()).digest(),
                str(row["sample_id"]),
            ),
        )
        selected.append(chosen)
    if len({row["sample_id"] for row in selected}) != expected_videos:
        raise AssertionError("one-event manifest sample_id 不唯一")
    return selected


def load_and_verify_scores() -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    summary = read_json(SCORE_SUMMARY)
    if summary.get("status") != "pass" or summary.get("scope") != "full_pilot":
        raise ValueError("旧 pilot score summary 不合格")
    rows = read_jsonl(SCORE_MANIFEST)
    if len(rows) != 100 or len({row["video_id"] for row in rows}) != 100:
        raise ValueError("pilot score manifest 必须恰有 100 个唯一视频")
    scores: dict[str, dict[str, Any]] = {}
    for row in rows:
        path = Path(row["feature_path"])
        if not path.is_file() or sha256_file(path) != row["feature_sha256"]:
            raise ValueError(f"score feature SHA 漂移: {row['video_id']}")
        payload = torch.load(path, map_location="cpu", weights_only=True)
        count = int(payload["timestamps"].shape[0])
        if payload.get("video_id") != row["video_id"] or payload["timestamps"].shape != (count, 2):
            raise ValueError(f"score payload schema 漂移: {row['video_id']}")
        for key in ("energy_scores", "eventness_cached_scores"):
            if payload[key].shape != (count,) or not torch.isfinite(payload[key]).all():
                raise ValueError(f"score payload {key} 非法: {row['video_id']}")
        if payload["metadata"].get("window_sec") != 1.0 or payload["metadata"].get("hop_sec") != 1.0:
            raise ValueError(f"score window contract 漂移: {row['video_id']}")
        scores[str(row["video_id"])] = payload
    return scores, summary


def verify_pilot_media(rehash: bool) -> dict[str, Any]:
    audit = read_json(PILOT_MEDIA_AUDIT)
    if audit.get("status") != "pass" or len(audit.get("videos", [])) != 100:
        raise ValueError("pilot media audit 不合格")
    checked = 0
    for row in audit["videos"]:
        path = PILOT_MEDIA_ROOT / f"{row['video_id']}.mp4"
        if not path.is_file() or path.stat().st_size != int(row["bytes"]):
            raise ValueError(f"pilot media 缺失或 size 漂移: {row['video_id']}")
        if rehash and sha256_file(path) != row["sha256"]:
            raise ValueError(f"pilot media SHA 漂移: {row['video_id']}")
        checked += 1
    return {"status": "pass", "video_count": checked, "content_sha256_rechecked": rehash}


def interval_metrics(selected_timestamps: list[list[float]], target: tuple[float, float]) -> dict[str, float]:
    target_start, target_end = target
    target_duration = target_end - target_start
    overlaps = [max(0.0, min(end, target_end) - max(start, target_start)) for start, end in selected_timestamps]
    best_tiou = 0.0
    for (start, end), overlap in zip(selected_timestamps, overlaps):
        union = max(end, target_end) - min(start, target_start)
        best_tiou = max(best_tiou, overlap / union if union > 0 else 0.0)
    covered = min(target_duration, sum(overlaps))
    return {
        "event_coverage": float(any(value > 0 for value in overlaps)),
        "positive_recall": covered / target_duration if target_duration > 0 else 0.0,
        "hit_at_k": float(any(value > 0 for value in overlaps)),
        "best_tiou": best_tiou,
    }


def first_hit_rank(trace: dict[str, Any], target: tuple[float, float]) -> int | None:
    items = list(zip(trace["selection_scores"], trace["selected_timestamps"], trace["selected_source_indices"]))
    ranked = sorted(items, key=lambda item: (-float(item[0]), float(item[1][0]), int(item[2])))
    for rank, (_, (start, end), _) in enumerate(ranked, start=1):
        if max(0.0, min(end, target[1]) - max(start, target[0])) > 0:
            return rank
    return None


def select(policy: str, row: dict[str, Any], payload: dict[str, Any], k: int, seed: int | None = None):
    request = SelectionRequest(
        sample_id=str(row["sample_id"]),
        source_indices=torch.arange(payload["timestamps"].shape[0]),
        timestamps=payload["timestamps"],
        valid_mask=torch.ones(payload["timestamps"].shape[0], dtype=torch.bool),
        top_k=None if policy == "fixed_all" else k,
        scores={
            "energy": payload["energy_scores"],
            "eventness": payload["eventness_cached_scores"],
        },
        target_intervals=torch.tensor([[row["event_start_sec"], row["event_end_sec"]]]),
        seed=seed,
        diagnostic=policy == "oracle_topk",
        production=policy != "oracle_topk",
    )
    return DEFAULT_SELECTION_POLICY_REGISTRY.select(policy, request)


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(quantile * (len(ordered) - 1)))))
    return ordered[index]


def bootstrap_delta(oracle: list[float], random_means: list[float]) -> dict[str, float]:
    if len(oracle) != len(random_means) or not oracle:
        raise ValueError("bootstrap 输入长度不一致或为空")
    rng = random.Random(BOOTSTRAP_SEED)
    deltas = []
    count = len(oracle)
    for _ in range(BOOTSTRAP_REPEATS):
        indices = [rng.randrange(count) for _ in range(count)]
        deltas.append(mean(oracle[index] - random_means[index] for index in indices))
    observed = mean(left - right for left, right in zip(oracle, random_means))
    return {
        "observed_delta": observed,
        "ci95_low": percentile(deltas, 0.025),
        "ci95_high": percentile(deltas, 0.975),
        "bootstrap_repeats": BOOTSTRAP_REPEATS,
        "bootstrap_seed": BOOTSTRAP_SEED,
    }


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault((int(record["k"]), str(record["policy"])), []).append(record)
    summary: dict[str, Any] = {}
    for (k, policy), rows in sorted(grouped.items()):
        key = f"k{k}/{policy}"
        summary[key] = {
            "sample_count": len(rows),
            "event_coverage": mean(row["metrics"]["event_coverage"] for row in rows),
            "positive_recall": mean(row["metrics"]["positive_recall"] for row in rows),
            "hit_at_k": mean(row["metrics"]["hit_at_k"] for row in rows),
            "mean_best_tiou": mean(row["metrics"]["best_tiou"] for row in rows),
            "mean_first_hit_rank": mean(
                row["first_hit_rank"] for row in rows if row["first_hit_rank"] is not None
            ),
        }
    return summary


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    input_hashes = verify_input_locks()
    preflight = read_json(PREFLIGHT_LOCK)
    if preflight.get("status") != "pass" or preflight["permissions"].get("oracle_confirm") is not False:
        raise ValueError("历史 preflight 权限边界漂移")

    pilot_rows = freeze_one_sparse_event_per_video(read_jsonl(PILOT_MANIFEST), 100)
    confirm_rows = freeze_one_sparse_event_per_video(read_jsonl(CONFIRM_MANIFEST), 200)
    pilot_ids = {row["video_id"] for row in pilot_rows}
    confirm_ids = {row["video_id"] for row in confirm_rows}
    overlap = sorted(pilot_ids & confirm_ids)
    if overlap:
        raise ValueError(f"pilot/confirm video_id 交集非空: {overlap[:10]}")

    scores, score_summary = load_and_verify_scores()
    if set(scores) != pilot_ids:
        raise ValueError("pilot one-event manifest 与 score cache video_id 不一致")
    media_status = verify_pilot_media(rehash=not args.skip_media_rehash)

    write_jsonl(args.output_dir / "pilot_one_sparse_event.jsonl", pilot_rows)
    write_jsonl(args.output_dir / "confirm_one_sparse_event_locked_not_allowed.jsonl", confirm_rows)

    records: list[dict[str, Any]] = []
    policies = ("fixed_all", "uniform_topk", "energy_topk", "eventness_topk", "oracle_topk")
    for k in (4, 8):
        for row in pilot_rows:
            payload = scores[str(row["video_id"])]
            target = (float(row["event_start_sec"]), float(row["event_end_sec"]))
            for policy in policies:
                result = select(policy, row, payload, k)
                trace = result.to_trace()
                metrics = interval_metrics(trace["selected_timestamps"], target)
                records.append(
                    {
                        "sample_id": row["sample_id"],
                        "video_id": row["video_id"],
                        "k": k,
                        "policy": policy,
                        "trace": trace,
                        "metrics": metrics,
                        "first_hit_rank": first_hit_rank(trace, target),
                    }
                )
            for seed in CONTROL_SEEDS:
                result = select("random_topk", row, payload, k, seed=seed)
                trace = result.to_trace()
                metrics = interval_metrics(trace["selected_timestamps"], target)
                records.append(
                    {
                        "sample_id": row["sample_id"],
                        "video_id": row["video_id"],
                        "k": k,
                        "policy": f"random_topk_seed_{seed}",
                        "trace": trace,
                        "metrics": metrics,
                        "first_hit_rank": first_hit_rank(trace, target),
                    }
                )
    write_jsonl(args.output_dir / "selection_traces.jsonl", records)
    metric_summary = summarize(records)

    bootstrap: dict[str, Any] = {}
    for k in (4, 8):
        by_key = {(row["sample_id"], row["policy"]): row for row in records if row["k"] == k}
        for metric in ("event_coverage", "positive_recall", "best_tiou"):
            oracle_values = []
            random_values = []
            for row in pilot_rows:
                sample_id = row["sample_id"]
                oracle_values.append(by_key[(sample_id, "oracle_topk")]["metrics"][metric])
                random_values.append(
                    mean(
                        by_key[(sample_id, f"random_topk_seed_{seed}")]["metrics"][metric]
                        for seed in CONTROL_SEEDS
                    )
                )
            bootstrap[f"k{k}/oracle_minus_random_mean/{metric}"] = bootstrap_delta(
                oracle_values, random_values
            )

    localization_gate = all(
        metric_summary[f"k{k}/oracle_topk"]["event_coverage"] == 1.0
        and bootstrap[f"k{k}/oracle_minus_random_mean/event_coverage"]["ci95_low"] > 0
        for k in (4, 8)
    )
    review = {
        "artifact_kind": "m4_ptpa_dynamic_selector_v3_p1_review",
        "format_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "pass_with_confirm_locked" if localization_gate else "fail",
        "next_stage_allowed": bool(localization_gate),
        "input_hashes": input_hashes,
        "event_selection_namespace": EVENT_NAMESPACE,
        "pilot": {
            "video_count": len(pilot_rows),
            "sample_count": len(pilot_rows),
            "media": media_status,
            "score_window_count": score_summary["total_window_count"],
        },
        "confirm": {
            "video_count": len(confirm_rows),
            "sample_count": len(confirm_rows),
            "pilot_video_id_overlap": len(overlap),
            "media_downloaded": False,
            "physical_media_sha_audit": "pending_before_confirm",
            "allowed": False,
        },
        "historical_exposure": {
            "pilot": "old_v2_full_predictions_exist; diagnostic_only",
            "confirm": "no local media/scores/predictions found; still locked",
            "ave_official_test": "not_rerun",
        },
        "temporal_scorer": {"status": "omitted_ineligible_asset", "included": False},
        "metric_summary": metric_summary,
        "bootstrap": bootstrap,
        "optimizer_created": False,
        "gpu_used": False,
        "selector_training_allowed": False,
        "oracle_confirm_allowed": False,
        "blockers": [],
        "majors": [
            "confirm physical media SHA and train/history overlap remain pending; confirm stays locked"
        ],
    }
    write_json(args.output_dir / "p1_review.json", review)
    lock = {
        "artifact_kind": "m4_ptpa_dynamic_selector_v3_p1_lock",
        "format_version": 1,
        "status": review["status"],
        "model_version": "M4-LongVA-7B-Qwen2-PTPA-Best-AudioBranch-v1",
        "code_commit": "eb53be1",
        "input_hashes": input_hashes,
        "pilot_manifest_sha256": sha256_file(args.output_dir / "pilot_one_sparse_event.jsonl"),
        "confirm_manifest_sha256": sha256_file(
            args.output_dir / "confirm_one_sparse_event_locked_not_allowed.jsonl"
        ),
        "selection_traces_sha256": sha256_file(args.output_dir / "selection_traces.jsonl"),
        "review_sha256": sha256_file(args.output_dir / "p1_review.json"),
        "next_stage_allowed": review["next_stage_allowed"],
        "oracle_confirm_allowed": False,
        "selector_training_allowed": False,
    }
    write_json(args.output_dir / "p1_lock.json", lock)
    print(json.dumps(review, ensure_ascii=False, indent=2, sort_keys=True))
    if review["status"] == "fail":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
