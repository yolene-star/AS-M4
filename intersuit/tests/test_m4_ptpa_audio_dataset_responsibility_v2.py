from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "configs/m4_ptpa_audio_dataset_responsibility_v2.json"
LLP_DATA = ROOT / "datasets/LLP/data"


def load_lock() -> dict:
    return json.loads(LOCK_PATH.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def physical_id(filename: str) -> str:
    return filename.rsplit("_", 2)[0]


def test_dataset_roles_follow_frozen_stage_order():
    lock = load_lock()

    assert lock["status"] == "plan_locked_dataset_qualification_required"
    assert lock["datasets"]["llp"]["primary_stage"] == "s1"
    assert lock["datasets"]["clotho_aqa"]["primary_stage"] == "s2"
    assert lock["datasets"]["avcaps"]["primary_stage"] == "s3a"
    assert lock["datasets"]["longvale"]["primary_stages"] == ["s3b", "s6"]
    assert lock["datasets"]["ave"]["formal_confirm_allowed"] is False


def test_permissions_remain_fail_closed_after_plan_integration():
    permissions = load_lock()["permissions"]

    assert permissions["dataset_metadata_audit_allowed"] is True
    assert permissions["longvale_part19_download_allowed_by_explicit_user_authorization"] is True
    assert permissions["llp_pilot_download_allowed"] is False
    assert permissions["llp_full_download_allowed"] is False
    assert permissions["clotho_aqa_download_allowed"] is False
    assert permissions["avcaps_download_allowed"] is False
    assert permissions["feature_extraction_allowed"] is False
    assert permissions["gpu_execution_allowed"] is False
    assert permissions["training_allowed"] is False
    assert permissions["confirm_effect_access_allowed"] is False
    assert permissions["formal_test_effect_access_allowed"] is False


def test_cross_dataset_scores_cannot_be_averaged_or_cherry_picked():
    gates = load_lock()["shared_gates"]

    assert gates["dataset_native_scores_must_not_be_averaged_across_datasets"] is True
    assert gates["upstream_pass_cannot_override_downstream_fail"] is True
    assert gates["correct_must_exceed_mismatch"] is True
    assert gates["test_effect_access_once_after_candidate_freeze"] is True


def test_local_llp_metadata_matches_lock_and_official_splits_are_disjoint():
    llp = load_lock()["datasets"]["llp"]
    filenames = {
        "train": "AVVP_train.csv",
        "validation": "AVVP_val_pd.csv",
        "test": "AVVP_test_pd.csv",
    }
    physical_ids: dict[str, set[str]] = {}

    for split, filename in filenames.items():
        path = LLP_DATA / filename
        assert sha256(path) == llp["metadata_sha256"][filename]
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
        assert len(rows) == llp["official_split_counts"][split]
        physical_ids[split] = {physical_id(row["filename"]) for row in rows}

    assert physical_ids["train"].isdisjoint(physical_ids["validation"])
    assert physical_ids["train"].isdisjoint(physical_ids["test"])
    assert physical_ids["validation"].isdisjoint(physical_ids["test"])

    for filename in ("AVVP_eval_audio.csv", "AVVP_eval_visual.csv"):
        assert sha256(LLP_DATA / filename) == llp["metadata_sha256"][filename]


def test_llp_pre_download_review_records_partial_progress_without_unlocking_media():
    llp = load_lock()["datasets"]["llp"]
    qualification = llp["pre_download_qualification"]

    assert qualification["status"] == "historical_ledger_closed_blocked_before_youtube_metadata_and_downloader"
    assert qualification["canonical_youtube_history_count"] == 54664
    assert qualification["pilot_physical_video_count"] == 150
    assert qualification["pilot_historical_overlap_count"] == 0
    assert qualification["avqa_physical_id_inventory_available"] is True
    assert qualification["avqa_official_source_audit"]["expected_physical_video_count"] == 57015
    assert qualification["avqa_official_source_audit"]["annotation_mirror"]["source_youtube_id_count"] == 51157
    assert qualification["avqa_official_source_audit"]["annotation_mirror"]["video_subset_used"] is False
    assert qualification["repository_license_applies_to_raw_youtube_media"] is False
    assert qualification["metadata_probe_full"]["attempted"] == 150
    assert qualification["metadata_probe_full"]["metadata_available"] == 9
    assert qualification["metadata_probe_full"]["probe_failed"] == 141
    assert qualification["metadata_probe_full"]["license_nonempty"] == 0
    assert qualification["metadata_probe_full"]["qualification_complete"] is False
    assert qualification["pilot_media_download_allowed"] is False
    downloader = qualification["official_protocol_downloader"]
    assert downloader["status"] == "node_ejs_compat_canary2_passed_awaiting_canary20_authorization"
    assert downloader["official_repository_commit"] == "fde56118f2c2e7c19188dfefa2569a37b70054fe"
    assert downloader["download_format"] == "best"
    assert downloader["video_codec"] == "libx264"
    assert downloader["audio_codec"] == "aac"
    assert downloader["credentials_rotated_after_chat_exposure"] is True
    assert downloader["anonymous_canary_authorized_by_user"] is True
    assert downloader["anonymous_canary_compromised_credentials_used"] is False
    assert downloader["anonymous_canary_result"]["passed"] == 0
    assert downloader["anonymous_canary_result"]["failed"] == 2
    assert downloader["anonymous_canary_result"]["media_files_created"] == 0
    assert downloader["rotated_cookie_canary_result"]["passed"] == 0
    assert downloader["rotated_cookie_canary_result"]["failed"] == 2
    assert downloader["rotated_cookie_canary_result"]["media_files_created"] == 0
    assert downloader["rotated_cookie_canary_result"]["credential_material_persisted"] is False
    assert downloader["stable_ytdlp_update"]["conda_environment"] == "M4"
    assert downloader["stable_ytdlp_update"]["yt_dlp_version"] == "2026.08.19"
    assert downloader["stable_ytdlp_update"]["websockets_restored_version"] == "11.0.3"
    assert downloader["stable_ytdlp_update"]["new_dependency_conflicts"] == 0
    assert downloader["stable_ytdlp_canary_result"]["passed"] == 0
    assert downloader["stable_ytdlp_canary_result"]["failed"] == 2
    assert downloader["stable_ytdlp_canary_result"]["media_files_created"] == 0
    isolated = downloader["isolated_download_environment"]
    assert isolated["conda_environment"] == "llp-download"
    assert isolated["python_version"] == "3.11.15"
    assert isolated["yt_dlp_channel"] == "nightly"
    assert isolated["yt_dlp_version"] == "2026.08.20.234504"
    assert isolated["ffmpeg_version"] == "8.1.2"
    assert isolated["node_version"] == "26.5.1"
    assert isolated["pip_check"] == "pass_no_broken_requirements"
    assert isolated["m4_environment_used_for_download"] is False
    assert downloader["isolated_nightly_canary_result"]["passed"] == 0
    assert downloader["isolated_nightly_canary_result"]["failed"] == 2
    assert downloader["isolated_nightly_canary_result"]["media_files_created"] == 0
    literal = downloader["literal_official_reproduction"]
    assert literal["status"] == "youtube_dl_canary2_failed_page_reload"
    assert literal["youtube_dl_version"] == "2021.12.17"
    assert literal["cookies_used"] is False
    assert literal["client_or_po_token_args_used"] is False
    assert literal["passed"] == 0
    assert literal["failed"] == 2
    assert literal["source_media_files_created"] == 0
    assert literal["ffmpeg_clip_step_reached"] is False
    node_metadata = downloader["node_ejs_metadata_canary_result"]
    assert node_metadata["metadata_available"] == 2
    assert node_metadata["probe_failed"] == 0
    assert node_metadata["qualification_complete"] is True
    assert node_metadata["js_runtime"] == "node_explicit"
    format_review = downloader["node_ejs_format_review"]
    assert format_review["premerged_best_available"] is False
    assert format_review["mp4_video_only_available"] is True
    assert format_review["m4a_audio_only_available"] is True
    assert format_review["merge_output_format"] == "mp4"
    ffmpeg_repair = downloader["x264_ffmpeg_repair"]
    assert ffmpeg_repair["statically_linked"] is True
    assert ffmpeg_repair["libx264_encoder_available"] is True
    assert ffmpeg_repair["aac_encoder_available"] is True
    assert ffmpeg_repair["official_ffmpeg_codec_contract_restored"] is True
    media_canary = downloader["node_ejs_compat_media_canary_result"]
    assert media_canary["passed"] == 2
    assert media_canary["failed"] == 0
    assert media_canary["media_qualification_passed"] is True
    assert media_canary["media_files_created"] == 2
    assert all(value["duration_sec"] == 10.0 for value in media_canary["files"].values())
    assert all(value["video_codec"] == "h264" for value in media_canary["files"].values())
    assert all(value["audio_codec"] == "aac" for value in media_canary["files"].values())
    assert downloader["canary2_download_allowed"] is False
    assert downloader["pilot150_download_allowed"] is False
    assert "canary20_download_not_authorized" in llp["qualification_blockers"]
    assert "literal_official_youtube_dl_canary2_failed_page_reload" in llp["qualification_blockers"]
    assert "avqa_local_physical_id_inventory_missing" not in llp["qualification_blockers"]
    assert "per_item_youtube_license_not_verified" in llp["qualification_blockers"]


def test_longvale_exception_is_exactly_one_locked_archive():
    lock = load_lock()
    longvale = lock["datasets"]["longvale"]
    archive = longvale["authorized_archive"]

    assert archive["path"].endswith("LongVALE_train_7240_part_19.zip")
    assert archive["size_bytes"] == 5264455669
    assert archive["sha256"] == "1702842ef7daf86b699708bdb8bb44a702486a8b11374778c2a0173a058ca3bf"
    assert archive["eligible_candidate_count_prequalification"] >= archive["minimum_required_candidate_count"]
    qualification = longvale["media_qualification"]
    assert qualification["status"] == "qualified_effect_locked"
    assert qualification["selected_physical_video_count"] == 186
    assert qualification["passed_video_count"] == 186
    assert qualification["failed_video_count"] == 0
    assert qualification["sealed_manifest_sha256"] == "2609157e9708b44038c0148c13ed42bb820b00da87c4e5e413f1db6e865ca9ff"
    assert qualification["confirm_effect_access_allowed"] is False
    assert longvale["qualification_blockers"] == []
    assert lock["next_stage_allowed"] is False
