#!/usr/bin/env python3
"""生成 OmniMMI PT/PA 模态人工盲审工作包，不读取模型预测。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REVIEW = ROOT / "intersuit/harness/artifacts/omnimmi_audio_requirement_review_v1/review_manifest.jsonl"
DEFAULT_MEDIA = ROOT / "intersuit/harness/artifacts/omnimmi_audio_requirement_review_v1/media_audit.json"
DEFAULT_OUTPUT = ROOT / "intersuit/harness/artifacts/omnimmi_audio_requirement_review_v1/manual_review_packet.jsonl"
DEFAULT_SUMMARY = ROOT / "intersuit/harness/artifacts/omnimmi_audio_requirement_review_v1/manual_review_packet_summary.json"


def stable_key(review_id: str) -> str:
    return hashlib.sha256(review_id.encode("utf-8")).hexdigest()


def build_packet(review_path: Path, media_path: Path, controls_per_task: int = 20) -> list[dict]:
    rows = [json.loads(line) for line in review_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    media = json.loads(media_path.read_text(encoding="utf-8"))["media"]
    excluded = set(json.loads(media_path.read_text(encoding="utf-8"))["ineligible_review_ids"])
    audio_candidates = [
        row for row in rows
        if row["auto_candidate"] in {"audio_required_candidate", "audio_visual_joint_candidate"}
        and row["review_id"] not in excluded
    ]
    visual_candidates = [row for row in rows if row["auto_candidate"] == "visual_only_candidate"]
    selected_controls = []
    for task in ("pt", "pa"):
        task_rows = sorted((row for row in visual_candidates if row["task"] == task), key=lambda row: stable_key(row["review_id"]))
        selected_controls.extend(task_rows[:controls_per_task])
    packet = []
    for role, selected in (("audio_candidate", audio_candidates), ("visual_control_candidate", selected_controls)):
        for row in selected:
            item = dict(row)
            item["review_role"] = role
            item["media_path"] = f"third_party/OmniMMI/omnimmi/videos/{row['video']}"
            item["media_audit"] = media.get(
                row["video"],
                {"status": "not_audited_in_audio_candidate_preflight"},
            )
            item["final_modality"] = None
            item["review_status"] = "pending_human_review"
            item["review_note"] = ""
            packet.append(item)
    return sorted(packet, key=lambda row: (row["task"], row["review_role"], row["review_id"]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-manifest", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--media-audit", type=Path, default=DEFAULT_MEDIA)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--controls-per-task", type=int, default=20)
    args = parser.parse_args()
    packet = build_packet(args.review_manifest.resolve(), args.media_audit.resolve(), args.controls_per_task)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for row in packet:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    counts = {}
    for row in packet:
        key = f"{row['task']}:{row['review_role']}"
        counts[key] = counts.get(key, 0) + 1
    summary = {
        "status": "PENDING_HUMAN_MODALITY_REVIEW",
        "prediction_files_accessed": False,
        "gpu_used": False,
        "rows": len(packet),
        "counts": counts,
        "audio_candidate_rows": sum(row["review_role"] == "audio_candidate" for row in packet),
        "visual_control_rows": sum(row["review_role"] == "visual_control_candidate" for row in packet),
        "controls_per_task": args.controls_per_task,
        "final_labels_frozen": False,
        "mismatch_mapping_frozen": False,
        "packet": str(args.output.resolve().relative_to(ROOT)),
    }
    args.summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
