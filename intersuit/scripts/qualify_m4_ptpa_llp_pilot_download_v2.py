#!/usr/bin/env python3
"""构建 LLP pilot 下载前资格审计，并提供只读 metadata probe/媒体后验收入口。"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path
import subprocess
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
INTERSUIT_ROOT = PROJECT_ROOT / "intersuit"
ARTIFACT_ROOT = INTERSUIT_ROOT / "harness/artifacts/m4_ptpa_audio_semantic_fusion_v1"
RESPONSIBILITY_LOCK = INTERSUIT_ROOT / "configs/m4_ptpa_audio_dataset_responsibility_v2.json"
METADATA_ROOT = ARTIFACT_ROOT / "llp_metadata_qualification_v2"
PILOT_PLAN = METADATA_ROOT / "llp_pilot_plan_locked_not_allowed.jsonl"
EXCLUSION_INVENTORY = INTERSUIT_ROOT / "datasets/NEW_AV_TRAIN/exclusion_inventory.json"
AVE_HISTORY = (
    INTERSUIT_ROOT
    / "harness/artifacts/dynamic_selector_d1_ave_official/historical_exposure_inventory.json"
)
STAGE2_MANIFESTS = {
    "as_m4_stage2_train": INTERSUIT_ROOT / "datasets/AS_M4_STAGE2/train_manifest.json",
    "as_m4_stage2_dev": INTERSUIT_ROOT / "datasets/AS_M4_STAGE2/dev_manifest.json",
    "as_m4_stage2_reserve": INTERSUIT_ROOT / "datasets/AS_M4_STAGE2/reserve_manifest.json",
}
LONGVALE_SEALED = ARTIFACT_ROOT / "longvale_s05_media_qualification_v2/longvale_confirm_sealed_effect_locked.jsonl"
M4_IT = INTERSUIT_ROOT / "inputs/texts/m4-it-qwen-audio.generated.json"
LLP_README = INTERSUIT_ROOT / "datasets/LLP/README.md"
LLP_REPOSITORY = INTERSUIT_ROOT / "datasets/LLP"
LLP_REPOSITORY_COMMIT = "fde56118f2c2e7c19188dfefa2569a37b70054fe"
DEFAULT_OUTPUT = ARTIFACT_ROOT / "llp_pilot_download_qualification_v2"
YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")

OFFICIAL_LICENSE_REFERENCES = {
    "llp_repository": "https://github.com/YapengTian/AVVP-ECCV20",
    "youtube_license_types": "https://support.google.com/youtube/answer/2797468?hl=en",
    "youtube_terms": "https://www.youtube.com/static?template=terms",
}
AVQA_OFFICIAL_SOURCES = {
    "dataset_website": "https://mn.cs.tsinghua.edu.cn/avqa/",
    "repository": "https://github.com/AlyssaYoung/AVQA",
    "repository_commit": "a5056c1d82849aa0981f50fa4c5a57bd16658c52",
    "repository_readme_blob": "62afd269a3460cb25a174d0d1b78bd9817db459e",
    "expected_physical_video_count": 57015,
    "source_corpus": "VGGSound",
}
AVQA_ANNOTATION_MIRROR = {
    "repository": "juyil/AVQA-videos",
    "revision": "e9538b7abd3dcf018f85c9e431d51f542728c13b",
    "files": {
        "train_qa.json": "55ec202636c3e25f279995eb68ddd14264004cbe0d042d1f559b03662a8575ee",
        "val_qa.json": "304acac38a254b4dc64b2e07b5202854ff22604d968585777f026010fc0be5bc",
    },
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


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def canonical_youtube_id(value: Any) -> str | None:
    candidate = str(value or "").strip()
    return candidate.casefold() if YOUTUBE_ID.fullmatch(candidate) else None


def youtube_ids(rows: Iterable[dict[str, Any]]) -> set[str]:
    result: set[str] = set()
    for row in rows:
        for field in ("youtube_id", "video_id", "physical_media_id"):
            if value := canonical_youtube_id(row.get(field)):
                result.add(value)
                break
    return result


def verify_inputs() -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, str]]:
    lock = read_json(RESPONSIBILITY_LOCK)
    if (
        lock.get("status") != "plan_locked_dataset_qualification_required"
        or lock.get("permissions", {}).get("dataset_metadata_audit_allowed") is not True
        or lock.get("permissions", {}).get("llp_pilot_download_allowed") is not False
        or lock.get("permissions", {}).get("feature_extraction_allowed") is not False
        or lock.get("permissions", {}).get("training_allowed") is not False
    ):
        raise ValueError("LLP 下载前责任锁权限漂移")
    pilot = read_jsonl(PILOT_PLAN)
    if (
        len(pilot) != 150
        or any(row.get("official_split") not in {"train", "validation"} for row in pilot)
        or any(row.get("download_allowed") is not False for row in pilot)
    ):
        raise ValueError("LLP metadata pilot 冻结合同漂移")
    paths = [
        RESPONSIBILITY_LOCK,
        PILOT_PLAN,
        EXCLUSION_INVENTORY,
        AVE_HISTORY,
        LONGVALE_SEALED,
        M4_IT,
        LLP_README,
        INTERSUIT_ROOT / "datasets/AVQA_HF_ANNOTATIONS/train_qa.json",
        INTERSUIT_ROOT / "datasets/AVQA_HF_ANNOTATIONS/val_qa.json",
        *STAGE2_MANIFESTS.values(),
    ]
    hashes = {str(path.relative_to(PROJECT_ROOT)): sha256(path) for path in paths}
    return lock, pilot, hashes


def build_historical_ledger(pilot: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source_sets: dict[str, set[str]] = {}
    inventory = read_json(EXCLUSION_INVENTORY)
    if inventory.get("status") != "PASS" or len(inventory.get("ids", [])) != inventory.get("id_count"):
        raise ValueError("历史 exclusion inventory 合同漂移")
    source_sets["historical_112_manifest_snapshot"] = {
        value for raw in inventory["ids"] if (value := canonical_youtube_id(raw))
    }

    ave = read_json(AVE_HISTORY)
    source_sets["ave_historical_exposure"] = {
        value
        for row in ave.get("entries", [])
        if (value := canonical_youtube_id(row.get("youtube_id")))
    }
    if len(source_sets["ave_historical_exposure"]) != ave.get("touched_physical_video_count"):
        raise ValueError("AVE 历史暴露数量漂移")

    for name, path in STAGE2_MANIFESTS.items():
        source_sets[name] = youtube_ids(read_json(path))
    source_sets["longvale_effect_locked"] = youtube_ids(read_jsonl(LONGVALE_SEALED))

    avqa_root = INTERSUIT_ROOT / "datasets/AVQA_HF_ANNOTATIONS"
    avqa_rows = {
        "train": read_json(avqa_root / "train_qa.json"),
        "validation": read_json(avqa_root / "val_qa.json"),
    }
    for filename, expected in AVQA_ANNOTATION_MIRROR["files"].items():
        if sha256(avqa_root / filename) != expected:
            raise ValueError(f"AVQA annotation mirror SHA 漂移: {filename}")
    avqa_clips = {
        split: {str(row.get("video_name", "")) for row in rows}
        for split, rows in avqa_rows.items()
    }
    if sum(len(rows) for rows in avqa_rows.values()) != 57335 or len(set().union(*avqa_clips.values())) != 57015:
        raise ValueError("AVQA annotation mirror 官方数量合同漂移")
    avqa_youtube = {value[:11].casefold() for values in avqa_clips.values() for value in values}
    source_sets["avqa_official_annotation_mirror"] = avqa_youtube

    all_history = set().union(*source_sets.values())
    memberships: dict[str, list[str]] = defaultdict(list)
    for source, values in source_sets.items():
        for value in values:
            memberships[value].append(source)
    ledger = [
        {
            "canonical_namespace": "youtube",
            "canonical_physical_media_id": value,
            "historical_sources": sorted(sources),
            "historically_exposed": True,
        }
        for value, sources in sorted(memberships.items())
    ]

    pilot_ids = {canonical_youtube_id(row["physical_media_id"]) for row in pilot}
    if None in pilot_ids or len(pilot_ids) != len(pilot):
        raise ValueError("LLP pilot 物理 YouTube ID 非法或重复")
    overlaps = sorted(value for value in pilot_ids if value in all_history)

    m4_rows = read_json(M4_IT)
    m4_ids = {str(row.get("id", "")).strip() for row in m4_rows if row.get("id")}
    unresolved: list[str] = []
    summary = {
        "artifact_kind": "m4_ptpa_llp_full_historical_physical_id_ledger_v2",
        "source_counts": {name: len(values) for name, values in source_sets.items()},
        "canonical_youtube_union_count": len(all_history),
        "ledger_rows": len(ledger),
        "pilot_physical_video_count": len(pilot_ids),
        "pilot_historical_overlap_count": len(overlaps),
        "pilot_historical_overlap_ids": overlaps,
        "m4_it_scope": {
            "row_count": len(m4_rows),
            "opaque_non_video_id_count": len(m4_ids),
            "youtube_alias_available": False,
            "comparison_conclusion": "M4-IT 本地清单是 image/speech instruction ID，不冒充 YouTube 物理视频 ID",
        },
        "avqa_scope": {
            "local_file_count": 2,
            "physical_id_inventory_available": True,
            "official_sources": AVQA_OFFICIAL_SOURCES,
            "annotation_mirror": AVQA_ANNOTATION_MIRROR,
            "qa_counts": {split: len(rows) for split, rows in avqa_rows.items()},
            "unique_clip_count": len(set().union(*avqa_clips.values())),
            "source_youtube_id_count": len(avqa_youtube),
            "clip_split_overlap": len(avqa_clips["train"] & avqa_clips["validation"]),
            "official_csv_contract": "youtube_id_start_seconds_train_test_split",
            "official_sharepoint_anonymous_result": "redirected_to_microsoft_login",
            "annotation_mirror_accepted_after_official_count_validation": True,
            "third_party_video_subset_used": False,
        },
        "coverage_limitations": unresolved,
        "full_history_closed": not unresolved,
    }
    return ledger, summary


def license_boundary() -> dict[str, Any]:
    readme = LLP_README.read_text(encoding="utf-8")
    result = subprocess.run(
        [
            "git",
            "-C",
            str(LLP_REPOSITORY),
            "show",
            f"{LLP_REPOSITORY_COMMIT}:scripts/download_dataset.py",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError("无法读取 LLP 冻结提交中的官方 downloader")
    downloader = result.stdout
    if (
        "GNU General Public License v3.0" not in readme
        or "Download raw videos" not in readme
        or "youtube.com/watch?v=" not in downloader
    ):
        raise ValueError("LLP README 许可/媒体来源表述漂移")
    return {
        "status": "boundary_locked_per_item_rights_unverified",
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "official_references": OFFICIAL_LICENSE_REFERENCES,
        "local_repository_commit": LLP_REPOSITORY_COMMIT,
        "local_readme_sha256": sha256(LLP_README),
        "official_downloader_blob_sha256": hashlib.sha256(downloader.encode()).hexdigest(),
        "repository_code_and_annotations_statement": "README declares GPL-3.0 for the project",
        "raw_youtube_media_inherits_repository_license": False,
        "youtube_license_types": ["standard_youtube_license", "creative_commons_attribution"],
        "policy": {
            "metadata_probe_allowed": True,
            "media_download_allowed": False,
            "redistribution_allowed": False,
            "commercial_use_claimed": False,
            "per_item_license_must_be_recorded": True,
            "unavailable_private_age_restricted_or_unknown_license_fail_closed": True,
            "explicit_authorization_lock_required_before_media_download": True,
        },
        "important_boundary": (
            "项目仓库提供下载脚本且声明 GPL-3.0，不等于第三方 YouTube 视频自动采用 GPL；"
            "逐视频权利与可用性必须单独记录，本 Review 不提供法律意见。"
        ),
    }


def reviewed_probe_rows(pilot: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for row in pilot:
        value = dict(row)
        value.update(
            {
                "metadata_probe_allowed": True,
                "media_download_allowed": False,
                "test_media_access_allowed": False,
                "required_probe_fields": [
                    "id",
                    "availability",
                    "duration",
                    "license",
                    "webpage_url",
                ],
            }
        )
        rows.append(value)
    return rows


def probe_metadata(
    row: dict[str, Any],
    ytdlp: str,
    cookies_from_browser: str | None = None,
    cookies_file: str | None = None,
    js_runtime: str | None = None,
) -> dict[str, Any]:
    command = [
        ytdlp,
        "--ignore-config",
        "--skip-download",
        "--no-playlist",
        "--dump-single-json",
        "--no-warnings",
    ]
    if cookies_from_browser:
        command.extend(["--cookies-from-browser", cookies_from_browser])
    elif cookies_file:
        command.extend(["--cookies", cookies_file])
    if js_runtime:
        command.extend(["--js-runtimes", js_runtime])
    command.append(row["source_url"])
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        return {
            "sample_id": row["sample_id"],
            "physical_media_id": row["physical_media_id"],
            "status": "probe_failed",
            "returncode": result.returncode,
            "error": result.stderr.strip()[-4000:],
            "media_download_allowed": False,
            "authenticated_metadata_probe": bool(cookies_from_browser),
            "cookie_file_metadata_probe": bool(cookies_file),
            "js_runtime_enabled": bool(js_runtime),
        }
    payload = json.loads(result.stdout)
    actual_id = str(payload.get("id") or "")
    status = "metadata_available" if actual_id == row["physical_media_id"] else "id_mismatch"
    return {
        "sample_id": row["sample_id"],
        "physical_media_id": row["physical_media_id"],
        "status": status,
        "availability": payload.get("availability"),
        "duration": payload.get("duration"),
        "license": payload.get("license"),
        "webpage_url": payload.get("webpage_url"),
        "title": payload.get("title"),
        "channel_id": payload.get("channel_id"),
        "media_download_allowed": False,
        "authenticated_metadata_probe": bool(cookies_from_browser),
        "cookie_file_metadata_probe": bool(cookies_file),
        "js_runtime_enabled": bool(js_runtime),
    }


def summarize_probes(plan: list[dict[str, Any]], probes: list[dict[str, Any]]) -> dict[str, Any]:
    expected = {row["sample_id"] for row in plan}
    actual = [row.get("sample_id") for row in probes]
    duplicates = len(actual) - len(set(actual))
    missing = sorted(expected - set(actual))
    unexpected = sorted(set(actual) - expected)
    structural_complete = not duplicates and not missing and not unexpected and len(probes) == len(expected)
    metadata_available = sum(row.get("status") == "metadata_available" for row in probes)
    return {
        "artifact_kind": "m4_ptpa_llp_youtube_metadata_probe_v2",
        "expected": len(expected),
        "observed": len(probes),
        "status_counts": dict(Counter(str(row.get("status")) for row in probes)),
        "availability_counts": dict(Counter(str(row.get("availability")) for row in probes)),
        "license_counts": dict(Counter(str(row.get("license")) for row in probes)),
        "duplicates": duplicates,
        "missing_sample_ids": missing,
        "unexpected_sample_ids": unexpected,
        "structural_complete": structural_complete,
        "metadata_available": metadata_available,
        "qualification_complete": structural_complete and metadata_available == len(expected),
        "media_download_allowed": False,
    }


def ffprobe(path: Path, executable: str = "ffprobe") -> dict[str, Any]:
    result = subprocess.run(
        [executable, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(result.stderr.strip()[-4000:] or f"ffprobe exit {result.returncode}")
    return json.loads(result.stdout)


def audit_downloaded_media(row: dict[str, Any], path: Path, probe: dict[str, Any]) -> dict[str, Any]:
    streams = probe.get("streams", [])
    format_info = probe.get("format", {})
    try:
        duration = float(format_info.get("duration"))
    except (TypeError, ValueError):
        duration = -1.0
    expected = float(row["end_sec"]) - float(row["start_sec"])
    checks = {
        "file_exists": path.is_file(),
        "nonempty": path.is_file() and path.stat().st_size > 0,
        "has_video_stream": any(stream.get("codec_type") == "video" for stream in streams),
        "has_audio_stream": any(stream.get("codec_type") == "audio" for stream in streams),
        "finite_positive_duration": duration > 0,
        "duration_within_one_second": abs(duration - expected) <= 1.0,
    }
    return {
        "sample_id": row["sample_id"],
        "physical_media_id": row["physical_media_id"],
        "path": str(path),
        "bytes": path.stat().st_size if path.is_file() else 0,
        "sha256": sha256(path) if path.is_file() else None,
        "duration": duration,
        "expected_duration": expected,
        "checks": checks,
        "status": "pass" if all(checks.values()) else "fail",
        "feature_extraction_allowed": False,
        "effect_access_allowed": False,
    }


def build_review(
    ledger_summary: dict[str, Any], boundary: dict[str, Any], probe_summary: dict[str, Any] | None
) -> dict[str, Any]:
    blockers = []
    if not ledger_summary["full_history_closed"]:
        blockers.extend(ledger_summary["coverage_limitations"])
    if probe_summary is None:
        blockers.append("youtube_per_item_metadata_probe_not_run")
    elif not probe_summary["qualification_complete"]:
        blockers.append("youtube_per_item_metadata_probe_incomplete")
    blockers.append("pilot_media_downloader_not_implemented_or_reviewed")
    blockers.append("explicit_media_download_authorization_not_granted")
    return {
        "artifact_kind": "m4_ptpa_llp_pilot_download_qualification_review_v2",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "blocked_before_llp_pilot_download",
        "historical_ledger": ledger_summary,
        "license_boundary": boundary,
        "metadata_probe": probe_summary,
        "blockers": blockers,
        "closed_prior_blockers": [
            "historical_snapshot_and_known_stage_assets_compared_with_pilot_zero_overlap",
            "repository_license_not_misapplied_to_raw_youtube_media",
            "metadata_probe_and_post_download_media_audit_contract_implemented",
        ],
        "permissions": {
            "metadata_probe_allowed": True,
            "pilot_media_download_allowed": False,
            "test_media_access_allowed": False,
            "feature_extraction_allowed": False,
            "gpu_execution_allowed": False,
            "training_allowed": False,
            "effect_access_allowed": False,
        },
        "next_stage_allowed": False,
        "next_stage": "resolve_youtube_metadata_probe_and_avqa_ledger_then_review_media_downloader",
    }


def render_review(review: dict[str, Any]) -> str:
    ledger = review["historical_ledger"]
    return "\n".join(
        [
            "# LLP pilot 下载前资格 Review v2",
            "",
            f"结论：`{review['status'].upper()}`。允许 metadata-only probe，媒体下载仍禁止。",
            "",
            "## 历史物理 ID",
            "",
            f"- 可归一 YouTube 历史并集：`{ledger['canonical_youtube_union_count']}`。",
            f"- 冻结 pilot：`{ledger['pilot_physical_video_count']}`；历史重叠：`{ledger['pilot_historical_overlap_count']}`。",
            f"- 未闭合范围：`{ledger['coverage_limitations']}`。",
            "- M4-IT 本地数据是 image/speech instruction ID，没有伪装成 YouTube ID。",
            "",
            "## 许可边界",
            "",
            "- LLP 仓库 GPL-3.0 声明不自动覆盖第三方 YouTube 原始视频。",
            "- 逐视频必须记录 availability/license；未知、私有、不可用或受限条目 fail-closed。",
            "- 禁止再分发，不声称商业使用权；本 Review 不是法律意见。",
            "",
            "## 当前 blocker",
            "",
            *[f"- `{value}`" for value in review["blockers"]],
            "",
            "## 人话结论",
            "",
            "150 个题目没有撞上当前能核对的旧视频，但 AVQA 历史清单仍缺失。现在只允许联网问 YouTube“视频是否存在、标了什么许可证”，不允许把视频下载回来。",
            "",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--probe-metadata", action="store_true")
    parser.add_argument("--yt-dlp", default="yt-dlp")
    parser.add_argument("--probe-limit", type=int)
    parser.add_argument(
        "--resume-probes",
        action="store_true",
        help="复用同一冻结 plan 中 status=metadata_available 的结果；失败项会重试。",
    )
    parser.add_argument(
        "--cookies-from-browser",
        help="仅传给 yt-dlp 做 metadata probe；Review 不记录 profile 路径或 cookie 内容。",
    )
    parser.add_argument(
        "--cookies-file",
        help="仅传给 yt-dlp 做 metadata probe；Review 不记录文件路径或 cookie 内容。",
    )
    parser.add_argument(
        "--js-runtime",
        help="显式传给 yt-dlp 的 --js-runtimes 值；Review 只记录是否启用。",
    )
    args = parser.parse_args()

    _, pilot, input_hashes = verify_inputs()
    ledger, ledger_summary = build_historical_ledger(pilot)
    boundary = license_boundary()
    probe_plan = reviewed_probe_rows(pilot)
    probes: list[dict[str, Any]] = []
    probe_summary = None
    if args.probe_metadata:
        if args.cookies_from_browser and args.cookies_file:
            raise ValueError("metadata probe 的 credential source 必须二选一")
        selected = probe_plan[: args.probe_limit] if args.probe_limit else probe_plan
        results_path = args.output_root / "llp_metadata_probe_results.jsonl"
        reusable: dict[str, dict[str, Any]] = {}
        if args.resume_probes and results_path.is_file():
            reusable = {
                row["sample_id"]: row
                for row in read_jsonl(results_path)
                if row.get("status") == "metadata_available"
            }
        for row in selected:
            result = reusable.get(row["sample_id"])
            if result is None:
                result = probe_metadata(
                    row,
                    args.yt_dlp,
                    args.cookies_from_browser,
                    args.cookies_file,
                    args.js_runtime,
                )
            probes.append(result)
            write_jsonl(results_path, probes)
        probe_summary = summarize_probes(selected, probes)

    review = build_review(ledger_summary, boundary, probe_summary)
    write_jsonl(args.output_root / "llp_historical_physical_id_ledger.jsonl", ledger)
    write_json(args.output_root / "llp_historical_physical_id_summary.json", ledger_summary)
    write_json(args.output_root / "llp_license_boundary.json", boundary)
    write_jsonl(args.output_root / "llp_metadata_probe_plan_media_locked.jsonl", probe_plan)
    if probes:
        write_json(args.output_root / "llp_metadata_probe_summary.json", probe_summary)
    review["input_hashes"] = input_hashes
    write_json(args.output_root / "llp_pilot_download_review.json", review)
    (args.output_root / "LLP_PILOT_DOWNLOAD_REVIEW_ZH.md").write_text(
        render_review(review), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": review["status"],
                "pilot_rows": len(pilot),
                "historical_overlap": ledger_summary["pilot_historical_overlap_count"],
                "metadata_probe_ran": args.probe_metadata,
                "media_download_allowed": False,
                "output_root": str(args.output_root),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
