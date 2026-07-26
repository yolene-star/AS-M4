#!/usr/bin/env python3
"""冻结 UnAV-100 Dynamic Selector V2 V1 完整稀疏 validation 媒体清单。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from intersuit.harness.dynamic_selector_v2_media import (
    build_full_validation_media_allowlist,
    load_source_clip_csv,
)


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
    parser.add_argument("--sparse-ratio", type=float, default=0.10)
    parser.add_argument("--expected-videos", type=int, default=335)
    parser.add_argument("--expected-samples", type=int, default=443)
    args = parser.parse_args()

    records = json.loads(args.manifest.read_text(encoding="utf-8"))
    entries = build_full_validation_media_allowlist(
        records,
        load_source_clip_csv(args.source_csv),
        sparse_ratio=args.sparse_ratio,
    )
    sample_count = sum(int(entry["sample_count"]) for entry in entries)
    if len(entries) != args.expected_videos:
        raise ValueError(
            f"完整 validation 物理视频数不匹配：{len(entries)} != {args.expected_videos}"
        )
    if sample_count != args.expected_samples:
        raise ValueError(
            f"完整 validation 样本数不匹配：{sample_count} != {args.expected_samples}"
        )

    payload = {
        "artifact_kind": "dynamic_selector_v2_v1_full_validation_media_allowlist",
        "format_version": 1,
        "split": "validation",
        "sparse_ratio": args.sparse_ratio,
        "video_count": len(entries),
        "sample_count": sample_count,
        "manifest_sha256": sha256(args.manifest),
        "source_csv_sha256": sha256(args.source_csv),
        "entries": entries,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "PASS",
                "video_count": len(entries),
                "sample_count": sample_count,
                "output": str(args.output),
                "output_sha256": sha256(args.output),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
