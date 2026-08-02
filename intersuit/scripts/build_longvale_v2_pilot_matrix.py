#!/usr/bin/env python3
"""从冻结 LongVALE pilot/score 资产构建 17 条件模型推理矩阵。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import torch

from intersuit.harness.dynamic_selector_v2_audio import topk_from_scores
from intersuit.harness.dynamic_selector_v2_localization import (
    oracle_topk_indices,
    random_topk_indices,
    uniform_topk_indices,
)
from intersuit.harness.longvale_v2_preflight import CONTROL_SEEDS, TOP_K


PROJECT_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = PROJECT_ROOT / "intersuit"
DEFAULT_PREFLIGHT_DIR = INTERSUIT_ROOT / "harness/artifacts/dynamic_selector_v2_v2/preflight"
DEFAULT_SCORE_DIR = INTERSUIT_ROOT / "harness/artifacts/dynamic_selector_v2_v2/pilot_scores"
DEFAULT_MEDIA_ROOT = INTERSUIT_ROOT / "datasets/LongVALE_V2/oracle_pilot_media"
DEFAULT_OUTPUT = INTERSUIT_ROOT / "harness/artifacts/dynamic_selector_v2_v2/oracle_pilot"
CHECKPOINT = "intersuit/checkpoints/AS-M4-BEATs-semantic-contrast-canary150-v1/checkpoint-150"
CHECKPOINT_LOCK = INTERSUIT_ROOT / "configs/dynamic_selector_v2_checkpoint_lock.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight-dir", type=Path, default=DEFAULT_PREFLIGHT_DIR)
    parser.add_argument("--score-dir", type=Path, default=DEFAULT_SCORE_DIR)
    parser.add_argument("--media-root", type=Path, default=DEFAULT_MEDIA_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def condition_names() -> list[str]:
    return [
        "text_only", "all", "uniform_topk", "energy_topk", "onset_topk",
        "eventness_cached_topk",
        *(f"random_topk_seed_{seed}" for seed in CONTROL_SEEDS),
        "oracle_topk",
        *(f"oracle_mismatch_seed_{seed}" for seed in CONTROL_SEEDS),
    ]


def select_indices(
    condition: str,
    *,
    sample_id: str,
    target_start: float,
    target_end: float,
    target_duration: float,
    score_payload: dict,
    mismatch_payload: dict | None = None,
) -> list[int] | None:
    if condition in {"text_only", "all"}:
        return None
    active = mismatch_payload if mismatch_payload is not None else score_payload
    timestamps = active["timestamps"].tolist()
    if condition == "uniform_topk":
        return sorted(uniform_topk_indices(len(timestamps), TOP_K))
    if condition == "energy_topk":
        return sorted(topk_from_scores(active["energy_scores"].tolist(), TOP_K))
    if condition == "onset_topk":
        return sorted(topk_from_scores(active["onset_scores"].tolist(), TOP_K))
    if condition == "eventness_cached_topk":
        return sorted(topk_from_scores(active["eventness_cached_scores"].tolist(), TOP_K))
    if condition.startswith("random_topk_seed_"):
        seed = int(condition.rsplit("_", 1)[-1])
        return sorted(random_topk_indices(len(timestamps), TOP_K, seed=seed, sample_id=sample_id))
    if condition == "oracle_topk":
        return sorted(oracle_topk_indices(timestamps, [[target_start, target_end]], TOP_K))
    if condition.startswith("oracle_mismatch_seed_"):
        if mismatch_payload is None:
            raise ValueError("oracle mismatch 缺少错配 score payload")
        source_duration = float(mismatch_payload["metadata"]["duration_sec"])
        normalized_start = target_start / target_duration
        normalized_end = target_end / target_duration
        mapped_interval = [[normalized_start * source_duration, normalized_end * source_duration]]
        return sorted(oracle_topk_indices(timestamps, mapped_interval, TOP_K))
    raise ValueError(f"未知 selector condition：{condition}")


def validate_score_payload(payload: dict, *, video_id: str) -> None:
    if payload.get("video_id") != video_id:
        raise ValueError(f"score payload video_id 漂移：{video_id}")
    count = int(payload["timestamps"].shape[0])
    if count <= 0 or payload["timestamps"].shape != (count, 2):
        raise ValueError(f"score timestamps 非法：{video_id}")
    for key in ("energy_scores", "onset_scores", "eventness_cached_scores"):
        if payload[key].shape != (count,) or not torch.isfinite(payload[key]).all():
            raise ValueError(f"score payload {key} 非法：{video_id}")


def main() -> None:
    args = parse_args()
    preflight_lock = json.loads((args.preflight_dir / "preflight_lock.json").read_text(encoding="utf-8"))
    checkpoint_lock = json.loads(CHECKPOINT_LOCK.read_text(encoding="utf-8"))
    score_summary = json.loads((args.score_dir / "summary.json").read_text(encoding="utf-8"))
    pilot_manifest_path = args.preflight_dir / "oracle_pilot.jsonl"
    if (
        preflight_lock.get("status") != "pass"
        or preflight_lock["permissions"].get("oracle_pilot") is not True
        or preflight_lock["permissions"].get("oracle_confirm") is not False
        or checkpoint_lock.get("v2_oracle_pilot_allowed") is not True
        or checkpoint_lock.get("v2_oracle_confirm_allowed") is not False
    ):
        raise ValueError("pilot-only 权限锁不满足")
    if score_summary.get("status") != "pass" or score_summary.get("scope") != "full_pilot":
        raise ValueError("完整 pilot score 资产未通过")
    if score_summary.get("manifest_sha256") != sha256(pilot_manifest_path):
        raise ValueError("score 资产与 pilot manifest SHA 不一致")

    score_manifest = read_jsonl(args.score_dir / "score_manifest.jsonl")
    if sha256(args.score_dir / "score_manifest.jsonl") != score_summary["score_manifest_sha256"]:
        raise ValueError("score manifest SHA 漂移")
    score_by_video = {}
    for row in score_manifest:
        path = Path(row["feature_path"])
        if sha256(path) != row["feature_sha256"]:
            raise ValueError(f"score feature SHA 漂移：{row['video_id']}")
        payload = torch.load(path, map_location="cpu", weights_only=True)
        validate_score_payload(payload, video_id=row["video_id"])
        score_by_video[row["video_id"]] = payload
    if len(score_by_video) != 100:
        raise ValueError(f"pilot score 应有 100 个视频，实际 {len(score_by_video)}")

    base_rows = read_jsonl(pilot_manifest_path)
    mismatch_by_seed = json.loads(
        (args.preflight_dir / "oracle_pilot_mismatch_mappings.json").read_text(encoding="utf-8")
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_dir = args.output_dir / "manifests"
    prediction_dir = args.output_dir / "predictions"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    prediction_dir.mkdir(parents=True, exist_ok=True)

    condition_hashes = {}
    selection_hashes = {}
    plan_rows = []
    for condition in condition_names():
        samples = []
        selection_records = []
        mismatch_seed = int(condition.rsplit("_", 1)[-1]) if condition.startswith("oracle_mismatch_seed_") else None
        for row in base_rows:
            video_id = row["video_id"]
            score_payload = score_by_video[video_id]
            source_video_id = video_id
            mismatch_payload = None
            if mismatch_seed is not None:
                source_video_id = mismatch_by_seed[str(mismatch_seed)][video_id]
                mismatch_payload = score_by_video[source_video_id]
                if source_video_id == video_id:
                    raise ValueError("oracle mismatch 映射出现同视频")
            selected = select_indices(
                condition,
                sample_id=row["sample_id"],
                target_start=float(row["event_start_sec"]),
                target_end=float(row["event_end_sec"]),
                target_duration=float(row["duration_sec"]),
                score_payload=score_payload,
                mismatch_payload=mismatch_payload,
            )
            if selected is not None and (len(selected) != min(TOP_K, len((mismatch_payload or score_payload)["timestamps"]))):
                raise ValueError(f"{condition}/{row['sample_id']} K 数量不一致")
            question = row["prompt"]
            sample = {
                "id": row["sample_id"],
                "physical_media_id": video_id,
                "source_dataset": "LongVALE-training",
                "task_type": "seg_captioning",
                "sentence_id": int(row["event_index"]),
                "event_start_sec": float(row["event_start_sec"]),
                "event_end_sec": float(row["event_end_sec"]),
                "is_sparse": bool(row["is_sparse"]),
                "selector_strategy": condition,
                "video_path": str((args.media_root / f"{video_id}.mp4").resolve()),
                "video_max_frames": 32,
                "scene_audio_path": str((args.media_root / f"{source_video_id}.mp4").resolve()),
                "scene_audio_sample_rate": 16000,
                "scene_audio_window_sec": 1.0,
                "scene_audio_hop_sec": 1.0,
                "scene_audio_expected_duration_sec": float(
                    (mismatch_payload or score_payload)["metadata"]["duration_sec"]
                ),
                "scene_audio_selected_indices": selected,
                "source_audio_video_id": source_video_id,
                "question": question,
                "conversations": [
                    {"from": "human", "value": f"<image>\n{question}"},
                    {"from": "gpt", "value": row["caption"]},
                ],
            }
            samples.append(sample)
            selection_records.append(
                {
                    "sample_id": row["sample_id"],
                    "video_id": video_id,
                    "source_audio_video_id": source_video_id,
                    "selected_source_indices": selected,
                }
            )
        manifest_path = manifest_dir / f"{condition}.json"
        selection_path = manifest_dir / f"{condition}_selections.jsonl"
        write_json(manifest_path, samples)
        selection_path.write_text(
            "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in selection_records),
            encoding="utf-8",
        )
        condition_hashes[condition] = sha256(manifest_path)
        selection_hashes[condition] = sha256(selection_path)
        env = {
            "AS_M4_ENABLE_SCENE_AUDIO": "0" if condition == "text_only" else "1",
            "AS_M4_FUSION_MODE": "beats_simple_residual",
            "AS_M4_INFERENCE_SIMPLE_AUDIO_GATE": "0.005",
            "AS_M4_DEBUG_AUDIO_RESIDUAL_SCALE": "1.0",
            "AS_M4_AUDIO_DELTA_RATIO_CAP": "0.0",
            "AS_M4_ENABLE_AUDIO_CONFIDENCE_GATE_V1": "0",
            "AS_M4_ENABLE_AUDIO_EVENT_ALIGNER_V1": "0",
        }
        plan_rows.append(
            {
                "id": condition,
                "model_path": CHECKPOINT,
                "manifest": str(manifest_path.resolve()),
                "manifest_sha256": condition_hashes[condition],
                "output_jsonl": str((prediction_dir / f"{condition}.jsonl").resolve()),
                "audio_condition": "none" if condition == "text_only" else "correct",
                "scorer": "exact_match",
                "env": env,
            }
        )

    plan_path = args.output_dir / "matrix_plan.jsonl"
    plan_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in plan_rows),
        encoding="utf-8",
    )
    annotation_by_video = {}
    for row in base_rows:
        item = annotation_by_video.setdefault(
            row["video_id"],
            {"duration": row["duration_sec"], "timestamps": [], "sentences": []},
        )
        item["timestamps"].append([row["event_start_sec"], row["event_end_sec"]])
        item["sentences"].append(row["caption"])
    annotation_path = args.output_dir / "pilot_annotation.json"
    write_json(annotation_path, annotation_by_video)

    lock = {
        "artifact_kind": "dynamic_selector_v2_longvale_oracle_pilot_matrix_lock",
        "format_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "pass",
        "sample_count": len(base_rows),
        "video_count": len(annotation_by_video),
        "sparse_sample_count": sum(bool(row["is_sparse"]) for row in base_rows),
        "conditions": condition_names(),
        "condition_count": len(condition_names()),
        "top_k": TOP_K,
        "random_seeds": list(CONTROL_SEEDS),
        "mismatch_seeds": list(CONTROL_SEEDS),
        "mismatch_time_mapping": "target normalized [start,end] mapped to source duration; source annotations not read",
        "video_max_frames": 32,
        "checkpoint": CHECKPOINT,
        "checkpoint_lock_sha256": sha256(CHECKPOINT_LOCK),
        "preflight_lock_sha256": sha256(args.preflight_dir / "preflight_lock.json"),
        "score_summary_sha256": sha256(args.score_dir / "summary.json"),
        "manifest_sha256": condition_hashes,
        "selection_sha256": selection_hashes,
        "plan_sha256": sha256(plan_path),
        "annotation_sha256": sha256(annotation_path),
        "temporal_scorer": {"status": "not_qualified", "included": False},
        "optimizer_created": False,
        "confirm_allowed": False,
        "learned_selector_training_allowed": False,
    }
    write_json(args.output_dir / "matrix_lock.json", lock)
    print(json.dumps(lock, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
