#!/usr/bin/env python3
"""构建并锁定 Dynamic Selector V3 的 P2 七条件 GPU smoke 矩阵。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
P1_ROOT = PROJECT_ROOT / "intersuit/harness/artifacts/m4_ptpa_audio_dynamic_selector_v3/p1_offline"
P1_MANIFEST = P1_ROOT / "pilot_one_sparse_event.jsonl"
P1_LOCK = P1_ROOT / "p1_lock.json"
PILOT_MEDIA_AUDIT = PROJECT_ROOT / "intersuit/harness/artifacts/dynamic_selector_v2_v2/preflight/oracle_pilot_media_audit.json"
MEDIA_ROOT = PROJECT_ROOT / "intersuit/datasets/LongVALE_V2/oracle_pilot_media"
MODEL_PATH = PROJECT_ROOT / "intersuit/checkpoints/M4-LongVA-7B-Qwen2-PTPA-Best-AudioBranch-v1"
EVAL_LOCK = PROJECT_ROOT / "intersuit/configs/m4_ptpa_best_audio_branch_v1.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "intersuit/harness/artifacts/m4_ptpa_audio_dynamic_selector_v3/p2_smoke"

EXPECTED_INPUT_SHA256 = {
    P1_LOCK: "caccb8193386dce3362439191708275624397210fc1248108f48e99ec7cb2e52",
    MODEL_PATH / "config.json": "359aed2660f9e8a3606d6d97d1fa89d5265d83415d414c56c3f21a5e17c2eeb8",
    MODEL_PATH / "audio-branch.safetensors": "de23fd7281b3835183533df384ab3398849b13c50386875a75b680cf4e12bf17",
    EVAL_LOCK: "1ccddb36060a5dacad6bd3cb156395339225a57841517991441c8cce0b4aec4d",
}
CONTROL_SEED = 20260720
SAMPLE_COUNT = 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def verify_inputs() -> dict[str, str]:
    actual = {}
    for path, expected in EXPECTED_INPUT_SHA256.items():
        if not path.is_file():
            raise FileNotFoundError(path)
        digest = sha256_file(path)
        if digest != expected:
            raise ValueError(f"P2 input SHA 漂移: {path}, expected={expected}, actual={digest}")
        actual[str(path.relative_to(PROJECT_ROOT))] = digest
    p1_lock = read_json(P1_LOCK)
    if p1_lock.get("next_stage_allowed") is not True:
        raise ValueError("P1 没有允许进入 P2")
    if p1_lock.get("oracle_confirm_allowed") is not False or p1_lock.get("selector_training_allowed") is not False:
        raise ValueError("P1 权限边界漂移")
    if sha256_file(P1_MANIFEST) != p1_lock["pilot_manifest_sha256"]:
        raise ValueError("P1 pilot manifest SHA 与 P1 lock 不一致")
    return actual


def select_smoke_rows() -> list[dict[str, Any]]:
    rows = read_jsonl(P1_MANIFEST)
    if len(rows) != 100:
        raise ValueError("P1 pilot 必须恰有 100 条")
    selected = sorted(rows, key=lambda row: (float(row["duration_sec"]), str(row["video_id"])))[:SAMPLE_COUNT]
    audit_by_video = {
        str(row["video_id"]): row for row in read_json(PILOT_MEDIA_AUDIT).get("videos", [])
    }
    for row in selected:
        media_path = MEDIA_ROOT / f"{row['video_id']}.mp4"
        audit = audit_by_video.get(str(row["video_id"]))
        if audit is None or not media_path.is_file():
            raise FileNotFoundError(f"P2 smoke media 缺失: {row['video_id']}")
        if media_path.stat().st_size != int(audit["bytes"]) or sha256_file(media_path) != audit["sha256"]:
            raise ValueError(f"P2 smoke media SHA/size 漂移: {row['video_id']}")
        row["physical_media_sha256"] = audit["sha256"]
    return selected


def base_sample(row: dict[str, Any]) -> dict[str, Any]:
    media_path = MEDIA_ROOT / f"{row['video_id']}.mp4"
    return {
        "id": row["sample_id"],
        "video_path": str(media_path),
        "video_max_frames": 32,
        "scene_audio_path": str(media_path),
        "scene_audio_sample_rate": 16000,
        "scene_audio_window_sec": 1.0,
        "scene_audio_hop_sec": 1.0,
        "scene_audio_expected_duration_sec": float(row["duration_sec"]),
        "physical_media_id": f"sha256:{row['physical_media_sha256']}",
        "source_dataset": "LongVALE_V3_PILOT_DIAGNOSTIC",
        "task_type": "sparse_event_caption",
        "event_start_sec": float(row["event_start_sec"]),
        "event_end_sec": float(row["event_end_sec"]),
        "is_sparse": True,
        "question": row["prompt"],
        "conversations": [
            {"from": "human", "value": f"<image>\n{row['prompt']}"},
            {"from": "gpt", "value": row["caption"]},
        ],
    }


def mapped_mismatch_intervals(rows: list[dict[str, Any]]) -> dict[str, list[list[float]]]:
    result = {}
    for index, row in enumerate(rows):
        source = rows[(index + 1) % len(rows)]
        target_duration = float(row["duration_sec"])
        source_duration = float(source["duration_sec"])
        start = source_duration * float(row["event_start_sec"]) / target_duration
        end = source_duration * float(row["event_end_sec"]) / target_duration
        result[str(row["sample_id"])] = [[start, min(end, source_duration)]]
    return result


def condition_samples(condition: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mismatch_intervals = mapped_mismatch_intervals(rows)
    samples = []
    for row in rows:
        sample = base_sample(row)
        sample["selector_strategy"] = condition
        if condition == "fixed_all":
            sample["scene_audio_selection_policy"] = "fixed_all"
        elif condition == "external_random_top8":
            sample.update(
                scene_audio_selection_policy="random_topk",
                scene_audio_selection_top_k=8,
                scene_audio_selection_seed=CONTROL_SEED,
            )
        elif condition == "external_oracle_top8":
            sample.update(
                scene_audio_selection_policy="oracle_topk",
                scene_audio_selection_top_k=8,
                scene_audio_selection_diagnostic=True,
                scene_audio_target_intervals=[[float(row["event_start_sec"]), float(row["event_end_sec"])]],
            )
        elif condition == "oracle_mismatch":
            sample.update(
                scene_audio_selection_policy="oracle_topk",
                scene_audio_selection_top_k=8,
                scene_audio_selection_diagnostic=True,
                scene_audio_target_intervals=mismatch_intervals[str(row["sample_id"])],
            )
        samples.append(sample)
    return samples


def active_audio_env() -> dict[str, str]:
    return {
        "AS_M4_ROLLBACK_MODE": "none",
        "AS_M4_ENABLE_SCENE_AUDIO": "1",
        "AS_M4_FUSION_MODE": "beats_simple_residual",
        "AS_M4_INFERENCE_SIMPLE_AUDIO_GATE": "0.005",
        "AS_M4_DEBUG_AUDIO_RESIDUAL_SCALE": "1.0",
        "AS_M4_AUDIO_DELTA_RATIO_CAP": "0.0",
        "AS_M4_ENABLE_AUDIO_CONFIDENCE_GATE_V1": "0",
        "AS_M4_ENABLE_AUDIO_EVENT_ALIGNER_V1": "0",
        "AS_M4_DEBUG_CAPTURE_FIRST_TOKEN_LOGITS": "0",
    }


def experiment_env(condition: str) -> dict[str, str]:
    env = active_audio_env()
    if condition == "behavior_off":
        env.update(AS_M4_ROLLBACK_MODE="behavior", AS_M4_ENABLE_SCENE_AUDIO="0", AS_M4_FORCE_AUDIO_GATE="0")
    elif condition == "gate0":
        env.update(AS_M4_ROLLBACK_MODE="gate0", AS_M4_FORCE_AUDIO_GATE="0")
    return env


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    input_hashes = verify_inputs()
    rows = select_smoke_rows()
    conditions = [
        ("behavior_off", "none"),
        ("gate0", "correct"),
        ("current_micro_top8", "correct"),
        ("fixed_all", "correct"),
        ("external_random_top8", "correct"),
        ("external_oracle_top8", "correct"),
        ("oracle_mismatch", "mismatched"),
    ]
    plan = []
    manifest_hashes = {}
    for condition, audio_condition in conditions:
        manifest = args.output_dir / "manifests" / f"{condition}.json"
        write_json(manifest, condition_samples(condition, rows))
        manifest_hash = sha256_file(manifest)
        manifest_hashes[condition] = manifest_hash
        plan.append(
            {
                "id": condition,
                "description": f"Dynamic Selector V3 P2 smoke: {condition}",
                "dataset": "LongVALE_V3_PILOT_DIAGNOSTIC",
                "split": "p2_two_sample_smoke",
                "manifest": str(manifest),
                "manifest_sha256": manifest_hash,
                "scorer": "exact_match",
                "model_path": str(MODEL_PATH),
                "audio_condition": audio_condition,
                "alignment": "off",
                "gate_ablation": "all" if condition == "gate0" else "none",
                "env": experiment_env(condition),
                "output_jsonl": str(args.output_dir / "predictions" / f"{condition}.jsonl"),
            }
        )
    write_jsonl(args.output_dir / "matrix_plan.jsonl", plan)
    lock = {
        "artifact_kind": "m4_ptpa_dynamic_selector_v3_p2_lock",
        "format_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "locked_not_run",
        "model_version": MODEL_PATH.name,
        "input_hashes": input_hashes,
        "matrix_plan_sha256": sha256_file(args.output_dir / "matrix_plan.jsonl"),
        "manifest_hashes": manifest_hashes,
        "sample_ids": [row["sample_id"] for row in rows],
        "physical_media_sha256": [row["physical_media_sha256"] for row in rows],
        "sample_count_per_condition": SAMPLE_COUNT,
        "condition_order": [condition for condition, _ in conditions],
        "random_seed": CONTROL_SEED,
        "top_k": 8,
        "gpu_count": 1,
        "gpu_physical_id": "must_be_recorded_by_preflight",
        "confirm_accessed": False,
        "selector_training_allowed": False,
        "required_absent_process_env": ["AS_M4_FORCE_AUDIO_GATE"],
    }
    write_json(args.output_dir / "p2_lock.json", lock)
    print(json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
