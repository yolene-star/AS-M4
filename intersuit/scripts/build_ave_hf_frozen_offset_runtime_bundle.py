#!/usr/bin/env python
"""把冻结的 AVE offset scorer 封装成诊断或正式运行 bundle。

诊断模式允许保留未通过验收的历史 scorer，但产物会明确标记为
``diagnostic_only``。正式模式只接受所有冻结 seed 均已通过验收、且提供了
完整 schema manifest 的资产。两种模式都不训练、不调阈值，也不修改 Gate、
窗口或正式推理路径。
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any

import torch


REPO_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = REPO_ROOT / "intersuit"
DEFAULT_FROZEN_ROOT = INTERSUIT_ROOT / "harness/artifacts/ave_hf_temporal_offset_zero125_centerpeak_expanded_frozen"
DEFAULT_OUTPUT = DEFAULT_FROZEN_ROOT / "seed_20260719/temporal_offset_scorer_runtime_bundle.pt"
FROZEN_SEED = "20260719"
FROZEN_MARGIN = 0.15
FROZEN_SEEDS = ["20260718", "20260719", "20260720"]
FORMAL_PASS_DECISION = "temporal_offset_scorer_passed"
FORMAL_SCHEMA_REQUIRED_SECTIONS = {
    "window_schema": {
        "window_length_sec",
        "hop_sec",
        "window_count_policy",
        "tail_policy",
        "timestamp_semantics",
        "source_index_semantics",
    },
    "input_schema": {
        "audio_dim",
        "clip_dim",
        "rgb_dim",
        "scalar_dim",
        "audio_preprocessing",
        "clip_preprocessing",
        "rgb_preprocessing",
        "scalar_features",
    },
}


def import_temporal_module():
    path = INTERSUIT_ROOT / "scripts/train_ave_hf_temporal_offset_scorer.py"
    spec = importlib.util.spec_from_file_location("ave_hf_runtime_bundle_temporal", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


temporal = import_temporal_module()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_frozen_summary(summary: dict[str, Any]) -> list[str]:
    """校验冻结实验身份并返回每个 seed 的验收结论。"""

    config = summary["config"]
    if [str(seed) for seed in summary["seeds"]] != FROZEN_SEEDS:
        raise ValueError("冻结 seed 集合发生变化")
    if float(config["zero_class_weight"]) != 1.25 or not bool(config["require_center_peak"]):
        raise ValueError("冻结配置不再是 zero_weight=1.25 + center peak")

    runs = summary.get("runs")
    if not isinstance(runs, list) or len(runs) != len(FROZEN_SEEDS):
        raise ValueError("冻结 summary 缺少完整的三 seed 运行结论")
    run_names = [str(run.get("name", "")) for run in runs]
    expected_run_names = [f"seed_{seed}" for seed in FROZEN_SEEDS]
    if run_names != expected_run_names:
        raise ValueError("冻结 summary 的 seed 运行顺序或名称发生变化")
    decisions = [str(run.get("decision", "")) for run in runs]
    if any(not decision for decision in decisions):
        raise ValueError("冻结 summary 存在空验收结论")
    return decisions


def assert_formal_summary_eligible(decisions: list[str]) -> None:
    """正式构建必须 fail-closed：任一 seed 未通过就立即阻断。"""

    rejected = [
        decision for decision in decisions if decision != FORMAL_PASS_DECISION
    ]
    if rejected:
        raise ValueError(
            "formal runtime bundle not passed acceptance gate: "
            + ", ".join(rejected)
        )


def load_formal_schema_manifest(path: Path | None) -> dict[str, Any]:
    """读取并校验正式 bundle 所需的完整输入/窗口 schema。"""

    if path is None or not path.is_file():
        raise FileNotFoundError("formal schema manifest 不存在")
    manifest = load_json(path)
    for section, required_keys in FORMAL_SCHEMA_REQUIRED_SECTIONS.items():
        value = manifest.get(section)
        if not isinstance(value, dict):
            raise ValueError(f"formal schema manifest 缺少 {section}")
        missing = sorted(required_keys - set(value))
        if missing:
            raise ValueError(
                f"formal schema manifest 的 {section} 缺少字段: "
                + ", ".join(missing)
            )
    window_schema = manifest["window_schema"]
    for key in ("window_length_sec", "hop_sec"):
        value = window_schema[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) <= 0
        ):
            raise ValueError(f"formal schema manifest 的 {key} 必须为有限正数")
    for key in (
        "window_count_policy",
        "tail_policy",
        "timestamp_semantics",
        "source_index_semantics",
    ):
        if not isinstance(window_schema[key], str) or not window_schema[key].strip():
            raise ValueError(f"formal schema manifest 的 {key} 必须为非空字符串")

    input_schema = manifest["input_schema"]
    for key in ("audio_dim", "clip_dim", "rgb_dim", "scalar_dim"):
        value = input_schema[key]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"formal schema manifest 的 {key} 必须为正整数")
    for key in (
        "audio_preprocessing",
        "clip_preprocessing",
        "rgb_preprocessing",
    ):
        value = input_schema[key]
        if not isinstance(value, (str, dict)) or not value:
            raise ValueError(f"formal schema manifest 的 {key} 必须提供完整定义")
    scalar_features = input_schema["scalar_features"]
    if (
        not isinstance(scalar_features, list)
        or not scalar_features
        or any(not isinstance(value, str) or not value for value in scalar_features)
        or len(scalar_features) != len(set(scalar_features))
    ):
        raise ValueError(
            "formal schema manifest 的 scalar_features 必须为非空唯一字符串列表"
        )
    return manifest


def assert_formal_schema_matches_runtime(
    schema_manifest: dict[str, Any],
    *,
    runtime_dimensions: dict[str, int],
) -> None:
    """阻断 schema 声明与实际缓存/checkpoint 输入维度不一致的 formal bundle。"""

    declared = schema_manifest["input_schema"]
    for key in ("audio_dim", "clip_dim", "rgb_dim", "scalar_dim"):
        if int(declared[key]) != int(runtime_dimensions[key]):
            raise ValueError(
                f"formal schema manifest 的 {key}={declared[key]} "
                f"与 runtime {key}={runtime_dimensions[key]} 不一致"
            )


def build_filtered_caches(
    clip_manifest: Path,
    rgb_manifest: Path,
    ids: set[str],
) -> dict[str, dict[str, Any]]:
    clip_rows = [row for row in temporal.load_jsonl(clip_manifest) if str(row["youtube_id"]) in ids]
    rgb_rows = {
        str(row["youtube_id"]): row
        for row in temporal.load_jsonl(rgb_manifest)
        if str(row["youtube_id"]) in ids
    }
    if {str(row["youtube_id"]) for row in clip_rows} != ids or set(rgb_rows) != ids:
        raise ValueError("冻结训练记录与原始特征清单不完整或不一致")
    caches = [temporal.build_row_cache(row, rgb_rows[str(row["youtube_id"])]) for row in clip_rows]
    return {cache["youtube_id"]: cache for cache in caches}


def _build_runtime_bundle(
    frozen_root: Path,
    output_path: Path,
    *,
    mode: str,
    schema_manifest_path: Path | None = None,
) -> dict[str, Any]:
    if mode not in {"diagnostic", "formal"}:
        raise ValueError(f"未知 bundle mode: {mode}")

    summary_path = frozen_root / "frozen_seed_summary.json"
    summary = load_json(summary_path)
    decisions = validate_frozen_summary(summary)
    config = summary["config"]

    schema_manifest = None
    schema_manifest_sha256 = None
    if mode == "formal":
        assert_formal_summary_eligible(decisions)
        schema_manifest = load_formal_schema_manifest(schema_manifest_path)
        assert schema_manifest_path is not None
        schema_manifest_sha256 = sha256_file(schema_manifest_path)

    seed_root = frozen_root / f"seed_{FROZEN_SEED}"
    train_manifest = seed_root / "temporal_offset_train_manifest.jsonl"
    checkpoint_path = seed_root / "temporal_offset_scorer_one_epoch.pt"
    train_records = [temporal.OffsetRecord(**row) for row in load_jsonl(train_manifest)]
    train_ids = {record.youtube_id for record in train_records}
    cache_by_id = build_filtered_caches(
        (REPO_ROOT / config["clip_manifest"]).resolve(),
        (REPO_ROOT / config["rgb_manifest"]).resolve(),
        train_ids,
    )
    audio, video, scalars, _, _, scalar_stats = temporal.make_tensor_dataset(
        train_records,
        cache_by_id,
        context_radius=int(config["context_radius"]),
    )
    first_cache = next(iter(cache_by_id.values()))
    scalar_mean, scalar_std = scalar_stats
    runtime_dimensions = {
        "audio_dim": int(first_cache["audio"].shape[-1]),
        "clip_dim": int(first_cache["clip"].shape[-1]),
        "rgb_dim": int(first_cache["rgb"].shape[-1]),
        "scalar_dim": int(scalars.shape[-1]),
    }
    if schema_manifest is not None:
        assert_formal_schema_matches_runtime(
            schema_manifest,
            runtime_dimensions=runtime_dimensions,
        )
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    state = checkpoint["state_dict"]
    selected_run = next(
        (
            run
            for run in summary["runs"]
            if str(run.get("name")) == f"seed_{FROZEN_SEED}"
        ),
        None,
    )
    if selected_run is None:
        raise ValueError(f"冻结 summary 缺少 seed_{FROZEN_SEED} 运行结论")
    bundle = {
        "format_version": 1,
        "state_dict": state,
        "scalar_mean": scalar_mean.cpu(),
        "scalar_std": scalar_std.cpu(),
        "metadata": {
            "seed": int(FROZEN_SEED),
            "margin_threshold": FROZEN_MARGIN,
            "zero_class_weight": float(config["zero_class_weight"]),
            "require_center_peak": bool(config["require_center_peak"]),
            "context_radius": int(config["context_radius"]),
            "candidate_offsets": list(temporal.OFFSETS),
            "audio_dim": runtime_dimensions["audio_dim"],
            "clip_dim": runtime_dimensions["clip_dim"],
            "rgb_dim": runtime_dimensions["rgb_dim"],
            "scalar_dim": runtime_dimensions["scalar_dim"],
            "hidden_dim": int(config["hidden_dim"]),
            "checkpoint_sha256": sha256_file(checkpoint_path),
            "train_manifest_sha256": sha256_file(train_manifest),
            "frozen_summary_sha256": sha256_file(summary_path),
            "training_decisions": decisions,
            "source_decision": str(selected_run["decision"]),
            "bundle_mode": mode,
            "diagnostic_only": mode == "diagnostic",
            "formal_eligible": mode == "formal",
            "schema_manifest_sha256": schema_manifest_sha256,
            "moves_audio_window": False,
            "feeds_gate": False,
        },
    }
    if schema_manifest is not None:
        bundle["schema_manifest"] = schema_manifest
    output_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(bundle, output_path)
    reloaded = torch.load(output_path, map_location="cpu", weights_only=True)
    if reloaded["metadata"] != bundle["metadata"]:
        raise ValueError("runtime bundle 保存后元数据不一致")
    return {
        "output": str(output_path),
        "output_sha256": sha256_file(output_path),
        "checkpoint": str(checkpoint_path),
        "checkpoint_sha256": bundle["metadata"]["checkpoint_sha256"],
        "train_record_count": len(train_records),
        "scalar_dim": int(scalars.shape[-1]),
        "pair_dim": int(state["net.0.weight"].shape[1]),
        "margin_threshold": FROZEN_MARGIN,
        "bundle_mode": mode,
        "training_decisions": decisions,
    }


def build_diagnostic_bundle(frozen_root: Path, output_path: Path) -> dict[str, Any]:
    return _build_runtime_bundle(
        frozen_root,
        output_path,
        mode="diagnostic",
    )


def build_formal_bundle(
    frozen_root: Path,
    output_path: Path,
    *,
    schema_manifest_path: Path | None,
) -> dict[str, Any]:
    return _build_runtime_bundle(
        frozen_root,
        output_path,
        mode="formal",
        schema_manifest_path=schema_manifest_path,
    )


def build_bundle(frozen_root: Path, output_path: Path) -> dict[str, Any]:
    """兼容旧调用；旧入口只能生成明确标记的诊断 bundle。"""

    return build_diagnostic_bundle(frozen_root, output_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frozen-root", default=str(DEFAULT_FROZEN_ROOT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument(
        "--mode",
        choices=("diagnostic", "formal"),
        default="diagnostic",
    )
    parser.add_argument(
        "--schema-manifest",
        help="formal 模式必需；声明完整窗口和输入 schema 的 JSON 文件",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    frozen_root = Path(args.frozen_root).resolve()
    output_path = Path(args.output).resolve()
    if args.mode == "formal":
        schema_manifest_path = (
            Path(args.schema_manifest).resolve() if args.schema_manifest else None
        )
        result = build_formal_bundle(
            frozen_root,
            output_path,
            schema_manifest_path=schema_manifest_path,
        )
    else:
        result = build_diagnostic_bundle(frozen_root, output_path)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
