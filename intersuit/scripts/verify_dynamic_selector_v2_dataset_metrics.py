#!/usr/bin/env python3
"""使用冻结官方实现验证 LongVALE 与 UnAV-100 的 golden metrics。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from intersuit.harness.dynamic_selector_v2_metrics import (
    LONGVALE_COMMIT,
    UNAV_COMMIT,
    assert_golden_results,
    run_longvale_golden,
    run_unav_golden,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = PROJECT_ROOT / "intersuit/tests/fixtures/dynamic_selector_v2"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--longvale-repo", type=Path, required=True)
    parser.add_argument("--unav-repo", type=Path, required=True)
    parser.add_argument("--pycocoevalcap-root", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    longvale = run_longvale_golden(
        official_repo=args.longvale_repo,
        annotation_path=FIXTURES / "longvale_seg_caption_golden.json",
        predictions_path=FIXTURES / "longvale_seg_caption_predictions.json",
        pycocoevalcap_root=args.pycocoevalcap_root,
    )
    unav = run_unav_golden(
        official_repo=args.unav_repo,
        annotation_path=FIXTURES / "unav_detection_golden.json",
        predictions_path=FIXTURES / "unav_detection_predictions.json",
    )
    assert_golden_results(longvale, unav)
    print(
        json.dumps(
            {
                "status": "pass",
                "longvale_official_commit": LONGVALE_COMMIT,
                "unav_official_commit": UNAV_COMMIT,
                "longvale": longvale,
                "unav": unav,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
