from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/finalize_m4_ptpa_llp_media_v2.py"
SPEC = importlib.util.spec_from_file_location("finalize_m4_ptpa_llp_media_v2", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def test_real_final_manifest_is_150_balanced_media_qualified_and_effect_locked():
    rows, review = module.build_outputs(
        "/home/yjm/miniconda3/envs/llp-download/bin/ffprobe"
    )

    assert len(rows) == 150
    assert len({row["physical_media_id"] for row in rows}) == 150
    assert review["status"] == "media_qualified_effect_locked"
    assert review["split_counts"] == {"train": 100, "validation": 50}
    assert review["role_counts"] == {"original_pilot": 139, "coverage_repair": 11}
    assert review["label_count"] == {"train": 25, "validation": 25}
    assert review["minimum_per_label"] == {"train": 4, "validation": 2}
    assert review["below_required"] == {"train": {}, "validation": {}}
    assert review["historical_overlap"] == 0
    assert review["video_codec_counts"] == {"h264": 150}
    assert review["audio_codec_counts"] == {"aac": 150}
    assert review["permissions"]["feature_extraction_allowed"] is False
    assert review["permissions"]["effect_access_allowed"] is False
    assert review["next_stage_allowed"] is False
    assert all(row["media_qualified"] is True for row in rows)
    assert all(row["effect_access_allowed"] is False for row in rows)


def test_main_writes_manifest_review_and_chinese_report(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--output-root", str(tmp_path)])
    module.main()

    rows = [
        json.loads(line)
        for line in (tmp_path / "llp_pilot150_media_qualified_effect_locked.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]
    review = json.loads(
        (tmp_path / "llp_pilot150_final_media_review.json").read_text(encoding="utf-8")
    )
    assert len(rows) == 150
    assert review["manifest_sha256"] == module.sha256(
        tmp_path / "llp_pilot150_media_qualified_effect_locked.jsonl"
    )
    assert (tmp_path / "LLP_PILOT150_FINAL_MEDIA_REVIEW_ZH.md").is_file()
