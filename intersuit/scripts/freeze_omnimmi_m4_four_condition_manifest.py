#!/usr/bin/env python3
"""在人工模态复核完成后冻结 OmniMMI/M4 四条件正式 manifest。"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ALLOWED_MODALITIES = {"audio_required", "visual_only", "audio_visual_joint"}
EXCLUDED_MODALITY = "ambiguous_excluded"
CONDITIONS = ("correct_audio", "mismatch_audio", "video_only", "silence")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def freeze_rows(packet_path: Path) -> list[dict]:
    rows = [json.loads(line) for line in packet_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        raise ValueError("人工复核工作包为空")
    pending = [row["review_id"] for row in rows if row.get("review_status") not in {"approved", "excluded"}]
    if pending:
        raise ValueError(f"仍有 {len(pending)} 条记录未通过人工复核，例如 {pending[:3]}")
    invalid = [
        row["review_id"] for row in rows
        if (row.get("review_status") == "approved" and row.get("final_modality") not in ALLOWED_MODALITIES)
        or (row.get("review_status") == "excluded" and row.get("final_modality") != EXCLUDED_MODALITY)
    ]
    if invalid:
        raise ValueError(f"存在未冻结或不允许的 final_modality: {invalid[:3]}")
    excluded = [row for row in rows if row.get("media_audit", {}).get("has_audio") is False]
    if excluded:
        raise ValueError(f"含无音轨记录，必须先从 packet 移除：{[row['review_id'] for row in excluded[:3]]}")
    return [row for row in rows if row.get("review_status") == "approved"]


def build_manifest(rows: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[f"{row['task']}:{row['video']}:{row['source_sample_index']}"] .append(row)
    manifest = []
    for group_id, group in sorted(groups.items()):
        base = group[0]
        for condition in CONDITIONS:
            manifest.append({
                "pair_group_id": group_id,
                "task": base["task"],
                "video": base["video"],
                "question": base["question"],
                "answer": base["answer"],
                "modality": base["final_modality"],
                "condition": condition,
                "source_review_id": base["review_id"],
                "audio_source_video": base["video"] if condition == "correct_audio" else None,
                "mismatch_source_review_id": None,
                "review_note": base.get("review_note", ""),
            })
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = freeze_rows(args.packet.resolve())
    manifest = build_manifest(rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for row in manifest:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    summary = {
        "status": "FORMAL_MANIFEST_BUILT_PENDING_MISMATCH_MAPPING",
        "source_packet_sha256": sha256(args.packet.resolve()),
        "rows": len(manifest),
        "pair_groups": len(manifest) // len(CONDITIONS),
        "conditions": list(CONDITIONS),
        "mismatch_mapping_frozen": False,
        "formal_manifest_sha256": sha256(args.output.resolve()),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
