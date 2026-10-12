#!/usr/bin/env python3
"""为锁定的 LLP pilot150 生成 16kHz 单声道 WAV 与冻结 BEATs 1s/1s 特征。

本脚本只执行用户授权的服务器内部非商业特征准备。它拒绝 test、训练、效果
访问和未在最终媒体清单中的文件；不会联网，也不会修改任何模型参数。
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import platform
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import torch
from scipy.io import wavfile


REPO_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = REPO_ROOT / "intersuit"
DEFAULT_MANIFEST = (
    INTERSUIT_ROOT
    / "datasets/LLP_PILOT_MEDIA_V2/final_media_v2/llp_pilot150_media_qualified_effect_locked.jsonl"
)
DEFAULT_LOCK = INTERSUIT_ROOT / "configs/m4_ptpa_audio_dataset_responsibility_v2.json"
DEFAULT_OUTPUT = INTERSUIT_ROOT / "datasets/LLP_PILOT_MEDIA_V2/s1_beats_1s_v1"
DEFAULT_BEATS = INTERSUIT_ROOT / "checkpoints/BEATs_iter3_plus_AS2M.pt"
DEFAULT_BEATS_CODE = REPO_ROOT / "third_party/OmniMMI/baselines/videollama2/model"
EXPECTED_MANIFEST_SHA256 = "5888f881af6462fc63e1dab504afe1a34fef0c9c5af46f0db7ab68e7d2d35135"
EXPECTED_BEATS_SHA256 = "d43cbfad4d7b56381c061d7a24774f908d4d94c72961f6eb1d9090ff18cd8d34"
SAMPLE_RATE = 16_000
WINDOW_SEC = 1.0
HOP_SEC = 1.0


def import_script_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


beats_tools = import_script_module(
    "llp_s1_beats_tools", INTERSUIT_ROOT / "scripts/prepare_avut_beats_features.py"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
            if limit is not None and len(rows) >= limit:
                break
    if not rows:
        raise ValueError(f"输入清单为空：{path}")
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def validate_scope(lock_path: Path) -> dict[str, Any]:
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    llp = lock["datasets"]["llp"]
    qualification = llp["pre_download_qualification"]
    review = qualification["official_protocol_downloader"]["license_review"]
    scoped = lock["permissions"].get(
        "llp_s1_internal_noncommercial_feature_preparation_allowed"
    )
    allowed = (
        scoped is True
        and qualification.get("feature_extraction_allowed") is True
        and review.get("feature_extraction_allowed") is True
        and review.get("training_allowed") is False
        and review.get("redistribution_allowed") is False
        and review.get("effect_access_allowed") is False
    )
    if not allowed:
        raise PermissionError("责任锁未授权 LLP S1 内部非商业特征准备，拒绝运行")
    authorization = review.get("user_scope_authorization", {})
    if authorization.get("scope") != "local_server_internal_noncommercial_research":
        raise PermissionError("LLP S1 用户授权 scope 缺失或漂移")
    scoped_gpu = lock["permissions"].get("llp_s1_single_gpu_feature_extraction_allowed")
    if authorization.get("gpu_feature_extraction_allowed") is not scoped_gpu:
        raise PermissionError("LLP S1 单卡授权在责任锁内不一致")
    return authorization


def validate_environment(device: str) -> dict[str, Any]:
    if os.environ.get("CONDA_DEFAULT_ENV") != "M4":
        raise RuntimeError("拒绝运行：必须通过 conda run -n M4 启动")
    result = {
        "conda_env": os.environ.get("CONDA_DEFAULT_ENV"),
        "python": sys.executable,
        "python_version": platform.python_version(),
        "torch_version": torch.__version__,
        "device": device,
    }
    if device == "cuda:0":
        if os.environ.get("CUDA_VISIBLE_DEVICES") != "2":
            raise RuntimeError("GPU 提取必须显式设置 CUDA_VISIBLE_DEVICES=2（物理 GPU 2）")
        if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
            raise RuntimeError("GPU 提取必须只看到一张可用 CUDA GPU")
        result["physical_gpu"] = 2
        result["device_name"] = torch.cuda.get_device_name(0)
    elif device != "cpu":
        raise ValueError("--device 只允许 cpu 或 cuda:0")
    return result


def validate_rows(rows: list[dict[str, Any]]) -> None:
    seen = set()
    for row in rows:
        sample_id = str(row.get("sample_id", ""))
        media_id = str(row.get("physical_media_id", ""))
        if not sample_id or not media_id or media_id in seen:
            raise ValueError(f"清单 sample/media id 缺失或重复：{sample_id}/{media_id}")
        seen.add(media_id)
        if row.get("official_split") not in {"train", "validation"}:
            raise ValueError(f"S1 特征准备拒绝非 train/validation：{sample_id}")
        if row.get("media_qualified") is not True:
            raise ValueError(f"媒体未通过资格审计：{sample_id}")
        if row.get("effect_access_allowed") is not False:
            raise ValueError(f"输入清单 effect lock 漂移：{sample_id}")
        expected_duration = float(row.get("end_sec", 0.0)) - float(row.get("start_sec", 0.0))
        if not math.isclose(expected_duration, 10.0, abs_tol=1e-6):
            raise ValueError(f"LLP 官方 10 秒片段合同漂移：{sample_id}={expected_duration}")
        media_path = REPO_ROOT / str(row["media_path"])
        if not media_path.is_file() or sha256(media_path) != row.get("media_sha256"):
            raise ValueError(f"媒体缺失或 SHA256 漂移：{sample_id}")


def decode_wav(media_path: Path, wav_path: Path, ffmpeg: str) -> None:
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = wav_path.with_suffix(".partial.wav")
    proc = subprocess.run(
        [
            ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-i",
            str(media_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-c:a",
            "pcm_s16le",
            str(temporary),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode:
        temporary.unlink(missing_ok=True)
        raise ValueError(
            f"音频解码失败 {media_path.name}: "
            f"{proc.stderr.decode(errors='replace')[-1000:]}"
        )
    temporary.replace(wav_path)


def load_wav(path: Path) -> torch.Tensor:
    rate, data = wavfile.read(path)
    if rate != SAMPLE_RATE or data.ndim != 1 or data.dtype != np.int16:
        raise ValueError(f"WAV 合同不匹配：{path} rate={rate} shape={data.shape} dtype={data.dtype}")
    waveform = torch.from_numpy(data.copy()).float() / 32768.0
    if waveform.numel() == 0 or not torch.isfinite(waveform).all():
        raise ValueError(f"WAV 为空或含 NaN/Inf：{path}")
    return waveform


def match_official_duration(waveform: torch.Tensor, duration_sec: float) -> torch.Tensor:
    """按 LLP 标注时长裁剪/补零，忽略 AAC 容器尾部采样偏差。"""

    expected = int(round(float(duration_sec) * SAMPLE_RATE))
    if expected <= 0:
        raise ValueError("官方片段时长必须为正数")
    if waveform.numel() >= expected:
        return waveform[:expected].contiguous()
    return torch.nn.functional.pad(waveform, (0, expected - waveform.numel()))


def extract_one(
    row: dict[str, Any], output: Path, encoder: Any, ffmpeg: str, resume: bool
) -> dict[str, Any]:
    media_id = str(row["physical_media_id"])
    media_path = REPO_ROOT / str(row["media_path"])
    wav_path = output / "audio_16k_mono" / f"{media_id}.wav"
    feature_path = output / "features" / f"{media_id}.pt"
    if not (resume and wav_path.is_file()):
        decode_wav(media_path, wav_path, ffmpeg)
    expected_duration = float(row["end_sec"]) - float(row["start_sec"])
    decoded_waveform = load_wav(wav_path)
    waveform = match_official_duration(decoded_waveform, expected_duration)
    if waveform.numel() != decoded_waveform.numel():
        pcm = (
            waveform.mul(32768.0)
            .round()
            .clamp(torch.iinfo(torch.int16).min, torch.iinfo(torch.int16).max)
            .to(torch.int16)
            .numpy()
        )
        wavfile.write(wav_path, SAMPLE_RATE, pcm)
    from intersuit.streaming.audio_stream import split_audio_windows, stack_audio_windows

    records = split_audio_windows(
        waveform,
        sample_rate=SAMPLE_RATE,
        window_sec=WINDOW_SEC,
        hop_sec=HOP_SEC,
        pad_short=True,
    )
    windows, timestamps = stack_audio_windows(records)
    beats_tools.validate_windows(windows, timestamps)
    valid = torch.tensor([item.valid_sample_count for item in records], dtype=torch.long)
    if resume and feature_path.is_file():
        payload = torch.load(feature_path, map_location="cpu", weights_only=True)
        embeddings = payload["audio_embedding"].float()
        if payload.get("media_sha256") != row["media_sha256"]:
            raise ValueError(f"恢复特征的媒体 SHA 不匹配：{media_id}")
        metadata = payload.get("metadata", {})
        if metadata.get("checkpoint_sha256") != encoder.checkpoint_sha256:
            raise ValueError(f"恢复特征的 BEATs checkpoint SHA 不匹配：{media_id}")
        if (metadata.get("window_sec"), metadata.get("hop_sec")) != (
            WINDOW_SEC,
            HOP_SEC,
        ):
            raise ValueError(f"恢复特征的 1s/1s 合同不匹配：{media_id}")
    else:
        embeddings = encoder.encode_windows(windows)
        feature_path.parent.mkdir(parents=True, exist_ok=True)
        torch.save(
            {
                "dataset": "LLP",
                "sample_id": row["sample_id"],
                "physical_media_id": media_id,
                "official_split": row["official_split"],
                "event_labels": row["event_labels"],
                "media_sha256": row["media_sha256"],
                "timestamps": timestamps.cpu(),
                "valid_sample_count": valid.cpu(),
                "audio_embedding": embeddings.cpu(),
                "metadata": {
                    "encoder_name": encoder.encoder_name,
                    "checkpoint_sha256": encoder.checkpoint_sha256,
                    "sample_rate": SAMPLE_RATE,
                    "window_sec": WINDOW_SEC,
                    "hop_sec": HOP_SEC,
                    "tail_padding": "right_zero_padding",
                    "frozen_encoder": True,
                    "training_performed": False,
                    "effect_accessed": False,
                },
            },
            feature_path,
        )
    if embeddings.ndim != 2 or embeddings.shape[0] != timestamps.shape[0]:
        raise ValueError(f"BEATs 特征形状不匹配：{media_id}")
    if valid.shape != (embeddings.shape[0],) or (valid <= 0).any():
        raise ValueError(f"有效采样数不匹配：{media_id}")
    if not torch.isfinite(embeddings).all():
        raise ValueError(f"BEATs 特征含 NaN/Inf：{media_id}")
    return {
        "dataset": "LLP",
        "sample_id": row["sample_id"],
        "physical_media_id": media_id,
        "official_split": row["official_split"],
        "event_labels": row["event_labels"],
        "media_path": str(media_path),
        "media_sha256": row["media_sha256"],
        "audio_path": str(wav_path),
        "audio_sha256": sha256(wav_path),
        "feature_path": str(feature_path),
        "feature_sha256": sha256(feature_path),
        "window_count": int(embeddings.shape[0]),
        "embedding_dim": int(embeddings.shape[1]),
        "valid_sample_counts": valid.tolist(),
        "sample_rate": SAMPLE_RATE,
        "window_sec": WINDOW_SEC,
        "hop_sec": HOP_SEC,
        "tail_padding": "right_zero_padding",
        "official_duration_sec": expected_duration,
        "feature_audio_samples": int(waveform.numel()),
        "effect_accessed": False,
        "training_performed": False,
    }


def build_review(summary: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# LLP S1 冻结 BEATs 特征准备报告",
            "",
            f"- 结论：`{summary['status']}`",
            f"- 样本：{summary['rows']} 条（train={summary['split_counts'].get('train', 0)}，validation={summary['split_counts'].get('validation', 0)}）。",
            f"- 特征：{summary['total_windows']} 个 1s/1s 窗口，维度 {summary['embedding_dims']}。",
            "- 音频：16kHz、单声道 PCM WAV；尾窗右补零并记录真实有效采样数。",
            "- BEATs：冻结、本地 checkpoint；未训练模型，未访问效果指标或正式 test。",
            "- 使用边界：仅服务器内部非商业研究；不得再分发媒体或派生资产。",
            "- 注意：逐条 YouTube license 仍未核验，本报告不构成机构或法律批准。",
            "",
        ]
    )


def run(args: argparse.Namespace, encoder: Any | None = None) -> dict[str, Any]:
    environment = validate_environment(args.device)
    authorization = validate_scope(args.responsibility_lock)
    if args.device == "cuda:0" and authorization.get("gpu_feature_extraction_allowed") is not True:
        raise PermissionError("用户 scope 未授权 LLP S1 单卡冻结特征提取")
    if sha256(args.manifest) != EXPECTED_MANIFEST_SHA256:
        raise ValueError("LLP final manifest SHA256 漂移")
    if sha256(args.beats_checkpoint) != EXPECTED_BEATS_SHA256:
        raise ValueError("BEATs checkpoint SHA256 漂移")
    rows = read_jsonl(args.manifest, args.limit)
    validate_rows(rows)
    args.output.mkdir(parents=True, exist_ok=True)
    ffmpeg_path = Path(shutil.which(args.ffmpeg) or args.ffmpeg).resolve()
    if not ffmpeg_path.is_file():
        raise FileNotFoundError(f"ffmpeg 不存在：{ffmpeg_path}")
    if encoder is None:
        encoder = beats_tools.BEATsWindowEncoder(
            checkpoint_path=args.beats_checkpoint,
            beats_code_root=args.beats_code_root,
            device=args.device,
        )
    manifest_rows = []
    for index, row in enumerate(rows, 1):
        manifest_rows.append(
            extract_one(row, args.output, encoder, str(ffmpeg_path), args.resume)
        )
        print(f"[{index}/{len(rows)}] {row['physical_media_id']} windows={manifest_rows[-1]['window_count']}", flush=True)
    output_manifest = args.output / "llp_s1_beats_feature_manifest.jsonl"
    write_jsonl(output_manifest, manifest_rows)
    summary = {
        "status": "PASS_FEATURE_PREPARATION_ONLY",
        "rows": len(manifest_rows),
        "unique_physical_media": len({row["physical_media_id"] for row in manifest_rows}),
        "split_counts": dict(sorted(Counter(row["official_split"] for row in manifest_rows).items())),
        "total_windows": sum(row["window_count"] for row in manifest_rows),
        "embedding_dims": sorted({row["embedding_dim"] for row in manifest_rows}),
        "all_finite": True,
        "all_16khz_mono": True,
        "window_sec": WINDOW_SEC,
        "hop_sec": HOP_SEC,
        "frozen_beats": True,
        "training_performed": False,
        "effect_accessed": False,
        "formal_test_accessed": False,
        "redistribution_allowed": False,
        "manifest_sha256": sha256(output_manifest),
    }
    write_json(args.output / "summary.json", summary)
    (args.output / "REVIEW_ZH.md").write_text(build_review(summary), encoding="utf-8")
    write_json(
        args.output / "run_config.json",
        {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "environment": environment,
            "authorization": authorization,
            "input_manifest": str(args.manifest.resolve()),
            "input_manifest_sha256": sha256(args.manifest),
            "beats_checkpoint": str(args.beats_checkpoint.resolve()),
            "beats_checkpoint_sha256": sha256(args.beats_checkpoint),
            "beats_code_root": str(args.beats_code_root.resolve()),
            "beats_source_sha256": sha256(
                args.beats_code_root.resolve() / "beats/BEATs.py"
            ),
            "implementation_sha256": sha256(Path(__file__).resolve()),
            "ffmpeg": str(ffmpeg_path),
            "ffmpeg_sha256": sha256(ffmpeg_path),
            "output": str(args.output.resolve()),
            "limit": args.limit,
            "resume": args.resume,
            "training_allowed": False,
            "effect_access_allowed": False,
        },
    )
    return {"summary": summary, "manifest": output_manifest}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--responsibility-lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--beats-checkpoint", type=Path, default=DEFAULT_BEATS)
    parser.add_argument("--beats-code-root", type=Path, default=DEFAULT_BEATS_CODE)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--device", choices=("cpu", "cuda:0"), default="cpu")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit 必须为正数")
    return args


if __name__ == "__main__":
    run(parse_args())
