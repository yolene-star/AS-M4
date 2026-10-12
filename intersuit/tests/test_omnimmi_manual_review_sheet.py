import csv
import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


def load_script(name: str):
    script = Path(f"intersuit/scripts/{name}.py").resolve()
    spec = spec_from_file_location(name, script)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


EXPORT = load_script("export_omnimmi_manual_review_sheet")
APPLY = load_script("apply_omnimmi_manual_review_sheet")
FREEZE = load_script("freeze_omnimmi_m4_four_condition_manifest")


def test_export_excludes_two_no_audio_review_ids() -> None:
    rows = EXPORT.eligible_rows(
        Path("intersuit/harness/artifacts/omnimmi_audio_requirement_review_v1/manual_review_packet.jsonl"),
        Path("intersuit/configs/omnimmi_m4_four_condition_contract_v1.json"),
    )
    assert len(rows) == 89
    assert {"pt:60:1", "pt:185:8"}.isdisjoint({row["review_id"] for row in rows})


def test_apply_and_freeze_support_explicit_exclusion(tmp_path: Path) -> None:
    packet = tmp_path / "packet.jsonl"
    sheet = tmp_path / "sheet.csv"
    reviewed = tmp_path / "reviewed.jsonl"
    rows = [
        {"review_id": "pa:0", "task": "pa", "video": "a.mp4", "source_sample_index": 0,
         "question": "q", "answer": "a", "media_audit": {"has_audio": True}},
        {"review_id": "pa:1", "task": "pa", "video": "b.mp4", "source_sample_index": 1,
         "question": "q2", "answer": "a2", "media_audit": {"has_audio": True}},
    ]
    packet.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    with sheet.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["review_id", "review_status", "final_modality", "reviewer", "review_note"])
        writer.writeheader()
        writer.writerow({"review_id": "pa:0", "review_status": "approved", "final_modality": "audio_required", "reviewer": "r1", "review_note": "需要听语音内容"})
        writer.writerow({"review_id": "pa:1", "review_status": "excluded", "final_modality": "ambiguous_excluded", "reviewer": "r1", "review_note": "证据歧义"})
    result = APPLY.apply_sheet(packet, sheet, reviewed)
    assert result["approved"] == 1 and result["excluded"] == 1
    frozen = FREEZE.freeze_rows(reviewed)
    assert [row["review_id"] for row in frozen] == ["pa:0"]


def test_apply_rejects_blank_review(tmp_path: Path) -> None:
    packet = tmp_path / "packet.jsonl"
    sheet = tmp_path / "sheet.csv"
    output = tmp_path / "reviewed.jsonl"
    packet.write_text(json.dumps({"review_id": "pa:0"}) + "\n", encoding="utf-8")
    sheet.write_text("review_id,review_status,final_modality,reviewer,review_note\npa:0,,,,\n", encoding="utf-8")
    with pytest.raises(ValueError, match="review_status"):
        APPLY.apply_sheet(packet, sheet, output)
