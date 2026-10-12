#!/usr/bin/env python3
"""冻结 UnAV-100 validation 的 Dynamic Selector V2 S1 媒体资格清单。"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from intersuit.harness.dynamic_selector_v2_media import build_media_pilot, load_source_clip_csv


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source-csv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=48)
    parser.add_argument("--sparse-ratio", type=float, default=0.10)
    parser.add_argument("--seed", type=int, default=20260724)
    args = parser.parse_args()

    records = json.loads(args.manifest.read_text(encoding="utf-8"))
    entries = build_media_pilot(
        records,
        load_source_clip_csv(args.source_csv),
        limit=args.limit,
        sparse_ratio=args.sparse_ratio,
        seed=args.seed,
    )
    payload = {
        "format_version": 1,
        "artifact_kind": "dynamic_selector_v2_s1_validation_media_pilot_allowlist",
        "status": "FROZEN_NOT_DOWNLOADED",
        "manifest": str(args.manifest.resolve()),
        "manifest_sha256": sha256(args.manifest),
        "source_csv": str(args.source_csv.resolve()),
        "source_csv_sha256": sha256(args.source_csv),
        "config": {
            "split": "validation",
            "limit": args.limit,
            "sparse_ratio": args.sparse_ratio,
            "selection_seed": args.seed,
            "selection": "six-stratum round-robin; greedy unseen-label coverage; stable SHA tie-break",
        },
        "summary": {
            "video_count": len(entries),
            "sample_count": sum(int(entry["sample_count"]) for entry in entries),
            "unique_label_count": len({label for entry in entries for label in entry["event_labels"]}),
            "duration_bins": dict(Counter(entry["duration_bin"] for entry in entries)),
            "complexity_bins": dict(Counter(entry["complexity_bin"] for entry in entries)),
        },
        "entries": entries,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
