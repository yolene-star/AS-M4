from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


SCRIPT = Path("intersuit/scripts/audit_omnimmi_audio_candidate_media.py").resolve()
SPEC = spec_from_file_location("audit_omnimmi_audio_candidate_media", SCRIPT)
MODULE = module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_selected_review_rows_are_only_explicit_audio_candidates() -> None:
    rows = MODULE.selected_review_rows(
        Path("intersuit/harness/artifacts/omnimmi_audio_requirement_review_v1/review_manifest.jsonl")
    )
    assert len(rows) == 51
    assert {row["auto_candidate"] for row in rows} == {
        "audio_required_candidate",
        "audio_visual_joint_candidate",
    }
    assert all(row["review_status"] == "pending_human_review" for row in rows)
