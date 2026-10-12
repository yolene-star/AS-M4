#!/usr/bin/env python3
"""导出 OmniMMI 模态人工复核 CSV；排除合同中已知无音轨记录。"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PACKET = ROOT / "intersuit/harness/artifacts/omnimmi_audio_requirement_review_v1/manual_review_packet.jsonl"
DEFAULT_CONTRACT = ROOT / "intersuit/configs/omnimmi_m4_four_condition_contract_v1.json"
DEFAULT_OUTPUT = ROOT / "intersuit/harness/artifacts/omnimmi_audio_requirement_review_v1/manual_review_sheet.csv"
FIELDS = (
    "review_id", "task", "review_role", "video", "media_path", "question", "answer",
    "auto_candidate", "audio_cues", "visual_cues", "review_status", "final_modality",
    "reviewer", "review_note",
)


def eligible_rows(packet_path: Path, contract_path: Path) -> list[dict]:
    rows = [json.loads(line) for line in packet_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    excluded_ids = {item["review_id"] for item in contract.get("media_exclusions", [])}
    return [row for row in rows if row["review_id"] not in excluded_ids]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, default=DEFAULT_PACKET)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    rows = eligible_rows(args.packet.resolve(), args.contract.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            item = dict(row)
            item["answer"] = json.dumps(item.get("answer"), ensure_ascii=False)
            item["audio_cues"] = "|".join(item.get("audio_cues", []))
            item["visual_cues"] = "|".join(item.get("visual_cues", []))
            item["review_status"] = ""
            item["final_modality"] = ""
            item["reviewer"] = ""
            item["review_note"] = ""
            writer.writerow(item)
    print(json.dumps({
        "status": "PENDING_HUMAN_MODALITY_REVIEW",
        "rows": len(rows),
        "media_exclusions_applied": True,
        "output": str(args.output.resolve().relative_to(ROOT)),
    }, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
