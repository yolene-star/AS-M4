#!/usr/bin/env python3
"""运行 LLP modality manifest 的真实 M4 teacher-forced NLL；默认只做 CPU dry-run。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONTRACT = REPO_ROOT / "intersuit/harness/configs/llp_modality_evaluator_v1.json"
DEFAULT_MANIFEST = REPO_ROOT / "intersuit/harness/artifacts/llp_modality_evaluator_v1/manifest.jsonl"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def group_manifest(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["base_id"]].append(row)
    groups = []
    expected = {
        ("audio_presence", "correct_audio"),
        ("audio_presence", "mismatch_audio"),
        ("audio_presence", "video_only"),
        ("audio_presence", "silence"),
        ("visual_presence", "correct_audio"),
        ("visual_presence", "mismatch_audio"),
        ("visual_presence", "video_only"),
    }
    for base_id, items in sorted(grouped.items()):
        observed = {(item["query_type"], item["condition"]) for item in items}
        if observed != expected or len(items) != 7:
            raise ValueError(f"{base_id} 的 query/condition 不完整")
        invariant_fields = (
            "sample_id", "physical_media_id", "video_path", "window_start", "window_end",
            "modality_role", "event_label", "correct_audio_path", "mismatch_sample_id",
            "mismatch_audio_path", "mismatch_window_start", "mismatch_window_end",
        )
        for field in invariant_fields:
            if len({json.dumps(item[field], sort_keys=True) for item in items}) != 1:
                raise ValueError(f"{base_id} 组内字段不一致：{field}")
        groups.append({"base_id": base_id, "rows": sorted(items, key=lambda item: item["eval_id"])})
    return groups


def audio_request(row: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    condition = row["condition"]
    if condition == "video_only":
        return None, "original"
    if condition in {"correct_audio", "silence"}:
        path = row["correct_audio_path"]
        source_start = float(row["window_start"])
    elif condition == "mismatch_audio":
        path = row["mismatch_audio_path"]
        source_start = float(row["mismatch_window_start"])
    else:
        raise ValueError(f"未知 condition：{condition}")
    hop_sec = 0.5
    index = int(round(source_start / hop_sec))
    if abs(source_start - index * hop_sec) > 1e-9:
        raise ValueError("当前 smoke 只允许 0.5 秒对齐窗口")
    request = {
        "id": row["eval_id"],
        "scene_audio_path": path,
        "scene_audio_sample_rate": 16000,
        "scene_audio_window_sec": 1.0,
        "scene_audio_hop_sec": hop_sec,
        "scene_audio_selection_policy": "external_indices",
        "scene_audio_selected_indices": [index],
    }
    return request, "silence" if condition == "silence" else "original"


def dry_run(contract: dict[str, Any], rows: list[dict[str, Any]], limit_groups: int) -> dict[str, Any]:
    if contract["authorization"].get("cpu_real_runner_dry_run") is not True:
        raise PermissionError("CPU real runner dry-run 未授权")
    groups = group_manifest(rows)
    if not 1 <= limit_groups <= len(groups):
        raise ValueError("limit_groups 越界")
    selected = groups[:limit_groups]
    checked_paths = set()
    for group in selected:
        for row in group["rows"]:
            request, _ = audio_request(row)
            checked_paths.add(str(resolve_path(row["video_path"])))
            if request is not None:
                checked_paths.add(str(resolve_path(request["scene_audio_path"])))
    missing = sorted(path for path in checked_paths if not Path(path).is_file())
    if missing:
        raise ValueError(f"runner 输入缺失：{missing[:3]}")
    model_path = resolve_path(contract["model_contract"]["checkpoint"])
    if not model_path.is_dir():
        raise ValueError("模型 checkpoint 不存在")
    return {
        "status": "PASS_LLP_MODALITY_REAL_RUNNER_CPU_DRY_RUN_GPU_CLOSED",
        "selected_group_count": len(selected),
        "selected_row_count": sum(len(group["rows"]) for group in selected),
        "checked_path_count": len(checked_paths),
        "model_checkpoint": str(model_path),
        "gpu_used": False,
        "model_loaded": False,
    }


def run_gpu(
    contract: dict[str, Any], rows: list[dict[str, Any]], limit_groups: int, output_dir: Path
) -> dict[str, Any]:
    if contract["authorization"].get("gpu_model_effect_eval") is not True:
        raise PermissionError("LLP GPU model effect eval 尚未授权")
    smoke_scope = contract["authorization"].get("gpu_smoke_scope")
    if smoke_scope != {"physical_gpu": 2, "logical_device": "cuda:0", "limit_groups": 2, "roles": ["audio_only", "visual_only"]}:
        raise ValueError("GPU smoke 范围未冻结")
    if limit_groups != 2:
        raise ValueError("分层 GPU smoke 只允许 2 组")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"输出目录非空：{output_dir}")
    import torch
    from intersuit.harness.runners.run_predictions_from_plan import (
        _load_scene_audio_tensor,
        _load_video_tensor,
        frozen_runtime_module_sha256,
        load_model_once,
    )
    from intersuit.train.audio_semantic_m4_adapter import (
        build_m4_teacher_forced_example,
        m4_teacher_forced_forward,
    )

    device = contract["model_contract"]["device"]
    if device != "cuda:0":
        raise ValueError("只允许逻辑 cuda:0")
    all_groups = group_manifest(rows)
    selected_roles = {"audio_only", "visual_only"}
    groups_by_role: dict[str, list[dict[str, Any]]] = {role: [] for role in selected_roles}
    for group in all_groups:
        role = next(row for row in group["rows"])["modality_role"]
        if role in selected_roles:
            groups_by_role[role].append(group)
    groups = [groups_by_role[role][0] for role in ("audio_only", "visual_only") if groups_by_role[role]]
    if len(groups) != limit_groups:
        raise ValueError(f"角色分层 smoke 组数不匹配：{len(groups)}")
    tokenizer, model, image_processor, _ = load_model_once(
        contract["model_contract"]["checkpoint"], device
    )
    model.config.enable_scene_audio = True
    model.config.force_audio_gate = None
    model.config.as_m4_inference_simple_audio_gate = 0.005
    frozen_before = frozen_runtime_module_sha256(model)
    output_dir.mkdir(parents=True)
    started = time.perf_counter()
    result_rows = []
    torch.cuda.reset_peak_memory_stats(torch.device(device))
    with torch.inference_mode():
        for group in groups:
            video_cache: dict[str, Any] = {}
            for row in group["rows"]:
                input_ids, labels = build_m4_teacher_forced_example(
                    tokenizer, row["prompt"], row["target_answer"], device=device
                )
                video_path = row["video_path"]
                images = video_cache.get(video_path)
                if images is None:
                    images = _load_video_tensor(
                        video_path, image_processor, model, device,
                        max_frames=int(contract["model_contract"]["max_video_frames"]),
                    )
                    video_cache[video_path] = images
                request, debug_condition = audio_request(row)
                scene_audios = None
                scene_audio_timestamps = None
                if request is not None:
                    loaded, _source_timestamps, metadata = _load_scene_audio_tensor(
                        request, debug_condition=debug_condition
                    )
                    dtype = next(model.parameters()).dtype
                    scene_audios = loaded.to(device=device, dtype=dtype).unsqueeze(0)
                    target_start = float(row["window_start"])
                    target_end = float(row["window_end"])
                    scene_audio_timestamps = torch.tensor(
                        [[[target_start, target_end]]], dtype=torch.float32, device=device
                    )
                    if int(scene_audios.shape[1]) != 1:
                        raise RuntimeError("外部选窗后必须恰好保留 1 个音频窗口")
                outputs = m4_teacher_forced_forward(
                    model,
                    input_ids=input_ids,
                    labels=labels,
                    attention_mask=torch.ones_like(input_ids),
                    images=[images],
                    modalities=["video"],
                    scene_audios=scene_audios,
                    scene_audio_timestamps=scene_audio_timestamps,
                    use_cache=False,
                )
                result_rows.append(
                    {
                        "eval_id": row["eval_id"],
                        "base_id": row["base_id"],
                        "query_type": row["query_type"],
                        "condition": row["condition"],
                        "modality_role": row["modality_role"],
                        "target_answer": row["target_answer"],
                        "nll": float(outputs.loss.detach().float().item()),
                    }
                )
    frozen_after = frozen_runtime_module_sha256(model)
    if frozen_before != frozen_after:
        raise RuntimeError("LLP smoke 发生模型权重漂移")
    rows_path = output_dir / "rows.jsonl"
    rows_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in result_rows),
        encoding="utf-8",
    )
    summary = {
        "status": "PASS_LLP_MODALITY_TEACHER_FORCED_GPU_SMOKE",
        "group_count": len(groups),
        "row_count": len(result_rows),
        "runtime_seconds": time.perf_counter() - started,
        "cuda_peak_memory_allocated_bytes": int(torch.cuda.max_memory_allocated(torch.device(device))),
        "frozen_runtime_sha_before": frozen_before,
        "frozen_runtime_sha_after": frozen_after,
        "rows_sha256": sha256(rows_path),
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--limit-groups", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if os.environ.get("CONDA_DEFAULT_ENV") != "M4":
        raise RuntimeError("必须通过 conda run -n M4 启动")
    contract = read_json(args.contract)
    rows = read_jsonl(args.manifest)
    if args.dry_run:
        result = dry_run(contract, rows, args.limit_groups)
    else:
        if args.output_dir is None:
            raise ValueError("GPU run 必须提供 --output-dir")
        result = run_gpu(contract, rows, args.limit_groups, args.output_dir)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
