#!/usr/bin/env python3
"""审计 LLP 最终媒体的逐视频许可边界，保持特征与效果 fail-closed。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = PROJECT_ROOT / "intersuit"
FINAL_ROOT = INTERSUIT_ROOT / "datasets/LLP_PILOT_MEDIA_V2/final_media_v2"
FINAL_MANIFEST = FINAL_ROOT / "llp_pilot150_media_qualified_effect_locked.jsonl"
FINAL_REVIEW = FINAL_ROOT / "llp_pilot150_final_media_review.json"
DEFAULT_OUTPUT = INTERSUIT_ROOT / "datasets/LLP_PILOT_MEDIA_V2/license_review_v2"
EXPECTED_MANIFEST_SHA256 = "5888f881af6462fc63e1dab504afe1a34fef0c9c5af46f0db7ab68e7d2d35135"
EXPECTED_FINAL_REVIEW_SHA256 = "cef844e19dfea807c89e42265857b50a830b7a93b1dcfd45d10e9411e94eb8a9"
OFFICIAL_REFERENCES = {
    "llp_repository": "https://github.com/YapengTian/AVVP-ECCV20",
    "youtube_license_types": "https://support.google.com/youtube/answer/2797468?hl=en",
    "youtube_video_resource": "https://developers.google.com/youtube/v3/docs/videos",
    "youtube_videos_list": "https://developers.google.com/youtube/v3/docs/videos/list",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def build_outputs() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if sha256(FINAL_MANIFEST) != EXPECTED_MANIFEST_SHA256:
        raise ValueError("LLP final manifest SHA 漂移")
    if sha256(FINAL_REVIEW) != EXPECTED_FINAL_REVIEW_SHA256:
        raise ValueError("LLP final media Review SHA 漂移")
    final_review = read_json(FINAL_REVIEW)
    rows = read_jsonl(FINAL_MANIFEST)
    if len(rows) != 150 or final_review.get("status") != "media_qualified_effect_locked":
        raise ValueError("LLP final media 合同漂移")
    ledger = [
        {
            "sample_id": row["sample_id"],
            "physical_media_id": row["physical_media_id"],
            "official_split": row["official_split"],
            "source_url": row["source_url"],
            "media_sha256": row["media_sha256"],
            "llp_annotation_and_code_license": "GPL-3.0 project statement",
            "raw_youtube_media_inherits_llp_gpl": False,
            "youtube_status_license": None,
            "youtube_status_license_query": "not_run_no_youtube_data_api_key",
            "creative_commons_attribution_verified": False,
            "standard_youtube_license_verified": False,
            "underlying_rightsholder_permission_verified": False,
            "license_status": "unverified",
            "media_qualified": True,
            "feature_extraction_allowed": False,
            "effect_access_allowed": False,
            "training_allowed": False,
            "redistribution_allowed": False,
        }
        for row in rows
    ]
    review = {
        "artifact_kind": "m4_ptpa_llp_pilot150_license_review_v2",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "blocked_pending_per_item_license_or_institutional_authorization",
        "input_hashes": {
            "final_manifest": EXPECTED_MANIFEST_SHA256,
            "final_media_review": EXPECTED_FINAL_REVIEW_SHA256,
        },
        "official_references": OFFICIAL_REFERENCES,
        "rows": len(ledger),
        "media_qualified": sum(row["media_qualified"] for row in ledger),
        "license_verified": sum(row["license_status"] != "unverified" for row in ledger),
        "license_unverified": sum(row["license_status"] == "unverified" for row in ledger),
        "cc_by_verified": sum(row["creative_commons_attribution_verified"] for row in ledger),
        "standard_youtube_license_verified": sum(
            row["standard_youtube_license_verified"] for row in ledger
        ),
        "important_boundaries": [
            "LLP repository GPL-3.0 statement covers the project; it is not inherited by third-party YouTube media",
            "YouTube standard license is the upload default, but missing metadata is not per-item proof",
            "YouTube status.license can be creativeCommon or youtube; no API key is available locally",
            "public accessibility and successful download do not establish reuse, training, or redistribution rights",
            "this Review records a reproducibility boundary and is not legal advice",
        ],
        "resolution_options": [
            "provide a YouTube Data API v3 key through a secure environment variable and query status.license",
            "record an institutional/legal authorization for internal noncommercial research under the official LLP protocol",
            "restrict to a separately verified CC BY subset and rebuild all statistical gates",
        ],
        "permissions": {
            "media_qualification_preserved": True,
            "feature_extraction_allowed": False,
            "gpu_execution_allowed": False,
            "training_allowed": False,
            "effect_access_allowed": False,
            "redistribution_allowed": False,
        },
        "blockers": ["per_item_youtube_license_not_verified"],
        "next_stage_allowed": False,
        "next_stage": "obtain_api_license_metadata_or_institutional_authorization_then_independent_review",
    }
    return ledger, review


def render_review(review: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# LLP pilot150 逐视频许可边界 Review v2",
            "",
            f"结论：`{review['status'].upper()}`。媒体资格保留，但特征/效果/训练仍禁止。",
            "",
            f"- 媒体合格：`{review['media_qualified']}/{review['rows']}`。",
            f"- 逐视频许可已验证/未验证：`{review['license_verified']}/{review['license_unverified']}`。",
            f"- CC BY / 标准 YouTube许可已验证：`{review['cc_by_verified']}/{review['standard_youtube_license_verified']}`。",
            "- LLP仓库GPL不能自动覆盖第三方YouTube视频；公开视频和成功下载也不能自动证明训练或再分发权。",
            "- 当前没有YouTube Data API key，无法查询官方`status.license`字段。",
            "",
            "## 可关闭方式",
            "",
            "1. 安全提供YouTube Data API v3 key，仅查询`status.license`。",
            "2. 提供机构/法务对官方LLP协议下内部非商业研究使用的明确授权记录。",
            "3. 改成逐条验证CC BY子集，并重建样本量/类别/统计门禁。",
            "",
            "## 人话结论",
            "",
            "150个文件技术上都合格，但还缺一张明确的“可以怎么用”的法律/机构许可单。这个问题没关掉前，不开始提特征或训练。",
            "",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    rows, review = build_outputs()
    ledger = args.output_root / "llp_pilot150_license_ledger.jsonl"
    review_path = args.output_root / "llp_pilot150_license_review.json"
    write_jsonl(ledger, rows)
    review["ledger_sha256"] = sha256(ledger)
    write_json(review_path, review)
    (args.output_root / "LLP_PILOT150_LICENSE_REVIEW_ZH.md").write_text(
        render_review(review), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": review["status"],
                "rows": len(rows),
                "ledger_sha256": review["ledger_sha256"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
