from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/audit_m4_ptpa_audio_semantic_fusion_v1_s0.py"
SPEC = importlib.util.spec_from_file_location("audit_semantic_fusion_s0", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def test_physical_key_unifies_legacy_and_new_manifests():
    assert module.physical_key({"physical_media_id": "AVE:ABC"}) == "abc"
    assert module.physical_key({"youtube_id": " AbC ", "physical_media_id": "ignored"}) == "abc"


def test_overlap_matrix_is_symmetric():
    result = module.build_overlap_matrix({"a": {"1", "2"}, "b": {"2", "3"}})
    assert result["matrix"]["a"]["b"] == 1
    assert result["matrix"]["b"]["a"] == 1
    assert result["set_counts"] == {"a": 2, "b": 2}


def test_hard_negative_requires_labels_and_distinct_media():
    rows = [
        {
            "id": "ok",
            "event_label": "dog",
            "scene_audio_negative_event_label": "cat",
            "physical_media_id": "p1",
            "scene_audio_negative_physical_media_id": "p2",
        },
        {
            "id": "missing",
            "event_label": "",
            "scene_audio_negative_event_label": "cat",
            "physical_media_id": "p3",
            "scene_audio_negative_physical_media_id": "p4",
        },
        {
            "id": "same-media",
            "event_label": "dog",
            "scene_audio_negative_event_label": "cat",
            "physical_media_id": "p5",
            "scene_audio_negative_physical_media_id": "p5",
        },
        {
            "id": "visual-must-not-count",
            "task_type": "visual",
            "event_label": "dog",
            "scene_audio_negative_event_label": "cat",
            "physical_media_id": "p6",
            "scene_audio_negative_physical_media_id": "p7",
        },
    ]
    rows[0]["task_type"] = "audio"
    rows[1]["task_type"] = "audio"
    rows[2]["task_type"] = "audio"
    audit = module.audit_hard_negatives(rows)
    assert audit["counts"]["eligible_clip_level_cross_video"] == 1
    assert audit["counts"]["missing_positive_label"] == 1
    assert audit["counts"]["same_physical_media"] == 1
    assert audit["same_video_window_negative_allowed"] is False


def test_teacher_reproducibility_does_not_authorize_student_target():
    lock = {
        "teacher": {
            "model_id": "teacher",
            "official_source": "source",
            "revision": "rev",
            "license": "apache-2.0",
            "checkpoint_sha256": {"model": "sha"},
        },
        "preprocessing": {
            "normalization": "L2",
            "pooling": "mean",
            "golden_input_features_sha256": "golden",
        },
        "capabilities": {"allowed_student_targets": []},
    }
    audit = module.audit_teacher(lock)
    assert audit["reproducibility_locked"] is True
    assert audit["development_teacher_candidate_allowed"] is True
    assert audit["student_target_authorized"] is False
    assert audit["teacher_confirm_reusable_as_new_confirm"] is False
