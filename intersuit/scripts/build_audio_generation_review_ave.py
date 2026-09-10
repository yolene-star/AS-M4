#!/usr/bin/env python
"""构建单一 AVE_HF_EXPANDED reserve 来源的 audio generation review manifest。"""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path

SEED = 20260901
CONDITIONS = ("correct", "mismatch", "video_only", "silence")
SOURCE = "AVE_HF_EXPANDED"
SPLIT = "reserve"

def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def key(value: str) -> str:
    return hashlib.sha256(f"{SEED}:{value}".encode()).hexdigest()

def rows(path: Path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("输入 manifest 必须是 JSON 数组")
    return payload

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=Path("intersuit/datasets/AS_M4_STAGE2/reserve_manifest.json"))
    ap.add_argument("--exclude-manifest", type=Path, default=Path("intersuit/harness/artifacts/audio_semantic_m4_adapter_only_pilot_v1/manifest.jsonl"))
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--groups", type=int, default=20)
    args = ap.parse_args()
    source = rows(args.input)
    excluded = set()
    if args.exclude_manifest.is_file():
        excluded = {json.loads(line)["physical_media_id"] for line in args.exclude_manifest.read_text(encoding="utf-8").splitlines() if line.strip()}
    candidates = []
    for row in source:
        if row.get("source_dataset") != SOURCE or row.get("split") != SPLIT or row.get("task_type") != "audio":
            continue
        if not row.get("event_label") or row.get("physical_media_id") in excluded:
            continue
        video, audio = Path(row["video_path"]), Path(row["scene_audio_path"])
        if not video.is_file() or not audio.is_file():
            raise FileNotFoundError(row["sample_id"])
        if row.get("answer") != row.get("event_label"):
            raise ValueError(f"答案/事件标签不一致：{row['sample_id']}")
        candidates.append(row)
    candidates.sort(key=lambda row: (str(row["event_label"]).casefold(), key(str(row["sample_id"]))))
    selected = []
    used_labels = set()
    for row in candidates:
        label = str(row["event_label"]).casefold()
        if label not in used_labels:
            selected.append(row); used_labels.add(label)
        if len(selected) == args.groups:
            break
    if len(selected) < args.groups:
        for row in candidates:
            if row not in selected:
                selected.append(row)
            if len(selected) == args.groups:
                break
    if len(selected) != args.groups:
        raise ValueError(f"AVE reserve 可用组不足：{len(selected)} < {args.groups}")
    pool = candidates
    output = []
    for index, row in enumerate(selected):
        mismatch_candidates = [x for x in pool if x["physical_media_id"] != row["physical_media_id"] and str(x["event_label"]).casefold() != str(row["event_label"]).casefold()]
        mismatch_candidates.sort(key=lambda x: key(str(x["sample_id"])))
        mismatch = mismatch_candidates[index % len(mismatch_candidates)]
        base = {k: row[k] for k in ("question", "answer", "event_label", "video_path", "video_id", "physical_media_id", "media_sha256", "sample_id", "task_type", "source_dataset", "split", "source_revision")}
        group = f"ave_generation_review_{index:02d}"
        for condition in CONDITIONS:
            audio_row = row if condition in {"correct", "silence"} else (mismatch if condition == "mismatch" else None)
            item = dict(base)
            item.update({"record_id": f"{group}_{condition}", "pair_group_id": group, "condition": condition,
                         "audio_source_path": audio_row["scene_audio_path"] if audio_row else None,
                         "audio_source_sha256": audio_row["audio_sha256"] if audio_row else None,
                         "mismatch_source_sample_id": mismatch["sample_id"] if condition == "mismatch" else None,
                         "mismatch_source_physical_media_id": mismatch["physical_media_id"] if condition == "mismatch" else None,
                         "mismatch_source_event_label": mismatch["event_label"] if condition == "mismatch" else None,
                         "router_target": 0 if condition == "video_only" else 1, "first_stage_training": False})
            output.append(item)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in output) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS_AUDIO_GENERATION_REVIEW_BUILD", "source_dataset": SOURCE, "source_split": SPLIT, "groups": args.groups, "records": len(output), "input_sha256": digest(args.input), "output_sha256": digest(args.output)}, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
