from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


SCRIPT = Path("intersuit/scripts/audit_omnimmi_manual_review_packet_media.py").resolve()
SPEC = spec_from_file_location("audit_omnimmi_manual_review_packet_media", SCRIPT)
MODULE = module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_packet_media_audit_targets_visual_controls_only() -> None:
    # The command-level audit is intentionally not run here; this verifies the packet contract.
    packet = Path("intersuit/harness/artifacts/omnimmi_audio_requirement_review_v1/manual_review_packet.jsonl")
    rows = [__import__("json").loads(line) for line in packet.read_text(encoding="utf-8").splitlines() if line.strip()]
    controls = [row for row in rows if row.get("review_role") == "visual_control_candidate"]
    assert len(controls) == 40
    assert {row["task"] for row in controls} == {"pt", "pa"}
