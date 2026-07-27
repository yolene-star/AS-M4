#!/usr/bin/env python3
"""为 UnAV-100 V1 validation 生成冻结 1 秒 BEATs cached Eventness 资产。"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import torch

from intersuit.harness.dynamic_selector_contract import (
    audit_micro_window_output,
    module_state_sha256,
)
from intersuit.harness.dynamic_selector_v2_localization import fixed_window_timestamps
from intersuit.model.streaming_av.dynamic_window_selector import DynamicWindowSelector


REPO_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = REPO_ROOT / "intersuit"
DEFAULT_BEATS_CHECKPOINT = INTERSUIT_ROOT / "checkpoints/BEATs_iter3_plus_AS2M.pt"
DEFAULT_BEATS_CODE_ROOT = REPO_ROOT / "third_party/OmniMMI/baselines/videollama2/model"
EXPECTED_BEATS_SHA256 = "d43cbfad4d7b56381c061d7a24774f908d4d94c72961f6eb1d9090ff18cd8d34"
SAMPLE_RATE = 16_000


def import_script_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


beats_tools = import_script_module(
    "dynamic_selector_v2_unav_beats_tools",
    INTERSUIT_ROOT / "scripts/prepare_avut_beats_features.py",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def decode_audio(path: Path, expected_duration: float) -> torch.Tensor:
    proc = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-f",
            "f32le",
            "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode:
        raise ValueError(
            f"audio decode failed for {path.name}: "
            f"{proc.stderr.decode(errors='replace')[-1000:]}"
        )
    waveform = torch.frombuffer(bytearray(proc.stdout), dtype=torch.float32).clone()
    expected_samples = int(round(expected_duration * SAMPLE_RATE))
    if waveform.numel() < expected_samples:
        waveform = torch.nn.functional.pad(
            waveform,
            (0, expected_samples - waveform.numel()),
        )
    return waveform[:expected_samples]


def split_windows(waveform: torch.Tensor, duration: float) -> tuple[torch.Tensor, torch.Tensor, list[int]]:
    timestamp_values = fixed_window_timestamps(duration)
    window_samples = SAMPLE_RATE
    windows = []
    valid_sample_counts = []
    for start, end in timestamp_values:
        left = int(round(start * SAMPLE_RATE))
        valid = min(window_samples, max(0, int(round(end * SAMPLE_RATE)) - left))
        window = waveform[left : left + valid]
        if window.numel() < window_samples:
            window = torch.nn.functional.pad(
                window,
                (0, window_samples - window.numel()),
            )
        windows.append(window)
        valid_sample_counts.append(valid)
    return (
        torch.stack(windows),
        torch.tensor(timestamp_values, dtype=torch.float32),
        valid_sample_counts,
    )


def validate_environment() -> dict[str, Any]:
    if os.environ.get("CONDA_DEFAULT_ENV") != "M4":
        raise RuntimeError("拒绝运行：必须通过 conda run -n M4 启动")
    visible = os.environ.get("CUDA_VISIBLE_DEVICES")
    if visible != "1":
        raise RuntimeError("Eventness 提取必须显式使用物理 GPU 1")
    if torch.cuda.device_count() != 1 or not torch.cuda.is_available():
        raise RuntimeError("Eventness 提取必须只看见一张可用 CUDA GPU")
    return {
        "conda_env": os.environ.get("CONDA_DEFAULT_ENV"),
        "python": sys.executable,
        "cuda_visible_devices": visible,
        "logical_device": "cuda:0",
        "device_name": torch.cuda.get_device_name(0),
        "torch_version": torch.__version__,
        "torch_cuda_version": torch.version.cuda,
    }


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allowlist", type=Path, required=True)
    parser.add_argument("--media-audit", type=Path, required=True)
    parser.add_argument("--media-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--beats-checkpoint", type=Path, default=DEFAULT_BEATS_CHECKPOINT)
    parser.add_argument("--beats-code-root", type=Path, default=DEFAULT_BEATS_CODE_ROOT)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    environment = validate_environment()
    checkpoint_sha256 = sha256(args.beats_checkpoint)
    if checkpoint_sha256 != EXPECTED_BEATS_SHA256:
        raise ValueError("BEATs checkpoint SHA does not match the V2 lock")
    allowlist = json.loads(args.allowlist.read_text(encoding="utf-8"))
    media_audit = json.loads(args.media_audit.read_text(encoding="utf-8"))
    if allowlist.get("artifact_kind") != "dynamic_selector_v2_v1_full_validation_media_allowlist":
        raise ValueError("invalid full-validation allowlist")
    if media_audit.get("status") not in {
        "PASS",
        "PASS_WITH_OFFICIAL_PACKAGING_DURATION_DIFFERENCES",
    }:
        raise ValueError("media audit did not pass")
    if media_audit.get("allowlist_sha256") != sha256(args.allowlist):
        raise ValueError("media audit/allowlist SHA mismatch")
    entries = list(allowlist["entries"])
    if args.limit is not None:
        if args.limit <= 0:
            raise ValueError("--limit must be positive")
        entries = entries[: args.limit]
    if not entries:
        raise ValueError("no validation media selected")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    feature_dir = args.output_dir / "features"
    if feature_dir.exists() and any(feature_dir.iterdir()):
        raise ValueError("feature output directory must be fresh")
    feature_dir.mkdir(parents=True, exist_ok=True)

    audit_by_video = {
        str(entry["video_id"]): entry
        for entry in media_audit["entries"]
    }
    encoder = beats_tools.BEATsWindowEncoder(
        checkpoint_path=args.beats_checkpoint,
        beats_code_root=args.beats_code_root,
        device="cuda:0",
    )
    manifest_rows = []
    for index, entry in enumerate(entries, start=1):
        video_id = str(entry["video_id"])
        media_row = audit_by_video[video_id]
        media_path = args.media_root / f"{video_id}.mp4"
        duration = float(entry["duration"])
        waveform = decode_audio(media_path, duration)
        windows, timestamps, valid_sample_counts = split_windows(waveform, duration)
        features = encoder.encode_windows(windows)
        if features.shape != (len(timestamps), int(features.shape[-1])):
            raise ValueError(f"BEATs feature shape mismatch: {video_id}")
        selector = DynamicWindowSelector(
            input_dim=int(features.shape[-1]),
            top_k=len(timestamps),
            causal=False,
            micro_window_sec=1.0,
        )
        state_sha_before = module_state_sha256(selector)
        mask = torch.ones(1, len(timestamps), dtype=torch.bool)
        source_indices = torch.arange(len(timestamps)).unsqueeze(0)
        with torch.inference_mode():
            output = selector(
                features.unsqueeze(0),
                timestamps.unsqueeze(0),
                audio_mask=mask,
                audio_windows=windows.unsqueeze(0),
                state=None,
                audio_source_indices=source_indices,
            )
        state_sha_after = module_state_sha256(selector)
        contract = audit_micro_window_output(
            output,
            features.unsqueeze(0),
            timestamps.unsqueeze(0),
            mask,
        )
        all_fixed_exact = (
            contract["status"] == "pass"
            and int(output.selection_mask.sum()) == len(timestamps)
            and torch.equal(output.selected_features[0], features)
            and torch.equal(output.selected_timestamps[0], timestamps)
            and torch.equal(output.selected_source_indices[0], torch.arange(len(timestamps)))
            and state_sha_before == state_sha_after
        )
        if not all_fixed_exact:
            raise ValueError(f"All does not exactly reproduce Fixed: {video_id}")
        feature_path = feature_dir / f"{video_id}.pt"
        torch.save(
            {
                "video_id": video_id,
                "audio_embedding": features,
                "timestamps": timestamps,
                "eventness_cached_scores": output.eventness_cached_scores[0].cpu(),
                "valid_sample_count": torch.tensor(valid_sample_counts, dtype=torch.long),
                "metadata": {
                    "official_split": "validation",
                    "media_sha256": media_row["media_sha256"],
                    "expected_duration": duration,
                    "official_mp4_duration": media_row["actual_duration"],
                    "checkpoint_sha256": checkpoint_sha256,
                    "selection_unit": "original_micro_window",
                    "micro_window_sec": 1.0,
                    "hop_sec": 1.0,
                    "causal_ranking": False,
                    "all_fixed_exact": True,
                    "selector_state_sha_before": state_sha_before,
                    "selector_state_sha_after": state_sha_after,
                    "test_accessed": False,
                },
            },
            feature_path,
        )
        payload = torch.load(feature_path, map_location="cpu", weights_only=True)
        if (
            payload["audio_embedding"].shape != features.shape
            or payload["timestamps"].shape != timestamps.shape
            or payload["eventness_cached_scores"].shape != timestamps.shape[:1]
            or not torch.isfinite(payload["audio_embedding"]).all()
            or not torch.isfinite(payload["eventness_cached_scores"]).all()
        ):
            raise ValueError(f"saved Eventness asset failed validation: {video_id}")
        manifest_rows.append(
            {
                "video_id": video_id,
                "feature_path": str(feature_path.resolve()),
                "feature_sha256": sha256(feature_path),
                "media_sha256": media_row["media_sha256"],
                "checkpoint_sha256": checkpoint_sha256,
                "window_count": len(timestamps),
                "embedding_dim": int(features.shape[-1]),
                "all_fixed_exact": True,
                "test_accessed": False,
            }
        )
        print(
            json.dumps(
                {
                    "progress": f"{index}/{len(entries)}",
                    "video_id": video_id,
                    "windows": len(timestamps),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )

    manifest_path = args.output_dir / "eventness_feature_manifest.jsonl"
    manifest_path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in manifest_rows
        ),
        encoding="utf-8",
    )
    summary = {
        "artifact_kind": "dynamic_selector_v2_v1_unav_eventness_features",
        "status": "PASS",
        "scope": "smoke" if args.limit is not None else "full_validation",
        "sample_count": len(entries),
        "allowlist_sha256": sha256(args.allowlist),
        "media_audit_sha256": sha256(args.media_audit),
        "checkpoint": {
            "path": str(args.beats_checkpoint.resolve()),
            "sha256": checkpoint_sha256,
        },
        "environment": environment,
        "selection_unit": "original_micro_window",
        "micro_window_sec": 1.0,
        "hop_sec": 1.0,
        "embedding_dims": sorted({row["embedding_dim"] for row in manifest_rows}),
        "all_fixed_exact_count": sum(row["all_fixed_exact"] for row in manifest_rows),
        "test_accessed": False,
        "saves_beats_compute": False,
        "manifest": {
            "path": str(manifest_path.resolve()),
            "sha256": sha256(manifest_path),
        },
    }
    write_json(args.output_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
