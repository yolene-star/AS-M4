#!/usr/bin/env python
"""提取 AVE D1 validation 冻结 BEATs 特征、cached Eventness 与 All=Fixed 审计。"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, asdict
from datetime import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from typing import Iterable

import torch

from intersuit.harness.ave_d1_protocol import (
    CLIP_DURATION_SEC,
    MICRO_WINDOW_SEC,
    WINDOW_COUNT,
    build_micro_window_timestamps,
)
from intersuit.harness.dynamic_selector_contract import (
    audit_micro_window_output,
    module_state_sha256,
)
from intersuit.model.streaming_av.dynamic_window_selector import DynamicWindowSelector


REPO_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = REPO_ROOT / "intersuit"
DEFAULT_BEATS_CHECKPOINT = INTERSUIT_ROOT / "checkpoints/BEATs_iter3_plus_AS2M.pt"
DEFAULT_BEATS_CODE_ROOT = (
    REPO_ROOT / "third_party/OmniMMI/baselines/videollama2/model"
)
EXPECTED_VALIDATION_COUNT = 391


def import_script_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


baseline = import_script_module(
    "ave_d1_validation_baseline_for_eventness",
    INTERSUIT_ROOT / "scripts/run_ave_official_d1_validation_baselines.py",
)
beats_tools = import_script_module(
    "ave_d1_validation_beats_tools",
    INTERSUIT_ROOT / "scripts/prepare_avut_beats_features.py",
)


@dataclass(frozen=True)
class EventnessFeatureRecord:
    youtube_id: str
    official_split: str
    media_path: str
    media_sha256: str
    feature_path: str
    feature_sha256: str
    checkpoint_sha256: str
    window_count: int
    embedding_dim: int
    all_fixed_exact: bool
    audit_status: str
    test_accessed: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidate-manifest",
        type=Path,
        default=Path(
            "intersuit/harness/artifacts/dynamic_selector_d1_ave_official/"
            "validation_candidate_manifest.jsonl"
        ),
    )
    parser.add_argument(
        "--media-inventory",
        type=Path,
        default=Path(
            "intersuit/harness/artifacts/dynamic_selector_d1_ave_official_media/"
            "validation_media_inventory.jsonl"
        ),
    )
    parser.add_argument(
        "--decode-preflight-summary",
        type=Path,
        default=Path(
            "intersuit/harness/artifacts/"
            "dynamic_selector_d1_ave_validation_media_preflight/summary.json"
        ),
    )
    parser.add_argument("--beats-checkpoint", type=Path, default=DEFAULT_BEATS_CHECKPOINT)
    parser.add_argument("--beats-code-root", type=Path, default=DEFAULT_BEATS_CODE_ROOT)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(
            "intersuit/harness/artifacts/"
            "dynamic_selector_d1_ave_validation_eventness"
        ),
    )
    parser.add_argument("--expected-count", type=int, default=EXPECTED_VALIDATION_COUNT)
    parser.add_argument(
        "--smoke-limit",
        type=int,
        default=None,
        help="仅用于独立输出目录的限量 smoke；正式运行不得设置。",
    )
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def current_process_environment(requested_device: str) -> dict:
    visible_count = torch.cuda.device_count()
    return {
        "requested_device": requested_device,
        "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
        "visible_cuda_device_count": visible_count,
        "visible_cuda_device_names": [
            torch.cuda.get_device_name(index) for index in range(visible_count)
        ],
        "torch_version": torch.__version__,
        "torch_cuda_version": torch.version.cuda,
        "python_executable": sys.executable,
        "conda_default_env": os.environ.get("CONDA_DEFAULT_ENV"),
    }


def assert_fresh_extraction_environment(environment: dict) -> None:
    requested_device = str(environment["requested_device"])
    if not requested_device.startswith("cuda"):
        return
    visible_devices = environment.get("cuda_visible_devices")
    if not visible_devices:
        raise ValueError("CUDA extraction 必须显式设置 CUDA_VISIBLE_DEVICES")
    if "," in visible_devices or int(environment["visible_cuda_device_count"]) != 1:
        raise ValueError("D1 Eventness extraction 必须显式映射且只可见一张 GPU")


def normalize_waveform(waveform: torch.Tensor) -> tuple[torch.Tensor, dict]:
    value = waveform.detach().cpu().float().flatten()
    expected_samples = int(round(CLIP_DURATION_SEC * baseline.SAMPLE_RATE))
    original_samples = value.numel()
    if original_samples < expected_samples:
        value = torch.nn.functional.pad(value, (0, expected_samples - original_samples))
    else:
        value = value[:expected_samples]
    windows = value.reshape(
        WINDOW_COUNT,
        int(round(MICRO_WINDOW_SEC * baseline.SAMPLE_RATE)),
    )
    return windows, {
        "original_sample_count": original_samples,
        "normalized_sample_count": value.numel(),
        "padded_sample_count": max(0, expected_samples - original_samples),
        "truncated_sample_count": max(0, original_samples - expected_samples),
    }


def validate_saved_feature(
    path: Path,
    *,
    youtube_id: str,
    media_sha256: str,
    checkpoint_sha256: str,
) -> dict:
    payload = torch.load(path, map_location="cpu", weights_only=True)
    required = {
        "youtube_id",
        "audio_embedding",
        "timestamps",
        "eventness_cached_scores",
        "metadata",
    }
    if required - set(payload):
        raise ValueError(f"eventness feature 缺字段：{path}")
    features = payload["audio_embedding"].float()
    timestamps = payload["timestamps"].float()
    scores = payload["eventness_cached_scores"].float()
    metadata = payload["metadata"]
    if (
        payload["youtube_id"] != youtube_id
        or metadata.get("media_sha256") != media_sha256
        or metadata.get("checkpoint_sha256") != checkpoint_sha256
    ):
        raise ValueError(f"eventness feature 身份或 SHA 不一致：{path}")
    if features.ndim != 2 or features.shape[0] != WINDOW_COUNT:
        raise ValueError(f"eventness feature 形状非法：{path}")
    if timestamps.shape != (WINDOW_COUNT, 2) or scores.shape != (WINDOW_COUNT,):
        raise ValueError(f"eventness timestamp/score 形状非法：{path}")
    if not (
        torch.isfinite(features).all()
        and torch.isfinite(timestamps).all()
        and torch.isfinite(scores).all()
    ):
        raise ValueError(f"eventness feature 含 NaN/Inf：{path}")
    if not torch.equal(timestamps, build_micro_window_timestamps()):
        raise ValueError(f"eventness timestamp 不是冻结 1 秒窗口：{path}")
    if metadata.get("all_fixed_exact") is not True:
        raise ValueError(f"eventness feature 未通过 All=Fixed：{path}")
    return payload


def extract_one(
    row: dict,
    *,
    encoder,
    checkpoint_sha256: str,
    output_dir: Path,
    official_split: str = "validation",
    test_accessed: bool = False,
) -> EventnessFeatureRecord:
    youtube_id = row["youtube_id"]
    waveform = baseline.decode_audio_mono_16k(Path(row["media_path"]))
    windows, waveform_audit = normalize_waveform(waveform)
    timestamps = build_micro_window_timestamps()
    features = encoder.encode_windows(windows)
    if features.shape[0] != WINDOW_COUNT or not torch.isfinite(features).all():
        raise ValueError(f"BEATs 输出窗口数或数值非法：{youtube_id}")
    selector = DynamicWindowSelector(
        input_dim=int(features.shape[-1]),
        top_k=WINDOW_COUNT,
        causal=False,
        micro_window_sec=MICRO_WINDOW_SEC,
    )
    selector_sha_before = module_state_sha256(selector)
    batched_features = features.unsqueeze(0)
    batched_timestamps = timestamps.unsqueeze(0)
    batched_mask = torch.ones(1, WINDOW_COUNT, dtype=torch.bool)
    batched_windows = windows.unsqueeze(0)
    batched_source_indices = torch.arange(WINDOW_COUNT).unsqueeze(0)
    with torch.inference_mode():
        output = selector(
            batched_features,
            batched_timestamps,
            batched_mask,
            batched_windows,
            audio_source_indices=batched_source_indices,
        )
    selector_sha_after = module_state_sha256(selector)
    audit = audit_micro_window_output(
        output,
        batched_features,
        batched_timestamps,
        batched_mask,
    )
    selected_count = int(output.selection_mask.sum().item())
    all_fixed_exact = (
        audit["status"] == "pass"
        and selected_count == WINDOW_COUNT
        and torch.equal(output.selected_features[0], features)
        and torch.equal(output.selected_timestamps[0], timestamps)
        and torch.equal(
            output.selected_source_indices[0],
            torch.arange(WINDOW_COUNT),
        )
        and selector_sha_before == selector_sha_after
    )
    if not all_fixed_exact:
        raise ValueError(f"All 未精确复现 Fixed：{youtube_id}")
    feature_dir = output_dir / "features"
    feature_dir.mkdir(parents=True, exist_ok=True)
    feature_path = feature_dir / f"{youtube_id}.pt"
    torch.save(
        {
            "youtube_id": youtube_id,
            "audio_embedding": features.cpu(),
            "timestamps": timestamps.cpu(),
            "eventness_cached_scores": output.eventness_cached_scores[0].cpu(),
            "metadata": {
                "official_split": official_split,
                "media_path": row["media_path"],
                "media_sha256": row["media_sha256"],
                "checkpoint_sha256": checkpoint_sha256,
                "encoder_name": encoder.encoder_name,
                "embedding_dim": int(features.shape[-1]),
                "selection_unit": "original_micro_window",
                "micro_window_sec": MICRO_WINDOW_SEC,
                "causal_ranking": False,
                "all_fixed_exact": True,
                "selector_state_sha_before": selector_sha_before,
                "selector_state_sha_after": selector_sha_after,
                "waveform_audit": waveform_audit,
                "test_accessed": test_accessed,
            },
        },
        feature_path,
    )
    payload = validate_saved_feature(
        feature_path,
        youtube_id=youtube_id,
        media_sha256=row["media_sha256"],
        checkpoint_sha256=checkpoint_sha256,
    )
    return EventnessFeatureRecord(
        youtube_id=youtube_id,
        official_split=official_split,
        media_path=row["media_path"],
        media_sha256=row["media_sha256"],
        feature_path=str(feature_path.resolve()),
        feature_sha256=file_sha256(feature_path),
        checkpoint_sha256=checkpoint_sha256,
        window_count=WINDOW_COUNT,
        embedding_dim=int(payload["audio_embedding"].shape[-1]),
        all_fixed_exact=True,
        audit_status=audit["status"],
        test_accessed=test_accessed,
    )


def load_existing_record(
    row: dict,
    *,
    checkpoint_sha256: str,
    output_dir: Path,
) -> EventnessFeatureRecord | None:
    feature_path = output_dir / "features" / f"{row['youtube_id']}.pt"
    if not feature_path.is_file():
        return None
    payload = validate_saved_feature(
        feature_path,
        youtube_id=row["youtube_id"],
        media_sha256=row["media_sha256"],
        checkpoint_sha256=checkpoint_sha256,
    )
    return EventnessFeatureRecord(
        youtube_id=row["youtube_id"],
        official_split="validation",
        media_path=row["media_path"],
        media_sha256=row["media_sha256"],
        feature_path=str(feature_path.resolve()),
        feature_sha256=file_sha256(feature_path),
        checkpoint_sha256=checkpoint_sha256,
        window_count=WINDOW_COUNT,
        embedding_dim=int(payload["audio_embedding"].shape[-1]),
        all_fixed_exact=True,
        audit_status="pass",
        test_accessed=False,
    )


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def serialize_jsonl(rows: Iterable[EventnessFeatureRecord]) -> str:
    return "".join(
        json.dumps(asdict(row), ensure_ascii=False, sort_keys=True) + "\n"
        for row in rows
    )


def write_jsonl(path: Path, rows: Iterable[EventnessFeatureRecord]) -> None:
    path.write_text(serialize_jsonl(rows), encoding="utf-8")


def validate_resume_only_source(
    output_dir: Path,
    *,
    manifest_text: str,
) -> dict:
    """确认 resume-only 只验证一个可追溯的完整 extraction 产物。"""

    summary_path = output_dir / "summary.json"
    manifest_path = output_dir / "validation_eventness_feature_manifest.jsonl"
    if not summary_path.is_file():
        raise ValueError("resume-only 缺少原始 extraction summary")
    if not manifest_path.is_file():
        raise ValueError("resume-only 缺少原始 extraction manifest")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if (
        summary.get("execution_mode") != "extraction"
        or int(summary.get("newly_extracted_count", 0)) <= 0
    ):
        raise ValueError("现有 summary 不能证明特征生成环境")
    expected_sha = sha256_text(manifest_text)
    recorded_sha = (
        summary.get("outputs", {})
        .get("manifest", {})
        .get("sha256")
    )
    if file_sha256(manifest_path) != expected_sha or recorded_sha != expected_sha:
        raise ValueError("resume-only 重建 manifest 与原始 extraction 不一致")
    return summary


def write_resume_validation_summary(
    output_dir: Path,
    *,
    source_summary: dict,
    current_process: dict,
    sample_count: int,
) -> dict:
    """追加 resume 校验记录，绝不覆盖原始 extraction summary。"""

    summary_path = output_dir / "summary.json"
    manifest_path = output_dir / "validation_eventness_feature_manifest.jsonl"
    audit = {
        "stage": "D1-AVE-validation-eventness-features-resume-audit",
        "status": "pass",
        "execution_mode": "resume_only",
        "finished_at": datetime.now().astimezone().isoformat(),
        "sample_count": sample_count,
        "newly_extracted_count": 0,
        "resumed_count": sample_count,
        "asset_generation_environment_source": "preserved_summary",
        "source_extraction_summary_sha256": file_sha256(summary_path),
        "source_manifest_sha256": file_sha256(manifest_path),
        "source_extraction_environment": source_summary.get(
            "asset_generation_environment"
        ),
        "current_process": current_process,
        "test_accessed": False,
    }
    write_json(output_dir / "resume_validation_summary.json", audit)
    return audit


def build_report(summary: dict) -> str:
    return f"""# Dynamic Window Selector D1 AVE validation cached Eventness 报告

## 结论

状态：**{summary["status"]}**。冻结 BEATs 只在 validation 上运行；test 未访问。

- validation：{summary["sample_count"]}
- 新提取：{summary["newly_extracted_count"]}
- resume：{summary["resumed_count"]}
- BEATs 维度：{summary["embedding_dims"]}
- 10 个原始 1 秒窗口：`true`
- All 精确复现 Fixed：{summary["all_fixed_exact_count"]}/{summary["sample_count"]}
- cached Eventness：只作为已编码特征上的 baseline，不宣称节省 BEATs
- test accessed：`false`

## 下一阶段边界

下一步只允许在冻结 validation 特征上计算 `eventness_cached_topk` 指标，并单独审计 Temporal scorer 是否具备可用的冻结定义。
"""


def run(args: argparse.Namespace) -> dict:
    started_at = datetime.now().astimezone()
    candidate_manifest = args.candidate_manifest.resolve()
    media_inventory = args.media_inventory.resolve()
    decode_preflight_summary = args.decode_preflight_summary.resolve()
    checkpoint_path = args.beats_checkpoint.resolve()
    code_root = args.beats_code_root.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    process_environment = current_process_environment(args.device)
    rows = baseline.load_validation_inputs(
        candidate_manifest,
        media_inventory,
        decode_preflight_summary,
        args.expected_count,
    )
    smoke_only = args.smoke_limit is not None
    if smoke_only:
        if args.smoke_limit <= 0:
            raise ValueError("--smoke-limit 必须为正整数")
        rows = rows[: args.smoke_limit]
    target_count = len(rows)
    checkpoint_sha256 = file_sha256(checkpoint_path)
    records = []
    pending = []
    for row in rows:
        existing = (
            load_existing_record(
                row,
                checkpoint_sha256=checkpoint_sha256,
                output_dir=output_dir,
            )
            if args.resume
            else None
        )
        if existing is None:
            pending.append(row)
        else:
            records.append(existing)
    if records and pending:
        raise ValueError(
            "partial resume 无法证明所有特征来自同一生成环境；"
            "请使用全新输出目录重新 extraction"
        )
    encoder = None
    if pending:
        assert_fresh_extraction_environment(process_environment)
        encoder = beats_tools.BEATsWindowEncoder(
            checkpoint_path=checkpoint_path,
            beats_code_root=code_root,
            device=args.device,
        )
        for row in pending:
            records.append(
                extract_one(
                    row,
                    encoder=encoder,
                    checkpoint_sha256=checkpoint_sha256,
                    output_dir=output_dir,
                )
            )
    records.sort(key=lambda row: row.youtube_id)
    if len(records) != target_count:
        raise ValueError("eventness validation feature 数量不完整")
    manifest_path = output_dir / "validation_eventness_feature_manifest.jsonl"
    manifest_text = serialize_jsonl(records)
    if args.resume and not pending:
        source_summary = validate_resume_only_source(
            output_dir,
            manifest_text=manifest_text,
        )
        audit = write_resume_validation_summary(
            output_dir,
            source_summary=source_summary,
            current_process=process_environment,
            sample_count=len(records),
        )
        print(
            json.dumps(
                {
                    "status": audit["status"],
                    "execution_mode": audit["execution_mode"],
                    "sample_count": len(records),
                    "newly_extracted_count": 0,
                    "resumed_count": len(records),
                },
                ensure_ascii=False,
            )
        )
        return audit

    manifest_path.write_text(manifest_text, encoding="utf-8")
    summary = {
        "stage": "D1-AVE-validation-eventness-features",
        "status": "pass",
        "execution_mode": "extraction",
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now().astimezone().isoformat(),
        "python_environment": "M4",
        "smoke_only": smoke_only,
        "expected_full_validation_count": args.expected_count,
        "device": args.device,
        "visible_cuda_device_count": torch.cuda.device_count(),
        "visible_cuda_device_name": (
            torch.cuda.get_device_name(0)
            if torch.cuda.is_available() and torch.cuda.device_count() == 1
            else None
        ),
        "asset_generation_environment": process_environment,
        "sample_count": len(records),
        "newly_extracted_count": len(pending),
        "resumed_count": len(records) - len(pending),
        "checkpoint": {
            "path": str(checkpoint_path),
            "sha256": checkpoint_sha256,
        },
        "embedding_dims": sorted({record.embedding_dim for record in records}),
        "all_fixed_exact_count": sum(record.all_fixed_exact for record in records),
        "audit_pass_count": sum(record.audit_status == "pass" for record in records),
        "selection_unit": "original_micro_window",
        "micro_window_sec": MICRO_WINDOW_SEC,
        "noncausal_full_clip_ranking": True,
        "saves_beats_compute": False,
        "inputs": {
            "candidate_manifest_sha256": file_sha256(candidate_manifest),
            "media_inventory_sha256": file_sha256(media_inventory),
            "decode_preflight_summary_sha256": file_sha256(
                decode_preflight_summary
            ),
        },
        "outputs": {
            "manifest": {
                "path": str(manifest_path.resolve()),
                "sha256": file_sha256(manifest_path),
            }
        },
        "test_accessed": False,
    }
    summary_path = output_dir / "summary.json"
    write_json(summary_path, summary)
    (output_dir / "report.md").write_text(build_report(summary), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": summary["status"],
                "sample_count": summary["sample_count"],
                "newly_extracted_count": summary["newly_extracted_count"],
                "resumed_count": summary["resumed_count"],
                "all_fixed_exact_count": summary["all_fixed_exact_count"],
            },
            ensure_ascii=False,
        )
    )
    return summary


def main() -> int:
    summary = run(parse_args())
    return 0 if summary["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
