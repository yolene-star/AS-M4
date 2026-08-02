#!/usr/bin/env python3
"""为 LongVALE V2 oracle_pilot 生成冻结 Energy/Onset/Eventness 窗口分数。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import numpy as np
import torch

from intersuit.harness.dynamic_selector_contract import module_state_sha256
from intersuit.harness.dynamic_selector_v2_audio import energy_scores, onset_scores
from intersuit.harness.dynamic_selector_v2_localization import fixed_window_timestamps
from intersuit.model.streaming_av.dynamic_window_selector import DynamicWindowSelector


PROJECT_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = PROJECT_ROOT / "intersuit"
DEFAULT_MANIFEST = INTERSUIT_ROOT / "harness/artifacts/dynamic_selector_v2_v2/preflight/oracle_pilot.jsonl"
DEFAULT_PREFLIGHT = INTERSUIT_ROOT / "harness/artifacts/dynamic_selector_v2_v2/preflight/preflight_lock.json"
DEFAULT_MEDIA_AUDIT = INTERSUIT_ROOT / "harness/artifacts/dynamic_selector_v2_v2/preflight/oracle_pilot_media_audit.json"
DEFAULT_MEDIA_ROOT = INTERSUIT_ROOT / "datasets/LongVALE_V2/oracle_pilot_media"
DEFAULT_OUTPUT = INTERSUIT_ROOT / "harness/artifacts/dynamic_selector_v2_v2/pilot_scores"
DEFAULT_BEATS_CHECKPOINT = INTERSUIT_ROOT / "checkpoints/BEATs_iter3_plus_AS2M.pt"
DEFAULT_BEATS_CODE_ROOT = PROJECT_ROOT / "third_party/OmniMMI/baselines/videollama2/model"
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
    "longvale_v2_beats_tools",
    INTERSUIT_ROOT / "scripts/prepare_avut_beats_features.py",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--preflight-lock", type=Path, default=DEFAULT_PREFLIGHT)
    parser.add_argument("--media-audit", type=Path, default=DEFAULT_MEDIA_AUDIT)
    parser.add_argument("--media-root", type=Path, default=DEFAULT_MEDIA_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--beats-checkpoint", type=Path, default=DEFAULT_BEATS_CHECKPOINT)
    parser.add_argument("--beats-code-root", type=Path, default=DEFAULT_BEATS_CODE_ROOT)
    parser.add_argument("--limit-videos", type=int)
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def validate_environment() -> dict[str, Any]:
    if os.environ.get("CONDA_DEFAULT_ENV") != "M4":
        raise RuntimeError("拒绝运行：必须通过 conda run -n M4 启动")
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "1":
        raise RuntimeError("pilot score 提取必须显式使用物理 GPU 1")
    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise RuntimeError("pilot score 提取必须只看见一张可用 CUDA GPU")
    return {
        "conda_env": os.environ.get("CONDA_DEFAULT_ENV"),
        "python": sys.executable,
        "cuda_visible_devices": "1",
        "logical_device": "cuda:0",
        "device_name": torch.cuda.get_device_name(0),
        "torch_version": torch.__version__,
        "torch_cuda_version": torch.version.cuda,
    }


def decode_audio(path: Path, expected_duration: float) -> np.ndarray:
    process = subprocess.run(
        [
            "ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-vn",
            "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if process.returncode:
        raise ValueError(
            f"audio decode failed for {path.name}: "
            f"{process.stderr.decode(errors='replace')[-1000:]}"
        )
    waveform = np.frombuffer(process.stdout, dtype="<f4").copy()
    expected_samples = int(round(expected_duration * SAMPLE_RATE))
    if len(waveform) < expected_samples:
        waveform = np.pad(waveform, (0, expected_samples - len(waveform)))
    waveform = waveform[:expected_samples]
    if not len(waveform) or not np.isfinite(waveform).all():
        raise ValueError(f"decoded waveform invalid: {path.name}")
    return waveform


def stack_windows(waveform: np.ndarray, timestamps: list[list[float]]) -> tuple[torch.Tensor, list[int]]:
    values = torch.from_numpy(waveform)
    windows = []
    valid_counts = []
    for start, end in timestamps:
        left = int(round(start * SAMPLE_RATE))
        valid = min(SAMPLE_RATE, max(0, int(round(end * SAMPLE_RATE)) - left))
        window = values[left : left + valid]
        if len(window) < SAMPLE_RATE:
            window = torch.nn.functional.pad(window, (0, SAMPLE_RATE - len(window)))
        windows.append(window)
        valid_counts.append(valid)
    return torch.stack(windows), valid_counts


def load_existing(path: Path, *, video_id: str, media_sha256: str) -> dict | None:
    if not path.is_file():
        return None
    try:
        payload = torch.load(path, map_location="cpu", weights_only=True)
    except Exception:
        return None
    required = {"video_id", "timestamps", "energy_scores", "onset_scores", "eventness_cached_scores", "metadata"}
    if required - set(payload):
        return None
    count = int(payload["timestamps"].shape[0])
    if (
        payload["video_id"] != video_id
        or payload["metadata"].get("media_sha256") != media_sha256
        or payload["metadata"].get("beats_checkpoint_sha256") != EXPECTED_BEATS_SHA256
        or any(tuple(payload[key].shape) != (count,) for key in ("energy_scores", "onset_scores", "eventness_cached_scores"))
        or not all(torch.isfinite(payload[key]).all() for key in ("timestamps", "energy_scores", "onset_scores", "eventness_cached_scores"))
    ):
        return None
    return payload


def main() -> None:
    args = parse_args()
    environment = validate_environment()
    if sha256(args.beats_checkpoint) != EXPECTED_BEATS_SHA256:
        raise ValueError("BEATs checkpoint SHA 漂移")
    preflight = json.loads(args.preflight_lock.read_text(encoding="utf-8"))
    media_audit = json.loads(args.media_audit.read_text(encoding="utf-8"))
    if preflight.get("status") != "pass" or preflight["permissions"].get("oracle_pilot") is not True:
        raise ValueError("LongVALE oracle_pilot preflight 未授权")
    if media_audit.get("status") != "pass" or media_audit.get("audited_video_count") != 100:
        raise ValueError("LongVALE oracle_pilot media audit 未通过")
    if media_audit.get("manifest_sha256") != sha256(args.manifest):
        raise ValueError("media audit 与 pilot manifest SHA 不一致")

    rows = [json.loads(line) for line in args.manifest.read_text(encoding="utf-8").splitlines() if line]
    duration_by_video = {}
    for row in rows:
        duration_by_video[str(row["video_id"])] = float(row["duration_sec"])
    video_ids = sorted(duration_by_video)
    if len(video_ids) != 100:
        raise ValueError(f"oracle_pilot 应包含 100 个物理视频，实际 {len(video_ids)}")
    if args.limit_videos is not None:
        if args.limit_videos <= 0:
            raise ValueError("--limit-videos 必须为正数")
        video_ids = video_ids[: args.limit_videos]
    audit_by_video = {row["video_id"]: row for row in media_audit["videos"]}

    args.output_dir.mkdir(parents=True, exist_ok=True)
    feature_dir = args.output_dir / "features"
    feature_dir.mkdir(parents=True, exist_ok=True)
    if any(feature_dir.iterdir()) and not args.resume:
        raise ValueError("score feature 目录非空；必须显式使用 --resume")
    run_log = args.output_dir / "run.log"
    run_log.write_text(
        json.dumps(
            {
                "started_at_utc": datetime.now(timezone.utc).isoformat(),
                "environment": environment,
                "limit_videos": args.limit_videos,
                "resume": args.resume,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    encoder = beats_tools.BEATsWindowEncoder(
        checkpoint_path=args.beats_checkpoint,
        beats_code_root=args.beats_code_root,
        device="cuda:0",
    )
    manifest_rows = []
    for index, video_id in enumerate(video_ids, start=1):
        media_path = args.media_root / f"{video_id}.mp4"
        media_sha256 = audit_by_video[video_id]["sha256"]
        if sha256(media_path) != media_sha256:
            raise ValueError(f"pilot media SHA 漂移：{video_id}")
        feature_path = feature_dir / f"{video_id}.pt"
        payload = load_existing(feature_path, video_id=video_id, media_sha256=media_sha256) if args.resume else None
        status = "reused"
        if payload is None:
            status = "computed"
            duration = duration_by_video[video_id]
            timestamps = fixed_window_timestamps(duration)
            waveform = decode_audio(media_path, duration)
            windows, valid_counts = stack_windows(waveform, timestamps)
            embeddings = encoder.encode_windows(windows)
            timestamp_tensor = torch.tensor(timestamps, dtype=torch.float32)
            selector = DynamicWindowSelector(
                input_dim=int(embeddings.shape[-1]),
                top_k=len(timestamps),
                causal=False,
                micro_window_sec=1.0,
            )
            state_before = module_state_sha256(selector)
            with torch.inference_mode():
                output = selector(
                    embeddings.unsqueeze(0),
                    timestamp_tensor.unsqueeze(0),
                    audio_mask=torch.ones(1, len(timestamps), dtype=torch.bool),
                    audio_windows=windows.unsqueeze(0),
                    audio_source_indices=torch.arange(len(timestamps)).unsqueeze(0),
                )
            state_after = module_state_sha256(selector)
            if state_before != state_after:
                raise ValueError("cached Eventness selector 参数发生变化")
            payload = {
                "video_id": video_id,
                "timestamps": timestamp_tensor,
                "energy_scores": torch.tensor(energy_scores(waveform, SAMPLE_RATE, timestamps)),
                "onset_scores": torch.tensor(onset_scores(waveform, SAMPLE_RATE, timestamps)),
                "eventness_cached_scores": output.eventness_cached_scores[0].cpu(),
                "valid_sample_count": torch.tensor(valid_counts, dtype=torch.long),
                "metadata": {
                    "media_sha256": media_sha256,
                    "duration_sec": duration,
                    "beats_checkpoint_sha256": EXPECTED_BEATS_SHA256,
                    "embedding_dim": int(embeddings.shape[-1]),
                    "window_sec": 1.0,
                    "hop_sec": 1.0,
                    "pad_short": True,
                    "selector_state_sha_before": state_before,
                    "selector_state_sha_after": state_after,
                },
            }
            temporary = feature_path.with_suffix(".pt.tmp")
            torch.save(payload, temporary)
            temporary.replace(feature_path)
            if load_existing(feature_path, video_id=video_id, media_sha256=media_sha256) is None:
                raise ValueError(f"保存的 score feature 未通过复读校验：{video_id}")
        manifest_rows.append(
            {
                "video_id": video_id,
                "feature_path": str(feature_path.resolve()),
                "feature_sha256": sha256(feature_path),
                "media_sha256": media_sha256,
                "window_count": int(payload["timestamps"].shape[0]),
            }
        )
        message = json.dumps(
            {"progress": f"{index}/{len(video_ids)}", "video_id": video_id, "status": status},
            ensure_ascii=False,
        )
        print(message, flush=True)
        with run_log.open("a", encoding="utf-8") as handle:
            handle.write(message + "\n")

    score_manifest = args.output_dir / "score_manifest.jsonl"
    score_manifest.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in manifest_rows),
        encoding="utf-8",
    )
    summary = {
        "artifact_kind": "dynamic_selector_v2_longvale_pilot_scores",
        "status": "pass",
        "scope": "smoke" if args.limit_videos is not None else "full_pilot",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "video_count": len(video_ids),
        "manifest_sha256": sha256(args.manifest),
        "media_audit_sha256": sha256(args.media_audit),
        "preflight_lock_sha256": sha256(args.preflight_lock),
        "beats_checkpoint_sha256": EXPECTED_BEATS_SHA256,
        "environment": environment,
        "score_manifest_sha256": sha256(score_manifest),
        "total_window_count": sum(row["window_count"] for row in manifest_rows),
        "optimizer_created": False,
        "temporal_scorer": "not_qualified",
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
