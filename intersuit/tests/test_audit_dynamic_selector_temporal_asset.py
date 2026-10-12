"""Dynamic Selector Temporal 条件资产资格审计测试。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import torch


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/audit_dynamic_selector_temporal_asset.py"
)
spec = importlib.util.spec_from_file_location(
    "audit_dynamic_selector_temporal_asset_tested",
    SCRIPT,
)
audit = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = audit
assert spec.loader is not None
spec.loader.exec_module(audit)


def test_historical_failed_asset_is_rejected_for_all_three_reasons():
    historical = {
        "window_length_sec": 1.0,
        "hop_sec": 0.5,
        "window_count": 20,
        "first_start_sec": 0.0,
        "last_end_sec": 10.5,
        "overlapping": True,
        "tail_policy": "extends_to_10.5_seconds",
    }

    statuses = audit.classify_qualification(
        decisions=["temporal_offset_scorer_not_passed"] * 3,
        diagnostic_only=True,
        source_verified=True,
        data_eligible=True,
        input_dimensions_match=True,
        historical_schema=historical,
        target_schema=audit.D1_WINDOW_SCHEMA,
        available_inputs={"audio_features"},
        required_inputs=set(audit.REQUIRED_INPUTS),
    )

    assert statuses == [
        "diagnostic_failed",
        "schema_incompatible",
        "missing_inputs",
    ]


def test_only_passed_exact_complete_asset_is_qualified():
    statuses = audit.classify_qualification(
        decisions=[audit.FORMAL_PASS_DECISION],
        diagnostic_only=False,
        source_verified=True,
        data_eligible=True,
        input_dimensions_match=True,
        historical_schema=audit.D1_WINDOW_SCHEMA,
        target_schema=audit.D1_WINDOW_SCHEMA,
        available_inputs=set(audit.REQUIRED_INPUTS),
        required_inputs=set(audit.REQUIRED_INPUTS),
    )

    assert statuses == ["qualified"]


def test_summary_sha_mismatch_is_separate_corruption_reason():
    statuses = audit.classify_qualification(
        decisions=[audit.FORMAL_PASS_DECISION],
        diagnostic_only=False,
        source_verified=False,
        data_eligible=True,
        input_dimensions_match=True,
        historical_schema=audit.D1_WINDOW_SCHEMA,
        target_schema=audit.D1_WINDOW_SCHEMA,
        available_inputs=set(audit.REQUIRED_INPUTS),
        required_inputs=set(audit.REQUIRED_INPUTS),
    )

    assert statuses == ["corrupt_or_unverifiable"]


def test_data_overlap_is_formally_ineligible():
    statuses = audit.classify_qualification(
        decisions=[audit.FORMAL_PASS_DECISION],
        diagnostic_only=False,
        source_verified=True,
        data_eligible=False,
        input_dimensions_match=True,
        historical_schema=audit.D1_WINDOW_SCHEMA,
        target_schema=audit.D1_WINDOW_SCHEMA,
        available_inputs=set(audit.REQUIRED_INPUTS),
        required_inputs=set(audit.REQUIRED_INPUTS),
    )

    assert statuses == ["data_ineligible"]


def test_window_schema_detects_overlapping_half_second_hop():
    timestamps = torch.tensor(
        [[index * 0.5, index * 0.5 + 1.0] for index in range(20)]
    )

    schema = audit.infer_window_schema(
        timestamps,
        tail_policy="extends_to_10.5_seconds",
    )

    assert schema["window_count"] == 20
    assert schema["window_length_sec"] == 1.0
    assert schema["hop_sec"] == 0.5
    assert schema["last_end_sec"] == 10.5
    assert schema["overlapping"] is True


def test_historical_schema_accepts_19_or_20_windows_by_media_duration():
    rows = [
        {"window_count": 20, "window_start": 0.0, "window_end": 10.5},
        {"window_count": 19, "window_start": 0.0, "window_end": 10.0},
    ]
    timestamps = torch.tensor(
        [[index * 0.5, index * 0.5 + 1.0] for index in range(20)]
    )

    schema, consistent = audit.build_historical_window_schema(rows, timestamps)

    assert consistent is True
    assert schema["window_count_policy"] == "19_or_20_by_media_duration"
    assert schema["observed_window_count_distribution"] == {"19": 1, "20": 1}
    assert schema["observed_last_end_distribution"] == {"10.0": 1, "10.5": 1}
