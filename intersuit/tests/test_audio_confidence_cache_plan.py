from __future__ import annotations

import copy

import pytest

from intersuit.train.audio_confidence_cache import audit_cache_plan, build_cache_plan


SOURCES = {"audio": "a" * 64, "video": "b" * 64, "question": "c" * 64}


def _row(record, question, condition, recipe, seed=None):
    return {
        "record_id": record, "pair_group_id": question, "question_id": question,
        "question": f"question {question}", "condition": condition, "modality": "audio",
        "router_target": 1, "quality_rank": 4, "relevance_rank": 4, "sync_rank": None,
        "first_stage_training": True, "audio_source_relative_media_path": "v.mp4",
        "audio_source_sha256": "d" * 64, "video_relative_media_path": "v.mp4",
        "video_sha256": "d" * 64, "corruption_recipe": recipe, "corruption_seed": seed,
        "target_audio_sample_rate_hz": 44100, "target_audio_channels": 2,
        "target_audio_duration_sec": 10.0, "window_sec": 2.0, "hop_sec": 1.0,
        "target_stream_window_count": 9,
    }


def test_cache_plan_deduplicates_video_question_and_identical_audio():
    rows = [
        _row("r1", "q1", "clean", {"type": "identity"}),
        _row("r2", "q1", "clean", {"type": "identity"}),
        _row("r3", "q2", "noise", {"type": "additive_noise", "snr_db": 10}, 7),
    ]
    plan = build_cache_plan(rows, p2_manifest_sha256="e" * 64, encoder_source_sha256=SOURCES)
    assert plan["unique_counts"] == {"audio": 2, "video": 1, "question": 2}
    result = audit_cache_plan(plan)
    assert result["status"] == "PASS_P3_FEATURE_CACHE_PLAN_AUDIT"
    assert result["saving_ratio"] > 0


def test_cache_plan_audit_rejects_reference_or_payload_drift():
    plan = build_cache_plan(
        [_row("r1", "q1", "clean", {"type": "identity"})],
        p2_manifest_sha256="e" * 64, encoder_source_sha256=SOURCES,
    )
    missing = copy.deepcopy(plan)
    missing["records"][0]["audio_key"] = "f" * 64
    with pytest.raises(ValueError, match="缺失"):
        audit_cache_plan(missing)
    drift = copy.deepcopy(plan)
    key = next(iter(drift["stores"]["video"]))
    drift["stores"]["video"][key]["window_count"] = 8
    with pytest.raises(ValueError, match="payload"):
        audit_cache_plan(drift)
