import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


SCRIPT = Path("intersuit/scripts/freeze_omnimmi_m4_four_condition_manifest.py").resolve()
SPEC = spec_from_file_location("freeze_omnimmi_m4_four_condition_manifest", SCRIPT)
MODULE = module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_freeze_fails_closed_while_packet_is_pending() -> None:
    packet = Path("intersuit/harness/artifacts/omnimmi_audio_requirement_review_v1/manual_review_packet.jsonl")
    with pytest.raises(ValueError, match="未通过人工复核"):
        MODULE.freeze_rows(packet)


def test_build_manifest_expands_each_approved_row_to_four_conditions(tmp_path: Path) -> None:
    row = {
        "review_id": "pa:0",
        "task": "pa",
        "video": "demo.mp4",
        "source_sample_index": 0,
        "question": "When is the alarm heard?",
        "answer": [1, 2],
        "final_modality": "audio_required",
        "review_status": "approved",
        "review_note": "人工确认需要音频内容",
        "media_audit": {"has_audio": True},
    }
    manifest = MODULE.build_manifest([row])
    assert [item["condition"] for item in manifest] == list(MODULE.CONDITIONS)
    assert all(item["mismatch_source_review_id"] is None for item in manifest)
