#!/usr/bin/env python3
"""校验并回填 OmniMMI 人工复核 CSV，生成可冻结的 reviewed packet。"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APPROVED_MODALITIES = {"audio_required", "visual_only", "audio_visual_joint"}
EXCLUDED_MODALITY = "ambiguous_excluded"


def apply_sheet(packet_path: Path, sheet_path: Path, output_path: Path) -> dict:
    packet = [json.loads(line) for line in packet_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    by_id = {row["review_id"]: row for row in packet}
    if len(by_id) != len(packet):
        raise ValueError("review packet 含重复 review_id")
    with sheet_path.open("r", encoding="utf-8-sig", newline="") as handle:
        sheet = list(csv.DictReader(handle))
    ids = [row.get("review_id", "") for row in sheet]
    if len(ids) != len(set(ids)):
        raise ValueError("人工复核表含重复 review_id")
    unknown = sorted(set(ids) - set(by_id))
    if unknown:
        raise ValueError(f"人工复核表含未知 review_id: {unknown[:3]}")
    missing = sorted(set(by_id) - set(ids))
    if missing:
        raise ValueError(f"人工复核表缺少 {len(missing)} 条记录，例如 {missing[:3]}")
    completed = []
    for review in sheet:
        review_id = review["review_id"]
        status = review.get("review_status", "").strip()
        modality = review.get("final_modality", "").strip()
        reviewer = review.get("reviewer", "").strip()
        note = review.get("review_note", "").strip()
        if status == "approved":
            if modality not in APPROVED_MODALITIES:
                raise ValueError(f"{review_id}: approved 必须使用三类正式 modality")
        elif status == "excluded":
            if modality != EXCLUDED_MODALITY:
                raise ValueError(f"{review_id}: excluded 必须使用 ambiguous_excluded")
        else:
            raise ValueError(f"{review_id}: review_status 必须为 approved 或 excluded")
        if not reviewer:
            raise ValueError(f"{review_id}: reviewer 不能为空")
        if not note:
            raise ValueError(f"{review_id}: review_note 不能为空")
        item = dict(by_id[review_id])
        item.update({
            "review_status": status,
            "final_modality": modality,
            "reviewer": reviewer,
            "review_note": note,
        })
        completed.append(item)
    completed.sort(key=lambda row: row["review_id"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for row in completed:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return {
        "status": "PASS_HUMAN_MODALITY_REVIEW_IMPORT",
        "rows": len(completed),
        "approved": sum(row["review_status"] == "approved" for row in completed),
        "excluded": sum(row["review_status"] == "excluded" for row in completed),
        "prediction_files_accessed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--sheet", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = apply_sheet(args.packet.resolve(), args.sheet.resolve(), args.output.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
