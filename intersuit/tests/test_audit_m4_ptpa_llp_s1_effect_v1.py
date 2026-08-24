"""LLP S1 effect 独立审计器的 CPU golden tests。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/audit_m4_ptpa_llp_s1_effect_v1.py"
SPEC = importlib.util.spec_from_file_location("audit_m4_ptpa_llp_s1_effect_v1", SCRIPT)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def test_video_equal_mean_does_not_overweight_multilabel_video():
    rows = [
        {"physical_media_id": "a", "value": 1.0},
        {"physical_media_id": "a", "value": 3.0},
        {"physical_media_id": "b", "value": -1.0},
    ]
    assert module.video_equal_mean(rows, "value") == 0.5


def test_dense_labels_skip_nonpositive_intervals(tmp_path):
    path = tmp_path / "dense.tsv"
    path.write_text(
        "filename\tonset\toffset\tevent_labels\n"
        "a\t0\t1\tSpeech\n"
        "a\t2\t2\tDog\n",
        encoding="utf-8",
    )
    assert module.dense_labels(path) == {"a": {"Speech"}}
