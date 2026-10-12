from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


SCRIPT = Path("intersuit/scripts/build_omnimmi_manual_review_packet.py").resolve()
SPEC = spec_from_file_location("build_omnimmi_manual_review_packet", SCRIPT)
MODULE = module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_manual_packet_has_audio_candidates_and_balanced_visual_controls() -> None:
    packet = MODULE.build_packet(
        Path("intersuit/harness/artifacts/omnimmi_audio_requirement_review_v1/review_manifest.jsonl"),
        Path("intersuit/harness/artifacts/omnimmi_audio_requirement_review_v1/media_audit.json"),
        controls_per_task=20,
    )
    assert len(packet) == 90
    assert sum(row["review_role"] == "audio_candidate" for row in packet) == 50
    assert sum(row["review_role"] == "visual_control_candidate" for row in packet) == 40
    assert sum(row["task"] == "pt" and row["review_role"] == "visual_control_candidate" for row in packet) == 20
    assert sum(row["task"] == "pa" and row["review_role"] == "visual_control_candidate" for row in packet) == 20
    assert all(row["final_modality"] is None for row in packet)
