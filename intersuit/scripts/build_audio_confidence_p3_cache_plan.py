#!/usr/bin/env python
"""从 P2 manifest 构建 P3 冻结特征去重缓存计划；不加载模型或 GPU。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from intersuit.data_processing.valor32k_confidence import sha256_file
from intersuit.train.audio_confidence_cache import audit_cache_plan, build_cache_plan


PROJECT_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = PROJECT_ROOT / "intersuit"
DEFAULT_P2 = INTERSUIT_ROOT / "datasets/VALOR32K_AVQA_V2_AUDIT/p2/as_m4_cgf_valor32k_p2_conditions_v1.json"
DEFAULT_SMOKE_RESULT = INTERSUIT_ROOT / "configs/audio_confidence_p3_feature_smoke_result_lock_v1.json"
DEFAULT_OUTPUT = INTERSUIT_ROOT / "harness/artifacts/audio_confidence_p3_cache_plan_v1/cache_plan.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--p2-manifest", type=Path, default=DEFAULT_P2)
    parser.add_argument("--smoke-result", type=Path, default=DEFAULT_SMOKE_RESULT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    smoke = json.loads(args.smoke_result.read_text(encoding="utf-8"))
    rows = json.loads(args.p2_manifest.read_text(encoding="utf-8"))
    plan = build_cache_plan(
        rows,
        p2_manifest_sha256=sha256_file(args.p2_manifest),
        encoder_source_sha256=smoke["result"]["encoder_source_sha256"],
    )
    audit = audit_cache_plan(plan)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**audit, "plan_sha256": sha256_file(args.output)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
