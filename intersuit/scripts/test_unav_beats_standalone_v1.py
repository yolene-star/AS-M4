#!/usr/bin/env python3
"""为 UnAV S1-R 提取冻结 BEATs 1s/1s raw 特征；不训练、不接入 M4。"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import torch


ROOT = Path(__file__).resolve().parents[2]
INTERSUIT = ROOT / "intersuit"
DEFAULT_CONTRACT = INTERSUIT / "configs/m4_ptpa_unav_s1r_beats_feature_v1.json"
DEFAULT_MANIFEST = INTERSUIT / "datasets/UNAV_S1_V1/full_train_validation_media_v1/qualified_media_manifest.jsonl"
DEFAULT_MEDIA_ROOT = INTERSUIT / "datasets/UNAV_S1_V1/full_train_validation_media_v1/media"
DEFAULT_OUTPUT = INTERSUIT / "harness/artifacts/unav_s1r_beats_features_v1"
DEFAULT_BEATS = INTERSUIT / "checkpoints/BEATs_iter3_plus_AS2M.pt"
DEFAULT_BEATS_CODE = ROOT / "third_party/OmniMMI/baselines/videollama2/model"
EXPECTED_BEATS_SHA256 = "d43cbfad4d7b56381c061d7a24774f908d4d94c72961f6eb1d9090ff18cd8d34"
SAMPLE_RATE = 16_000


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def import_beats_tools():
    path = INTERSUIT / "scripts/prepare_avut_beats_features.py"
    spec = importlib.util.spec_from_file_location("unav_s1r_beats_tools", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def validate_contract(contract_path: Path, manifest_path: Path) -> dict[str, Any]:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if contract.get("status") != "AUTHORIZED_FROZEN_BEATS_FEATURE_EXTRACTION_EFFECT_LOCKED":
        raise PermissionError("UnAV BEATs 特征合同未授权")
    permissions = contract.get("permissions", {})
    if permissions.get("feature_extraction_allowed") is not True:
        raise PermissionError("UnAV BEATs 特征提取未授权")
    for key in ("effect_access_allowed", "test_access_allowed", "training_allowed", "redistribution_allowed"):
        if permissions.get(key) is not False:
            raise PermissionError(f"禁止项未 fail-closed：{key}")
    expected = contract["qualified_manifest"]
    if sha256(manifest_path) != expected["sha256"]:
        raise ValueError("qualified manifest SHA 漂移")
    return contract


def validate_environment() -> dict[str, Any]:
    if os.environ.get("CONDA_DEFAULT_ENV") != "M4":
        raise RuntimeError("必须使用 Conda M4")
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "2":
        raise RuntimeError("必须显式使用物理 GPU 2")
    if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
        raise RuntimeError("必须只看到一张可用 GPU")
    return {
        "conda_env": "M4",
        "python": sys.executable,
        "cuda_visible_devices": "2",
        "logical_device": "cuda:0",
        "device_name": torch.cuda.get_device_name(0),
        "torch_version": torch.__version__,
        "torch_cuda_version": torch.version.cuda,
    }


def decode_audio(ffmpeg: str, media_path: Path, duration_sec: float) -> torch.Tensor:
    process = subprocess.run(
        [ffmpeg, "-nostdin", "-v", "error", "-i", str(media_path), "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-t", str(duration_sec), "-f", "f32le", "-"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if process.returncode:
        raise ValueError(f"音频解码失败：{media_path.name}：{process.stderr.decode(errors='replace')[-500:]}")
    waveform = torch.frombuffer(bytearray(process.stdout), dtype=torch.float32).clone()
    expected_samples = int(round(duration_sec * SAMPLE_RATE))
    if waveform.numel() < expected_samples:
        waveform = torch.nn.functional.pad(waveform, (0, expected_samples - waveform.numel()))
    return waveform[:expected_samples]


def split_windows(waveform: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    window_count = max(1, math.ceil(waveform.numel() / SAMPLE_RATE))
    windows, timestamps, valid_counts = [], [], []
    for index in range(window_count):
        start = index * SAMPLE_RATE
        valid = min(SAMPLE_RATE, max(0, waveform.numel() - start))
        window = waveform[start : start + valid]
        windows.append(torch.nn.functional.pad(window, (0, SAMPLE_RATE - valid)))
        timestamps.append((float(index), float(index) + valid / SAMPLE_RATE))
        valid_counts.append(valid)
    return torch.stack(windows), torch.tensor(timestamps, dtype=torch.float32), torch.tensor(valid_counts, dtype=torch.long)


def validate_existing(path: Path, row: dict[str, Any]) -> dict[str, Any]:
    payload = torch.load(path, map_location="cpu", weights_only=False)
    features = payload["audio_embedding"]
    if payload.get("video_id") != row["physical_media_id"] or payload.get("media_sha256") != row["sha256"]:
        raise ValueError(f"resume 媒体身份漂移：{path.name}")
    if payload.get("checkpoint_sha256") != EXPECTED_BEATS_SHA256:
        raise ValueError(f"resume checkpoint 漂移：{path.name}")
    if features.ndim != 2 or features.shape[1] != 768 or not torch.isfinite(features).all():
        raise ValueError(f"resume 特征合同失败：{path.name}")
    return {"video_id": row["physical_media_id"], "official_split": row["official_split"], "window_count": int(features.shape[0]), "embedding_dim": 768, "feature_path": str(path.resolve()), "feature_sha256": sha256(path), "resumed": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--media-root", type=Path, default=DEFAULT_MEDIA_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--beats-checkpoint", type=Path, default=DEFAULT_BEATS)
    parser.add_argument("--beats-code-root", type=Path, default=DEFAULT_BEATS_CODE)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    contract = validate_contract(args.contract, args.manifest)
    environment = validate_environment()
    if sha256(args.beats_checkpoint) != EXPECTED_BEATS_SHA256:
        raise ValueError("BEATs checkpoint SHA 漂移")
    rows = read_jsonl(args.manifest)
    if len(rows) != int(contract["qualified_manifest"]["rows"]):
        raise ValueError("qualified manifest 行数漂移")
    if args.limit is not None:
        if args.limit <= 0:
            raise ValueError("--limit 必须为正数")
        rows = rows[: args.limit]

    feature_dir = args.output / "features"
    feature_dir.mkdir(parents=True, exist_ok=True)
    tools = import_beats_tools()
    encoder = tools.BEATsWindowEncoder(args.beats_checkpoint, args.beats_code_root, device="cuda:0")
    if encoder.model.training or any(parameter.requires_grad for parameter in encoder.model.parameters()):
        raise RuntimeError("BEATs 未保持冻结 eval 状态")

    manifest_rows = []
    for index, row in enumerate(rows, start=1):
        video_id = str(row["physical_media_id"])
        feature_path = feature_dir / f"{video_id}.pt"
        if args.resume and feature_path.is_file():
            manifest_rows.append(validate_existing(feature_path, row))
        else:
            media_path = Path(row["path"])
            if media_path.parent != args.media_root.resolve() or sha256(media_path) != row["sha256"]:
                raise ValueError(f"媒体路径或 SHA 漂移：{video_id}")
            waveform = decode_audio(args.ffmpeg, media_path, float(row["metadata_duration_sec"]))
            windows, timestamps, valid_counts = split_windows(waveform)
            features = encoder.encode_windows(windows).cpu()
            if features.shape != (len(windows), 768) or not torch.isfinite(features).all():
                raise ValueError(f"BEATs 特征合同失败：{video_id}")
            payload = {
                "video_id": video_id,
                "official_split": row["official_split"],
                "event_labels": row["event_labels"],
                "events": row["events"],
                "duration_sec": float(row["metadata_duration_sec"]),
                "media_sha256": row["sha256"],
                "audio_embedding": features,
                "timestamps": timestamps,
                "valid_sample_count": valid_counts,
                "checkpoint_sha256": EXPECTED_BEATS_SHA256,
                "frozen_beats": True,
                "training_performed": False,
                "effect_accessed": False,
                "test_accessed": False,
            }
            temporary = feature_path.with_suffix(".pt.tmp")
            torch.save(payload, temporary)
            temporary.replace(feature_path)
            manifest_rows.append({"video_id": video_id, "official_split": row["official_split"], "window_count": len(windows), "embedding_dim": 768, "feature_path": str(feature_path.resolve()), "feature_sha256": sha256(feature_path), "resumed": False})
        if index % 100 == 0 or index == len(rows):
            print(json.dumps({"completed": index, "total": len(rows), "video_id": video_id}, ensure_ascii=False), flush=True)

    manifest_path = args.output / "feature_manifest.jsonl"
    temporary = manifest_path.with_suffix(".jsonl.tmp")
    temporary.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in manifest_rows), encoding="utf-8")
    temporary.replace(manifest_path)
    summary = {
        "status": "PASS_FROZEN_BEATS_FEATURE_EXTRACTION",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "rows": len(manifest_rows),
        "split_counts": {split: sum(row["official_split"] == split for row in manifest_rows) for split in ("train", "validation")},
        "total_windows": sum(row["window_count"] for row in manifest_rows),
        "embedding_dim": 768,
        "manifest_path": str(manifest_path.resolve()),
        "manifest_sha256": sha256(manifest_path),
        "qualified_manifest_sha256": sha256(args.manifest),
        "checkpoint_sha256": EXPECTED_BEATS_SHA256,
        "environment": environment,
        "frozen_beats": True,
        "training_performed": False,
        "effect_accessed": False,
        "test_accessed": False,
    }
    write_json(args.output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
