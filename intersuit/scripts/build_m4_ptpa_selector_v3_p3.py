#!/usr/bin/env python3
"""构建并锁定 Dynamic Selector V3 的 P3 LongVALE canary/full 矩阵。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_ROOT = PROJECT_ROOT / "intersuit/harness/artifacts"
V3_ROOT = ARTIFACT_ROOT / "m4_ptpa_audio_dynamic_selector_v3"
P1_MANIFEST = V3_ROOT / "p1_offline/pilot_one_sparse_event.jsonl"
P1_LOCK = V3_ROOT / "p1_offline/p1_lock.json"
P2_REVIEW = V3_ROOT / "p2_smoke_rerun1/p2_review.json"
P3_CANARY_REVIEW = V3_ROOT / "p3_longvale_canary20/official_metrics/p3_canary_review.json"
V2_PREFLIGHT = ARTIFACT_ROOT / "dynamic_selector_v2_v2/preflight"
MISMATCH_MAPPINGS = V2_PREFLIGHT / "oracle_pilot_mismatch_mappings.json"
MEDIA_AUDIT = V2_PREFLIGHT / "oracle_pilot_media_audit.json"
MEDIA_ROOT = PROJECT_ROOT / "intersuit/datasets/LongVALE_V2/oracle_pilot_media"
MODEL_PATH = PROJECT_ROOT / "intersuit/checkpoints/M4-LongVA-7B-Qwen2-PTPA-Best-AudioBranch-v1"
DEFAULT_OUTPUT = V3_ROOT / "p3_longvale_canary20"
FULL_OUTPUT = V3_ROOT / "p3_longvale_frozen100"

EXPECTED_SHA256 = {
    P1_LOCK: "caccb8193386dce3362439191708275624397210fc1248108f48e99ec7cb2e52",
    P2_REVIEW: "81665b455ea63cea562c77198d3fbf35057d1190185f96c64e06401fab5973e0",
    MISMATCH_MAPPINGS: "9005e6ba2294b979fe6490e7098375dde5b8506d14b03d4149fffffdb9d909ee",
    MEDIA_AUDIT: "68114ba9a26ed7659d939ba30ac56edd10d9acd3dccc90526d017ff6e0091e9a",
}
EXPECTED_CANARY_REVIEW_SHA256 = "c1b01b2cf268c122650f62d78c132e00d3c30e1adeaf44afa256f1fc9169dd42"
CONTROL_SEEDS = (20260720, 20260721, 20260722, 20260723, 20260724)
CANARY_COUNT = 20
TOP_K = 8
MAX_NEW_TOKENS = 128
BOOTSTRAP_SEED = 20260724
CANARY_NAMESPACE = "m4-ptpa-dynamic-selector-v3-p3-canary20-20260812"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=("canary20", "frozen100"), default="canary20")
    parser.add_argument("--output-dir", type=Path, default=None)
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


def verify_stage_locks(scope: str) -> dict[str, str]:
    actual = {}
    for path, expected in EXPECTED_SHA256.items():
        digest = sha256_file(path)
        if digest != expected:
            raise ValueError(f"P3 input SHA 漂移: {path}, expected={expected}, actual={digest}")
        actual[str(path.relative_to(PROJECT_ROOT))] = digest
    p1 = read_json(P1_LOCK)
    p2 = read_json(P2_REVIEW)
    if p1.get("next_stage_allowed") is not True or p1.get("selector_training_allowed") is not False:
        raise ValueError("P1 权限锁不允许 P3")
    if p2.get("next_stage_allowed") is not True or p2.get("p3_allowed") is not True:
        raise ValueError("P2 Review 未允许 P3")
    if p2.get("selector_training_allowed") is not False or p2.get("confirm_accessed") is not False:
        raise ValueError("P2 权限边界漂移")
    if scope == "frozen100":
        digest = sha256_file(P3_CANARY_REVIEW)
        if digest != EXPECTED_CANARY_REVIEW_SHA256:
            raise ValueError(
                "P3 canary Review SHA 漂移: "
                f"expected={EXPECTED_CANARY_REVIEW_SHA256}, actual={digest}"
            )
        review = read_json(P3_CANARY_REVIEW)
        if review.get("p3_full_allowed") is not True or review.get("status") != "pass_to_frozen_100":
            raise ValueError("P3 canary Review 未授权 frozen100")
        if review.get("selector_training_allowed") is not False or review.get("confirm_accessed") is not False:
            raise ValueError("P3 canary Review 权限边界漂移")
        actual[str(P3_CANARY_REVIEW.relative_to(PROJECT_ROOT))] = digest
    return actual


def freeze_canary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(rows) != 100 or len({row["video_id"] for row in rows}) != 100:
        raise ValueError("P3 输入必须是 100 个唯一 pilot 视频")
    return sorted(
        rows,
        key=lambda row: (
            hashlib.sha256(f"{CANARY_NAMESPACE}:{row['sample_id']}".encode()).digest(),
            str(row["sample_id"]),
        ),
    )[:CANARY_COUNT]


def select_scope_rows(scope: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if scope == "canary20":
        return freeze_canary(rows)
    if scope == "frozen100":
        if len(rows) != 100 or len({row["video_id"] for row in rows}) != 100:
            raise ValueError("frozen100 必须使用 P1 的 100 个唯一视频")
        return list(rows)
    raise ValueError(f"未知 P3 scope: {scope}")


def condition_names() -> list[str]:
    return [
        "behavior_off",
        "fixed_all",
        "current_micro_top8",
        "oracle_top8",
        *(f"random_top8_seed_{seed}" for seed in CONTROL_SEEDS),
        *(f"oracle_mismatch_seed_{seed}" for seed in CONTROL_SEEDS),
    ]


def mapped_interval(target: dict[str, Any], source: dict[str, Any]) -> list[list[float]]:
    target_duration = float(target["duration_sec"])
    source_duration = float(source["duration_sec"])
    return [[
        source_duration * float(target["event_start_sec"]) / target_duration,
        min(source_duration, source_duration * float(target["event_end_sec"]) / target_duration),
    ]]


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


def build_sample(
    condition: str,
    target: dict[str, Any],
    source: dict[str, Any],
    media_sha: dict[str, str],
) -> dict[str, Any]:
    target_video = str(target["video_id"])
    source_video = str(source["video_id"])
    sample = {
        "id": target["sample_id"],
        "video_path": str((MEDIA_ROOT / f"{target_video}.mp4").resolve()),
        "video_max_frames": 32,
        "scene_audio_path": str((MEDIA_ROOT / f"{source_video}.mp4").resolve()),
        "scene_audio_sample_rate": 16000,
        "scene_audio_window_sec": 1.0,
        "scene_audio_hop_sec": 1.0,
        "scene_audio_expected_duration_sec": float(source["duration_sec"]),
        "audio_sha256": media_sha[source_video],
        "physical_media_id": f"sha256:{media_sha[target_video]}",
        "source_dataset": "LongVALE_V3_PILOT_DIAGNOSTIC",
        "task_type": "seg_captioning",
        "sentence_id": int(target["event_index"]),
        "event_start_sec": float(target["event_start_sec"]),
        "event_end_sec": float(target["event_end_sec"]),
        "is_sparse": True,
        "selector_strategy": condition,
        "question": target["prompt"],
        "conversations": [
            {"from": "human", "value": f"<image>\n{target['prompt']}"},
            {"from": "gpt", "value": target["caption"]},
        ],
    }
    if condition == "fixed_all":
        sample["scene_audio_selection_policy"] = "fixed_all"
    elif condition == "oracle_top8":
        sample.update(
            scene_audio_selection_policy="oracle_topk",
            scene_audio_selection_top_k=TOP_K,
            scene_audio_selection_diagnostic=True,
            scene_audio_target_intervals=[[
                float(target["event_start_sec"]), float(target["event_end_sec"])
            ]],
        )
    elif condition.startswith("random_top8_seed_"):
        sample.update(
            scene_audio_selection_policy="random_topk",
            scene_audio_selection_top_k=TOP_K,
            scene_audio_selection_seed=int(condition.rsplit("_", 1)[-1]),
        )
    elif condition.startswith("oracle_mismatch_seed_"):
        sample.update(
            scene_audio_selection_policy="oracle_topk",
            scene_audio_selection_top_k=TOP_K,
            scene_audio_selection_diagnostic=True,
            scene_audio_target_intervals=mapped_interval(target, source),
            mismatch_source_id=f"longvale:video:{source_video}",
            mismatch_source_physical_media_id=f"sha256:{media_sha[source_video]}",
        )
    return sample


def main() -> None:
    args = parse_args()
    if args.output_dir is None:
        args.output_dir = DEFAULT_OUTPUT if args.scope == "canary20" else FULL_OUTPUT
    args.output_dir = args.output_dir.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    input_hashes = verify_stage_locks(args.scope)
    all_rows = read_jsonl(P1_MANIFEST)
    by_video = {str(row["video_id"]): row for row in all_rows}
    selected_rows = select_scope_rows(args.scope, all_rows)
    mappings = read_json(MISMATCH_MAPPINGS)
    audit_rows = read_json(MEDIA_AUDIT)["videos"]
    media_sha = {str(row["video_id"]): str(row["sha256"]) for row in audit_rows}
    if set(by_video) != set(media_sha):
        raise ValueError("P1 pilot 与 media audit video_id 不一致")

    mapping_signatures = set()
    for seed in CONTROL_SEEDS:
        mapping = mappings[str(seed)]
        pairs = tuple((row["video_id"], mapping[row["video_id"]]) for row in selected_rows)
        if any(target == source for target, source in pairs):
            raise ValueError(f"seed={seed} mismatch 出现物理自映射")
        signature = hashlib.sha256(json.dumps(pairs, sort_keys=True).encode()).hexdigest()
        if signature in mapping_signatures:
            raise ValueError("5-seed mismatch 在 canary 上出现重复 derangement")
        mapping_signatures.add(signature)

    relevant_videos = {str(row["video_id"]) for row in selected_rows}
    for seed in CONTROL_SEEDS:
        relevant_videos.update(mappings[str(seed)][row["video_id"]] for row in selected_rows)
    for video_id in sorted(relevant_videos):
        path = MEDIA_ROOT / f"{video_id}.mp4"
        if not path.is_file() or sha256_file(path) != media_sha[video_id]:
            raise ValueError(f"P3 relevant media SHA 漂移: {video_id}")

    conditions = condition_names()
    plan = []
    manifest_hashes = {}
    source_mapping_hashes = {}
    for condition in conditions:
        seed = int(condition.rsplit("_", 1)[-1]) if condition.startswith("oracle_mismatch_seed_") else None
        samples = []
        source_rows = []
        for target in selected_rows:
            source_video = mappings[str(seed)][target["video_id"]] if seed is not None else target["video_id"]
            source = by_video[source_video]
            samples.append(build_sample(condition, target, source, media_sha))
            source_rows.append({
                "sample_id": target["sample_id"],
                "target_video_id": target["video_id"],
                "source_video_id": source_video,
                "target_media_sha256": media_sha[target["video_id"]],
                "source_media_sha256": media_sha[source_video],
            })
        manifest = args.output_dir / "manifests" / f"{condition}.json"
        source_manifest = args.output_dir / "manifests" / f"{condition}_sources.jsonl"
        write_json(manifest, samples)
        write_jsonl(source_manifest, source_rows)
        manifest_hashes[condition] = sha256_file(manifest)
        source_mapping_hashes[condition] = sha256_file(source_manifest)
        env = active_audio_env()
        if condition == "behavior_off":
            env.update(AS_M4_ROLLBACK_MODE="behavior", AS_M4_ENABLE_SCENE_AUDIO="0", AS_M4_FORCE_AUDIO_GATE="0")
        plan.append({
            "id": condition,
            "dataset": "LongVALE_V3_PILOT_DIAGNOSTIC",
            "split": f"p3_{args.scope}",
            "manifest": str(manifest),
            "manifest_sha256": manifest_hashes[condition],
            "scorer": "exact_match",
            "model_path": str(MODEL_PATH),
            "audio_condition": "none" if condition == "behavior_off" else "correct",
            "alignment": "off",
            "gate_ablation": "none",
            "env": env,
            "output_jsonl": str(args.output_dir / "predictions" / f"{condition}.jsonl"),
        })
    write_jsonl(args.output_dir / "matrix_plan.jsonl", plan)

    annotation = {}
    for row in selected_rows:
        annotation[f"sha256:{media_sha[row['video_id']]}"] = {
            "duration": row["duration_sec"],
            "timestamps": [[row["event_start_sec"], row["event_end_sec"]]],
            "sentences": [row["caption"]],
        }
    annotation_name = "canary_annotation.json" if args.scope == "canary20" else "pilot_annotation.json"
    manifest_name = "canary_manifest.jsonl" if args.scope == "canary20" else "pilot_manifest.jsonl"
    lock_name = "p3_canary_lock.json" if args.scope == "canary20" else "p3_full_lock.json"
    write_json(args.output_dir / annotation_name, annotation)
    write_jsonl(args.output_dir / manifest_name, selected_rows)
    lock = {
        "artifact_kind": f"m4_ptpa_dynamic_selector_v3_p3_{args.scope}_lock",
        "format_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "locked_not_run",
        "p2_fix_commit": "a6ab234",
        "input_hashes": input_hashes,
        "scope": args.scope,
        "sample_namespace": CANARY_NAMESPACE if args.scope == "canary20" else "p1_frozen_one_sparse_event_all100",
        "sample_count": len(selected_rows),
        "sample_ids": [row["sample_id"] for row in selected_rows],
        "conditions": conditions,
        "condition_count": len(conditions),
        "control_seeds": list(CONTROL_SEEDS),
        "mismatch_derangement_signature_count": len(mapping_signatures),
        "top_k": TOP_K,
        "max_new_tokens": MAX_NEW_TOKENS,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "matrix_plan_sha256": sha256_file(args.output_dir / "matrix_plan.jsonl"),
        "manifest_hashes": manifest_hashes,
        "source_mapping_hashes": source_mapping_hashes,
        "relevant_media_count": len(relevant_videos),
        "full_execution_authorized_by_canary": args.scope == "frozen100",
        "final_oracle_gate_pending": args.scope == "frozen100",
        "p3_full_allowed": False,
        "selector_training_allowed": False,
        "confirm_accessed": False,
    }
    write_json(args.output_dir / lock_name, lock)
    print(json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
