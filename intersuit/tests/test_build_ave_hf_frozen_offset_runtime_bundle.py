"""AVE_HF frozen offset runtime bundle 的 formal/diagnostic 门禁测试。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/build_ave_hf_frozen_offset_runtime_bundle.py"
)
spec = importlib.util.spec_from_file_location(
    "build_ave_hf_frozen_offset_runtime_bundle_tested",
    SCRIPT,
)
builder = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = builder
assert spec.loader is not None
spec.loader.exec_module(builder)


def write_summary(root: Path, decisions: list[str]) -> None:
    root.mkdir(parents=True)
    (root / "frozen_seed_summary.json").write_text(
        json.dumps(
            {
                "seeds": [20260718, 20260719, 20260720],
                "config": {
                    "zero_class_weight": 1.25,
                    "require_center_peak": True,
                },
                "runs": [
                    {"name": f"seed_{seed}", "decision": decision}
                    for seed, decision in zip(
                        (20260718, 20260719, 20260720),
                        decisions,
                        strict=True,
                    )
                ],
            }
        ),
        encoding="utf-8",
    )


def test_formal_bundle_rejects_not_passed_before_creating_output(tmp_path):
    frozen_root = tmp_path / "frozen"
    output = tmp_path / "formal.pt"
    write_summary(
        frozen_root,
        ["temporal_offset_scorer_not_passed"] * 3,
    )

    with pytest.raises(ValueError, match="not passed"):
        builder.build_formal_bundle(
            frozen_root,
            output,
            schema_manifest_path=tmp_path / "schema.json",
        )

    assert not output.exists()


def test_formal_schema_manifest_requires_complete_window_and_input_schema(tmp_path):
    manifest = tmp_path / "schema.json"
    manifest.write_text(
        json.dumps(
            {
                "window_schema": {"window_length_sec": 1.0},
                "input_schema": {"audio_dim": 768},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="window_schema.*缺少字段"):
        builder.load_formal_schema_manifest(manifest)


def test_formal_schema_manifest_accepts_complete_schema(tmp_path):
    manifest = tmp_path / "schema.json"
    payload = {
        "window_schema": {
            "window_length_sec": 1.0,
            "hop_sec": 1.0,
            "window_count_policy": "ten_non_overlapping",
            "tail_policy": "clip_at_duration",
            "timestamp_semantics": "half_open_seconds",
            "source_index_semantics": "zero_based_micro_window",
        },
        "input_schema": {
            "audio_dim": 768,
            "clip_dim": 1024,
            "rgb_dim": 768,
            "scalar_dim": 37,
            "audio_preprocessing": "frozen",
            "clip_preprocessing": "frozen",
            "rgb_preprocessing": "frozen",
            "scalar_features": ["audio_rms", "audio_non_silent"],
        },
    }
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    assert builder.load_formal_schema_manifest(manifest) == payload


def test_formal_schema_manifest_rejects_invalid_values(tmp_path):
    manifest = tmp_path / "schema.json"
    payload = {
        "window_schema": {
            "window_length_sec": 0.0,
            "hop_sec": 1.0,
            "window_count_policy": "ten_non_overlapping",
            "tail_policy": "clip_at_duration",
            "timestamp_semantics": "half_open_seconds",
            "source_index_semantics": "zero_based_micro_window",
        },
        "input_schema": {
            "audio_dim": 768,
            "clip_dim": 1024,
            "rgb_dim": 768,
            "scalar_dim": 37,
            "audio_preprocessing": "frozen",
            "clip_preprocessing": "frozen",
            "rgb_preprocessing": "frozen",
            "scalar_features": ["audio_rms", "audio_non_silent"],
        },
    }
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="window_length_sec"):
        builder.load_formal_schema_manifest(manifest)


def test_formal_schema_must_match_runtime_input_dimensions():
    manifest = {
        "input_schema": {
            "audio_dim": 1,
            "clip_dim": 1024,
            "rgb_dim": 768,
            "scalar_dim": 37,
        }
    }

    with pytest.raises(ValueError, match="audio_dim"):
        builder.assert_formal_schema_matches_runtime(
            manifest,
            runtime_dimensions={
                "audio_dim": 768,
                "clip_dim": 1024,
                "rgb_dim": 768,
                "scalar_dim": 37,
            },
        )


def test_formal_bundle_requires_schema_manifest_before_creating_output(tmp_path):
    frozen_root = tmp_path / "frozen"
    output = tmp_path / "formal.pt"
    write_summary(
        frozen_root,
        ["temporal_offset_scorer_passed"] * 3,
    )

    with pytest.raises(FileNotFoundError, match="schema manifest"):
        builder.build_formal_bundle(
            frozen_root,
            output,
            schema_manifest_path=None,
        )

    assert not output.exists()
