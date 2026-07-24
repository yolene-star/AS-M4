#!/usr/bin/env python
"""审计历史 Temporal bundle 是否有资格进入 Dynamic Selector 正式矩阵。"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Iterable

import torch


REPO_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = REPO_ROOT / "intersuit"
DEFAULT_FROZEN_ROOT = (
    INTERSUIT_ROOT
    / "harness/artifacts/ave_hf_temporal_offset_zero125_centerpeak_expanded_frozen"
)
DEFAULT_D1_EVENTNESS_ROOT = (
    INTERSUIT_ROOT / "harness/artifacts/dynamic_selector_d1_ave_validation_eventness"
)
DEFAULT_OUTPUT = (
    INTERSUIT_ROOT
    / "harness/artifacts/dynamic_selector_t0_temporal_asset_recovery/qualification"
)
AUDIT_VERSION = "dynamic-selector-asset-qualification-v1"
BUILDER_SOURCE_COMMIT = "cfadc46d5d961ac7284b2306e1af47b799e691c7"
FORMAL_PASS_DECISION = "temporal_offset_scorer_passed"
REQUIRED_INPUTS = {
    "audio_features": 768,
    "clip_features": 1024,
    "rgb_features": 768,
    "audio_rms": 1,
    "non_silent_ratio": 1,
}
D1_WINDOW_SCHEMA = {
    "window_length_sec": 1.0,
    "hop_sec": 1.0,
    "window_count": 10,
    "first_start_sec": 0.0,
    "last_end_sec": 10.0,
    "overlapping": False,
    "tail_policy": "crop_or_pad_to_10_seconds",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bundle",
        type=Path,
        default=(
            DEFAULT_FROZEN_ROOT
            / "seed_20260719/temporal_offset_scorer_runtime_bundle.pt"
        ),
    )
    parser.add_argument(
        "--frozen-summary",
        type=Path,
        default=DEFAULT_FROZEN_ROOT / "frozen_seed_summary.json",
    )
    parser.add_argument(
        "--historical-feature-manifest",
        type=Path,
        default=(
            INTERSUIT_ROOT
            / "harness/artifacts/ave_hf_expanded_clip_window_features/"
            "ave_hf_clip_window_feature_manifest.jsonl"
        ),
    )
    parser.add_argument(
        "--d1-eventness-summary",
        type=Path,
        default=DEFAULT_D1_EVENTNESS_ROOT / "summary.json",
    )
    parser.add_argument(
        "--d1-eventness-manifest",
        type=Path,
        default=(
            DEFAULT_D1_EVENTNESS_ROOT
            / "validation_eventness_feature_manifest.jsonl"
        ),
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict]:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows:
        raise ValueError(f"manifest 为空：{path}")
    return rows


def infer_window_schema(timestamps: torch.Tensor, *, tail_policy: str) -> dict:
    value = timestamps.detach().cpu().float()
    if value.ndim != 2 or value.shape[1] != 2 or value.shape[0] == 0:
        raise ValueError("timestamps 必须是非空 [N,2]")
    if not torch.isfinite(value).all():
        raise ValueError("timestamps 含 NaN/Inf")
    durations = value[:, 1] - value[:, 0]
    if (durations <= 0).any():
        raise ValueError("timestamps 含非正窗口")
    hops = value[1:, 0] - value[:-1, 0]
    window_length = float(durations[0].item())
    if not torch.allclose(
        durations,
        torch.full_like(durations, window_length),
        atol=1e-6,
        rtol=0.0,
    ):
        raise ValueError("窗口长度不恒定")
    hop = window_length if hops.numel() == 0 else float(hops[0].item())
    if hops.numel() and not torch.allclose(
        hops,
        torch.full_like(hops, hop),
        atol=1e-6,
        rtol=0.0,
    ):
        raise ValueError("窗口 hop 不恒定")
    return {
        "window_length_sec": window_length,
        "hop_sec": hop,
        "window_count": int(value.shape[0]),
        "first_start_sec": float(value[0, 0].item()),
        "last_end_sec": float(value[-1, 1].item()),
        "overlapping": hop < window_length,
        "tail_policy": tail_policy,
    }


def build_historical_window_schema(
    rows: list[dict],
    observed_timestamps: torch.Tensor,
) -> tuple[dict, bool]:
    observed = infer_window_schema(
        observed_timestamps,
        tail_policy="preserve_decoded_duration",
    )
    count_distribution = Counter(int(row.get("window_count", -1)) for row in rows)
    end_distribution = Counter(float(row.get("window_end", float("nan"))) for row in rows)
    consistent = all(
        int(row.get("window_count", -1)) in {19, 20}
        and float(row.get("window_start", float("nan"))) == 0.0
        and abs(
            float(row.get("window_end", float("nan")))
            - (0.5 * (int(row.get("window_count", -1)) - 1) + 1.0)
        )
        <= 1e-6
        for row in rows
    )
    return {
        "window_length_sec": observed["window_length_sec"],
        "hop_sec": observed["hop_sec"],
        "window_count_policy": "19_or_20_by_media_duration",
        "observed_window_count_distribution": {
            str(key): count_distribution[key] for key in sorted(count_distribution)
        },
        "first_start_sec": 0.0,
        "last_end_policy": "10.0_or_10.5_by_media_duration",
        "observed_last_end_distribution": {
            str(key): end_distribution[key] for key in sorted(end_distribution)
        },
        "overlapping": observed["overlapping"],
        "tail_policy": "preserve_decoded_duration",
    }, consistent


def classify_qualification(
    *,
    decisions: Iterable[str],
    diagnostic_only: bool,
    source_verified: bool,
    input_dimensions_match: bool,
    historical_schema: dict,
    target_schema: dict,
    available_inputs: set[str],
    required_inputs: set[str],
) -> list[str]:
    statuses = []
    decision_values = list(decisions)
    if (
        not decision_values
        or any(value != FORMAL_PASS_DECISION for value in decision_values)
        or diagnostic_only
    ):
        statuses.append("diagnostic_failed")
    if historical_schema != target_schema or not input_dimensions_match:
        statuses.append("schema_incompatible")
    if not required_inputs <= available_inputs:
        statuses.append("missing_inputs")
    if not source_verified:
        statuses.append("corrupt_or_unverifiable")
    return statuses or ["qualified"]


def inspect_d1_available_inputs(feature_path: Path) -> tuple[set[str], dict]:
    payload = torch.load(feature_path, map_location="cpu", weights_only=True)
    available = set()
    if torch.is_tensor(payload.get("audio_embedding")):
        available.add("audio_features")
    if torch.is_tensor(payload.get("clip_features")):
        available.add("clip_features")
    if torch.is_tensor(payload.get("rgb_features")):
        available.add("rgb_features")
    if torch.is_tensor(payload.get("audio_rms")):
        available.add("audio_rms")
    if torch.is_tensor(payload.get("non_silent_ratio")):
        available.add("non_silent_ratio")
    timestamps = payload.get("timestamps")
    if not torch.is_tensor(timestamps):
        raise ValueError("D1 feature 缺少 timestamps")
    schema = infer_window_schema(
        timestamps,
        tail_policy="crop_or_pad_to_10_seconds",
    )
    return available, schema


def build_qualification(args: argparse.Namespace) -> tuple[dict, dict]:
    bundle_path = args.bundle.resolve()
    summary_path = args.frozen_summary.resolve()
    historical_manifest_path = args.historical_feature_manifest.resolve()
    d1_summary_path = args.d1_eventness_summary.resolve()
    d1_manifest_path = args.d1_eventness_manifest.resolve()
    bundle = torch.load(bundle_path, map_location="cpu", weights_only=True)
    metadata = bundle.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError("Temporal bundle 缺少 metadata")
    summary = load_json(summary_path)
    decisions = [str(row.get("decision")) for row in summary.get("runs", [])]
    summary_sha = file_sha256(summary_path)
    source_summary_verified = (
        metadata.get("frozen_summary_sha256") == summary_sha
    )
    bundle_input_dimensions = {
        "audio_features": int(metadata.get("audio_dim", -1)),
        "clip_features": int(metadata.get("clip_dim", -1)),
        "rgb_features": int(metadata.get("rgb_dim", -1)),
        "scalar_features": int(metadata.get("scalar_dim", -1)),
    }
    input_dimensions_match = bundle_input_dimensions == {
        "audio_features": 768,
        "clip_features": 1024,
        "rgb_features": 768,
        "scalar_features": 37,
    }
    context_count = int(metadata.get("context_radius", -1)) * 2 + 1
    expected_pair_dim = (
        context_count * bundle_input_dimensions["audio_features"] * 2
        + context_count
        * (
            bundle_input_dimensions["clip_features"]
            + bundle_input_dimensions["rgb_features"]
        )
        * 2
        + bundle_input_dimensions["scalar_features"]
    )
    first_weight = bundle.get("state_dict", {}).get("net.0.weight")
    bundle_structure_verified = (
        torch.is_tensor(first_weight)
        and tuple(first_weight.shape)
        == (int(metadata.get("hidden_dim", -1)), expected_pair_dim)
    )

    historical_rows = load_jsonl(historical_manifest_path)
    historical_first = historical_rows[0]
    historical_audio = torch.load(
        Path(historical_first["audio_feature_path"]),
        map_location="cpu",
        weights_only=True,
    )
    historical_schema, historical_manifest_schema_consistent = (
        build_historical_window_schema(
            historical_rows,
            historical_audio["timestamps"],
        )
    )

    d1_summary = load_json(d1_summary_path)
    d1_rows = load_jsonl(d1_manifest_path)
    if (
        d1_summary.get("status") != "pass"
        or d1_summary.get("smoke_only") is not False
        or d1_summary.get("sample_count") != len(d1_rows)
        or d1_summary.get("test_accessed") is not False
        or any(row.get("test_accessed") is not False for row in d1_rows)
        or any(
            int(row.get("window_count", -1)) != 10
            or int(row.get("embedding_dim", -1)) != 768
            for row in d1_rows
        )
    ):
        raise ValueError("D1 Eventness 正式 validation 资产未通过或访问了 test")
    available_inputs, observed_d1_schema = inspect_d1_available_inputs(
        Path(d1_rows[0]["feature_path"])
    )
    if observed_d1_schema != D1_WINDOW_SCHEMA:
        raise ValueError("D1 Eventness observed schema 与冻结 schema 不一致")

    historical_ids = {str(row["youtube_id"]) for row in historical_rows}
    d1_ids = {str(row["youtube_id"]) for row in d1_rows}
    overlap = sorted(historical_ids & d1_ids)
    required_inputs = set(REQUIRED_INPUTS)
    statuses = classify_qualification(
        decisions=decisions,
        diagnostic_only=bool(metadata.get("diagnostic_only", False)),
        source_verified=(
            source_summary_verified
            and bundle_structure_verified
            and historical_manifest_schema_consistent
        ),
        input_dimensions_match=input_dimensions_match,
        historical_schema=historical_schema,
        target_schema=D1_WINDOW_SCHEMA,
        available_inputs=available_inputs,
        required_inputs=required_inputs,
    )
    formal_eligible = statuses == ["qualified"]
    bundle_sha = file_sha256(bundle_path)
    asset_id = (
        "ave_hf_temporal_offset_seed20260719_"
        f"{'formal' if formal_eligible else 'diagnostic'}_{bundle_sha[:12]}"
    )
    missing_inputs = sorted(required_inputs - available_inputs)
    qualification = {
        "audit_version": AUDIT_VERSION,
        "asset_id": asset_id,
        "asset_type": "temporal_offset_scorer_runtime_bundle",
        "intended_strategy": "sync_margin_cached_topk",
        "python_environment": "M4",
        "python_executable": sys.executable,
        "formal_eligible": formal_eligible,
        "qualification_statuses": statuses,
        "source": {
            "bundle_path": str(bundle_path),
            "bundle_sha256": bundle_sha,
            "builder_source_commit": BUILDER_SOURCE_COMMIT,
            "frozen_summary_path": str(summary_path),
            "frozen_summary_sha256": summary_sha,
            "bundle_recorded_frozen_summary_sha256": metadata.get(
                "frozen_summary_sha256"
            ),
            "source_summary_verified": source_summary_verified,
            "bundle_structure_verified": bundle_structure_verified,
            "historical_manifest_schema_consistent": (
                historical_manifest_schema_consistent
            ),
            "training_decisions": decisions,
            "diagnostic_only": bool(metadata.get("diagnostic_only", False)),
            "checkpoint_sha256": metadata.get("checkpoint_sha256"),
        },
        "schema": {
            "historical": historical_schema,
            "target_d1": D1_WINDOW_SCHEMA,
            "historical_schema_sha256": canonical_sha256(historical_schema),
            "target_schema_sha256": canonical_sha256(D1_WINDOW_SCHEMA),
            "exact_match": historical_schema == D1_WINDOW_SCHEMA,
        },
        "inputs": {
            "required": REQUIRED_INPUTS,
            "bundle_input_dimensions": bundle_input_dimensions,
            "input_dimensions_match_expected": input_dimensions_match,
            "available_in_d1_cache": sorted(available_inputs),
            "missing_in_d1_cache": missing_inputs,
        },
        "data_boundary": {
            "historical_feature_video_count": len(historical_ids),
            "d1_validation_video_count": len(d1_ids),
            "physical_video_overlap_count": len(overlap),
            "physical_video_overlap_ids": overlap,
            "d1_test_accessed": False,
        },
        "allowed_uses": (
            ["formal_matrix"]
            if formal_eligible
            else ["legacy_ave_hf_code_regression", "offline_error_analysis"]
        ),
        "forbidden_stages": (
            [] if formal_eligible else ["D1", "D2", "S1"]
        ),
    }
    registry = {
        "registry_version": 1,
        "assets": [
            {
                "asset_id": asset_id,
                "asset_type": qualification["asset_type"],
                "intended_strategy": qualification["intended_strategy"],
                "purpose": "frozen temporal offset/sync margin diagnostics",
                "source_commit": BUILDER_SOURCE_COMMIT,
                "bundle_sha256": bundle_sha,
                "checkpoint_sha256": metadata.get("checkpoint_sha256"),
                "training_decision": decisions,
                "data_id_sha256": canonical_sha256(sorted(historical_ids)),
                "schema_sha256": qualification["schema"][
                    "historical_schema_sha256"
                ],
                "qualification_statuses": statuses,
                "formal_eligible": formal_eligible,
                "allowed_uses": qualification["allowed_uses"],
                "allowed_stages": qualification["allowed_uses"],
                "forbidden_stages": qualification["forbidden_stages"],
            }
        ],
    }
    return qualification, registry


def build_report(qualification: dict) -> str:
    schema = qualification["schema"]
    inputs = qualification["inputs"]
    source = qualification["source"]
    boundary = qualification["data_boundary"]
    return f"""# Dynamic Selector T0 历史 Temporal 资产资格审计

## 结论

- asset：`{qualification["asset_id"]}`
- formal eligible：`{str(qualification["formal_eligible"]).lower()}`
- qualification：`{qualification["qualification_statuses"]}`
- 允许用途：`{qualification["allowed_uses"]}`
- 禁止阶段：`{qualification["forbidden_stages"]}`

## 证据

- 三 seed decision：`{source["training_decisions"]}`
- diagnostic only：`{str(source["diagnostic_only"]).lower()}`
- summary SHA 与 bundle metadata 一致：
  `{str(source["source_summary_verified"]).lower()}`
- bundle 首层权重形状与 metadata 一致：
  `{str(source["bundle_structure_verified"]).lower()}`
- 历史 feature manifest schema 一致：
  `{str(source["historical_manifest_schema_consistent"]).lower()}`
- 历史窗口 schema：`{schema["historical"]}`
- D1 目标窗口 schema：`{schema["target_d1"]}`
- schema exact match：`{str(schema["exact_match"]).lower()}`
- D1 cache 已有输入：`{inputs["available_in_d1_cache"]}`
- D1 cache 缺失输入：`{inputs["missing_in_d1_cache"]}`
- bundle 输入维度符合冻结预期：
  `{str(inputs["input_dimensions_match_expected"]).lower()}`
- 历史/D1 validation 物理视频交集：
  `{boundary["physical_video_overlap_count"]}`
- test accessed：`false`

## 处理

该 bundle 只能保留为旧 AVE_HF 代码回归和离线错误分析资产。正式入口必须
fail-closed；D1 核心矩阵将其记录为 `omitted_ineligible_asset`，不等待 T0，
也不运行 `sync_margin_cached_topk`。

## 下一步

给 runtime bundle builder 增加 acceptance decision 失败测试，并拆分
diagnostic/formal builder。下一步只修资产门禁，不训练新 scorer。
"""


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def run(args: argparse.Namespace) -> dict:
    qualification, registry = build_qualification(args)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    qualification_path = output_dir / "asset_qualification.json"
    registry_path = output_dir / "asset_registry.json"
    report_path = output_dir / "report.md"
    write_json(qualification_path, qualification)
    write_json(registry_path, registry)
    report_path.write_text(build_report(qualification), encoding="utf-8")
    result = {
        "status": (
            "qualified"
            if qualification["formal_eligible"]
            else "omitted_ineligible_asset"
        ),
        "asset_id": qualification["asset_id"],
        "qualification_statuses": qualification["qualification_statuses"],
        "formal_eligible": qualification["formal_eligible"],
        "outputs": {
            "asset_qualification": {
                "path": str(qualification_path),
                "sha256": file_sha256(qualification_path),
            },
            "asset_registry": {
                "path": str(registry_path),
                "sha256": file_sha256(registry_path),
            },
            "report": {
                "path": str(report_path),
                "sha256": file_sha256(report_path),
            },
        },
        "test_accessed": False,
    }
    write_json(output_dir / "summary.json", result)
    print(json.dumps(result, ensure_ascii=False))
    return result


def main() -> int:
    result = run(parse_args())
    return 0 if result["status"] in {"qualified", "omitted_ineligible_asset"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
