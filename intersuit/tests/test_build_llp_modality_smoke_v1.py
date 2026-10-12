"""LLP modality smoke manifest 构建器的 CPU tests。"""

from __future__ import annotations

import importlib.util
import sys
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/build_llp_modality_smoke_v1.py"
SPEC = importlib.util.spec_from_file_location("build_llp_modality_smoke_v1", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def row(sample: str, role: str, label: str | None, start: float = 0.0):
    return {"sample_id": sample, "window_start": start, "window_end": start + 1.0, "modality_role": role, "event_label": label}


def test_balanced_selection_uses_distinct_media_per_role():
    rows = [row(f"{role}-{index}", role, None if role == "background" else "Speech") for role in ("audio_only", "visual_only", "audio_visual", "background") for index in range(3)]
    selected = module.select_balanced_records(rows, ["audio_only", "visual_only", "audio_visual", "background"], 2, 7)
    assert len(selected) == 8
    assert len({(item["modality_role"], item["sample_id"]) for item in selected}) == 8


def test_mismatch_donor_is_different_and_excludes_target():
    target = row("source", "audio_only", "Speech")
    audio = {
        ("source", 0.0, 1.0): {"Speech"},
        ("bad", 0.0, 1.0): {"Speech", "Dog"},
        ("good", 1.0, 2.0): {"Dog"},
    }
    assert module.choose_mismatch_donor(target, "Speech", audio, 11) == ("good", 1.0, 2.0)


def test_background_label_excludes_active_labels():
    target = row("source", "background", None)
    active = defaultdict(set)
    active[module.window_key(target)] = {"Dog"}
    assert module.choose_background_label(target, ["Dog", "Speech"], active, 3) == "Speech"


def test_expand_record_creates_seven_frozen_conditions(tmp_path):
    source = row("source", "audio_only", "Speech")
    media = {
        "source": {"physical_media_id": "source", "media_path": str(tmp_path / "source.mp4")},
        "donor": {"physical_media_id": "donor", "media_path": str(tmp_path / "donor.mp4")},
    }
    queries = {
        "audio_presence": {"conditions": ["correct_audio", "mismatch_audio", "video_only", "silence"]},
        "visual_presence": {"conditions": ["video_only", "correct_audio", "mismatch_audio"]},
    }
    expanded = module.expand_record(source, "Speech", ("donor", 1.0, 2.0), media, tmp_path, queries)
    assert len(expanded) == 7
    assert {item["target_answer"] for item in expanded if item["query_type"] == "audio_presence"} == {"yes"}
    assert {item["target_answer"] for item in expanded if item["query_type"] == "visual_presence"} == {"no"}
    assert len({item["eval_id"] for item in expanded}) == 7
