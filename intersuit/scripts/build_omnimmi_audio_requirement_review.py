#!/usr/bin/env python3
"""构建 OmniMMI PT/PA 音频依赖人工复核清单，不读取模型预测。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PT = ROOT / "third_party/OmniMMI/omnimmi/proactive_turntaking.json"
DEFAULT_PA = ROOT / "third_party/OmniMMI/omnimmi/proactive_alerting.json"
DEFAULT_OUTPUT = ROOT / "intersuit/harness/artifacts/omnimmi_audio_requirement_review_v1"

AUDIO_CUES = (
    "hear", "heard", "sound", "audio", "noise", "music", "sing", "singing", "says", "said",
    "saying", "speak", "speaking", "talk", "talking", "voice", "comment", "comments",
    "mention", "mentioned", "mentioning", "ask", "asked", "shout", "crying", "laugh",
    "applause", "ring", "ringing", "alarm",
)
VISUAL_CUES = (
    "color", "wear", "wears", "wearing", "look", "shown", "shows", "visible", "frame", "see", "watch",
    "where", "how many people", "at the start", "at the end", "holding", "standing",
    "sitting", "walking", "grooming", "driving", "outside", "inside",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cue_hits(text: str, cues: tuple[str, ...]) -> list[str]:
    lowered = text.lower()
    return sorted({
        cue for cue in cues
        if re.search(rf"(?<![a-z]){re.escape(cue)}(?![a-z])", lowered)
    })


def candidate_label(question: str, answer: str) -> tuple[str, list[str], list[str]]:
    combined = f"{question} {answer}"
    audio_hits = cue_hits(combined, AUDIO_CUES)
    visual_hits = cue_hits(combined, VISUAL_CUES)
    if audio_hits and visual_hits:
        label = "audio_visual_joint_candidate"
    elif audio_hits:
        label = "audio_required_candidate"
    elif visual_hits:
        label = "visual_only_candidate"
    else:
        label = "ambiguous_candidate"
    return label, audio_hits, visual_hits


def build_rows(pt_path: Path, pa_path: Path) -> list[dict]:
    pt = json.loads(pt_path.read_text(encoding="utf-8"))
    pa = json.loads(pa_path.read_text(encoding="utf-8"))
    rows: list[dict] = []
    for sample_index, sample in enumerate(pt):
        for turn_index, turn in enumerate(sample["conversation"]):
            if "assistant" not in turn:
                continue
            question = str(turn["human"]).strip()
            answer = str(turn["assistant"]).strip()
            label, audio_hits, visual_hits = candidate_label(question, answer)
            rows.append({
                "review_id": f"pt:{sample_index}:{turn_index}",
                "task": "pt",
                "source_file": str(pt_path.relative_to(ROOT)),
                "source_sample_index": sample_index,
                "source_turn_index": turn_index,
                "video": sample["video"],
                "question": question,
                "answer": answer,
                "auto_candidate": label,
                "audio_cues": audio_hits,
                "visual_cues": visual_hits,
                "final_modality": None,
                "review_status": "pending_human_review",
                "review_note": "",
            })
    for sample_index, sample in enumerate(pa):
        question = str(sample["question"]).strip()
        answer = json.dumps(sample["answer"], ensure_ascii=False)
        label, audio_hits, visual_hits = candidate_label(question, answer)
        rows.append({
            "review_id": f"pa:{sample_index}",
            "task": "pa",
            "source_file": str(pa_path.relative_to(ROOT)),
            "source_sample_index": sample_index,
            "source_turn_index": None,
            "video": sample["video"],
            "question": question,
            "answer": sample["answer"],
            "auto_candidate": label,
            "audio_cues": audio_hits,
            "visual_cues": visual_hits,
            "final_modality": None,
            "review_status": "pending_human_review",
            "review_note": "",
        })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pt", type=Path, default=DEFAULT_PT)
    parser.add_argument("--pa", type=Path, default=DEFAULT_PA)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    pt_path = args.pt.resolve()
    pa_path = args.pa.resolve()
    output_dir = args.output_dir.resolve()
    rows = build_rows(pt_path, pa_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "review_manifest.jsonl"
    with manifest_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    counts = Counter(row["auto_candidate"] for row in rows)
    task_counts = Counter(row["task"] for row in rows)
    summary = {
        "status": "PENDING_HUMAN_MODALITY_REVIEW",
        "prediction_files_accessed": False,
        "pt_source": str(pt_path.relative_to(ROOT)),
        "pt_sha256": sha256(pt_path),
        "pa_source": str(pa_path.relative_to(ROOT)),
        "pa_sha256": sha256(pa_path),
        "rows": len(rows),
        "task_counts": dict(sorted(task_counts.items())),
        "auto_candidate_counts": dict(sorted(counts.items())),
        "review_manifest": str(manifest_path.relative_to(ROOT)),
        "review_manifest_sha256": sha256(manifest_path),
        "formal_labels_frozen": False,
        "gpu_authorized": False,
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
