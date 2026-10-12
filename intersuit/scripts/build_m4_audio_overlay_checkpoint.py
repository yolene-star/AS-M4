#!/usr/bin/env python
"""生成官方 M4 + 最新场景音频模块的组合 checkpoint。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from intersuit.harness.audio_overlay_checkpoint import build_overlay_checkpoint


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-model", required=True, type=Path)
    parser.add_argument("--audio-checkpoint", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--runtime-lock", required=True, type=Path)
    parser.add_argument("--evaluation-lock", required=True, type=Path)
    parser.add_argument("--model-version", required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    manifest = build_overlay_checkpoint(
        base_model_dir=args.base_model,
        audio_checkpoint_dir=args.audio_checkpoint,
        output_dir=args.output_dir,
        runtime_lock_path=args.runtime_lock,
        evaluation_lock_path=args.evaluation_lock,
        model_version=args.model_version,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
