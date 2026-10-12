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
    assert lock["datasets"]["unav100_s1_reconstruction"]["primary_stage"] == "s1_reconstruction_candidate"
    assert lock["stage_order"][2] == "s1r_unav_strong_timed_semantic_reconstruction"
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
    assert permissions["llp_s1_internal_noncommercial_feature_preparation_allowed"] is True
    assert permissions["llp_s1_single_gpu_feature_extraction_allowed"] is True
    assert permissions["llp_s1_internal_validation_effect_evaluation_allowed"] is False
    assert permissions["llp_s1_internal_failure_analysis_allowed"] is False
    assert permissions["unav_s1_metadata_audit_allowed"] is True
    assert permissions["unav_s1_media_probe_allowed"] is False
    assert permissions["unav_s1_train_validation_media_download_allowed"] is False
    assert permissions["unav_s1_full_archive_download_allowed_by_explicit_user_authorization"] is False
    assert permissions["unav_s1_archive_member_inventory_allowed"] is False
    assert permissions["unav_s1_archive_extraction_allowed"] is False
    assert permissions["unav_s1_selective_train_validation_extraction_allowed"] is False
    assert permissions["unav_s1r_full_train_validation_media_extraction_allowed"] is False
    assert permissions["unav_s1r_feature_extraction_allowed"] is False
    assert permissions["unav_s1r_validation_effect_access_allowed"] is False
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
    assert downloader["status"] == "pilot150_media_qualified_effect_locked"
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
    assert downloader["canary20_plan"]["row_count"] == 20
    assert downloader["canary20_plan"]["sleep_interval_sec"] == 5.0
    assert downloader["canary20_plan"]["single_threaded"] is True
    canary20 = downloader["canary20_result"]
    assert canary20["passed"] == 20
    assert canary20["failed"] == 0
    assert canary20["media_qualification_passed"] is True
    assert canary20["all_sha_match"] is True
    assert canary20["duration_max_abs_delta_sec"] < 0.1
    assert downloader["canary20_download_allowed"] is False
    pilot150 = downloader["pilot150_result"]
    assert pilot150["qualified_original_media"] == 139
    assert pilot150["permanent_missing"] == 11
    assert pilot150["failure_classes"] == {
        "private": 6,
        "unavailable": 4,
        "age_restricted": 1,
    }
    assert pilot150["qualified_split_counts"] == {"train": 93, "validation": 46}
    backfill = downloader["backfill11_plan"]
    assert backfill["row_count"] == 11
    assert backfill["train"] == 7
    assert backfill["validation"] == 4
    assert backfill["historical_overlap"] == 0
    assert backfill["original_pilot_overlap"] == 0
    assert backfill["selection_is_content_or_effect_based"] is False
    assert downloader["pilot150_download_allowed"] is False
    assert downloader["backfill11_result"]["passed"] == 8
    assert downloader["backfill11_result"]["failed"] == 3
    replacement = downloader["replacement3_plan"]
    assert replacement["row_count"] == 3
    assert replacement["train"] == 2
    assert replacement["validation"] == 1
    assert replacement["historical_overlap"] == 0
    assert replacement["prior_plan_overlap"] == 0
    assert replacement["selection_is_content_or_effect_based"] is False
    assert downloader["backfill11_download_allowed"] is False
    assert downloader["replacement3_result"]["passed"] == 3
    assert downloader["replacement3_result"]["failed"] == 0
    coverage = downloader["coverage_repair11_plan"]
    assert coverage["row_count"] == 11
    assert coverage["existing_reusable"] == 4
    assert coverage["new_downloads"] == 7
    assert coverage["projected_train_min_per_label"] == 4
    assert coverage["projected_validation_min_per_label"] == 2
    assert coverage["projected_below_required"] == 0
    assert coverage["selection_is_content_or_effect_based"] is False
    assert downloader["replacement_download_allowed"] is False
    assert downloader["coverage_repair_round2_result"]["passed"] == 11
    assert downloader["coverage_repair_round2_result"]["failed"] == 0
    final_media = downloader["final_media_qualification"]
    assert final_media["status"] == "media_qualified_effect_locked"
    assert final_media["rows"] == 150
    assert final_media["unique_physical_media"] == 150
    assert final_media["train"] == 100
    assert final_media["validation"] == 50
    assert final_media["train_min_per_label"] == 4
    assert final_media["validation_min_per_label"] == 2
    assert final_media["historical_overlap"] == 0
    assert final_media["video_codec_h264"] == 150
    assert final_media["audio_codec_aac"] == 150
    assert final_media["media_qualified"] is True
    assert final_media["feature_extraction_allowed"] is False
    assert final_media["effect_access_allowed"] is False
    license_review = downloader["license_review"]
    assert license_review["status"] == "user_authorized_internal_noncommercial_feature_preparation_only"
    assert license_review["rows"] == 150
    assert license_review["media_qualified"] == 150
    assert license_review["license_verified"] == 0
    assert license_review["license_unverified"] == 150
    assert license_review["youtube_data_api_key_available"] is False
    assert license_review["institutional_authorization_recorded"] is False
    authorization = license_review["user_scope_authorization"]
    assert authorization["scope"] == "local_server_internal_noncommercial_research"
    assert authorization["per_item_youtube_license_verified"] is False
    assert authorization["institutional_or_legal_approval_claimed"] is False
    assert authorization["gpu_feature_extraction_allowed"] is True
    assert license_review["feature_extraction_allowed"] is True
    assert license_review["effect_access_allowed"] is False
    assert license_review["training_allowed"] is False
    assert license_review["redistribution_allowed"] is False
    feature_preparation = downloader["s1_feature_preparation"]
    assert feature_preparation["status"] == "pass_feature_preparation_only_effect_locked"
    assert feature_preparation["rows"] == 150
    assert feature_preparation["unique_physical_media"] == 150
    assert feature_preparation["split_counts"] == {"train": 100, "validation": 50}
    assert feature_preparation["windows_per_sample"] == 10
    assert feature_preparation["total_windows"] == 1500
    assert feature_preparation["embedding_dim"] == 768
    assert feature_preparation["frozen_beats"] is True
    assert feature_preparation["training_performed"] is False
    assert feature_preparation["effect_accessed"] is False
    effect = downloader["s1_effect_evaluation"]
    assert effect["status"] == "FAIL"
    assert effect["independent_audit_status"] == "PASS"
    assert effect["validation_video_label_queries"] == 99
    assert effect["selected_representation"] is None
    assert effect["best_failing_representation"] == "dense_target_window_mean"
    assert effect["macro_recall_gate_passed_all_representations"] is True
    assert effect["semantic_margin_gate_passed_any_representation"] is False
    assert effect["formal_test_accessed"] is False
    assert effect["training_performed"] is False
    failure = downloader["s1_failure_analysis"]
    assert failure["status"] == "PASS_DIAGNOSTIC_COMPLETE"
    assert failure["original_s1_status"] == "FAIL_UNCHANGED"
    assert failure["independent_audit_status"] == "PASS"
    assert failure["train_multilabel_video_fraction"] == 0.63
    assert failure["prototype_pair_cosine_mean"] > 0.8
    assert failure["imbalance_correlations_significant_at_0_05"] == 0
    assert failure["single_label_counterfactual_macro_recall_delta"] > 0
    assert failure["single_label_counterfactual_margin_delta"] < 0
    assert failure["formal_test_accessed"] is False
    assert failure["training_performed"] is False
    assert downloader["coverage_repair_download_allowed"] is False
    assert downloader["coverage_repair11_result"]["passed"] == 10
    assert downloader["coverage_repair11_result"]["failed"] == 1
    coverage2 = downloader["coverage_repair_round2_plan"]
    assert coverage2["existing_reusable"] == 10
    assert coverage2["new_downloads"] == 1
    assert coverage2["new_candidate_label"] == "Blender"
    assert llp["qualification_blockers"] == [
        "per_item_youtube_license_not_verified_for_redistribution_training_or_broader_use",
        "s1_frozen_beats_semantic_margin_gate_failed",
        "s1_prototype_geometry_entanglement_requires_new_data_representation_contract",
    ]
    assert "avqa_local_physical_id_inventory_missing" not in llp["qualification_blockers"]
    assert any("per_item_youtube_license_not_verified" in item for item in llp["qualification_blockers"])


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


def test_unav_s1_reconstruction_canary20_media_is_qualified_and_effect_locked():
    lock = load_lock()
    unav = lock["datasets"]["unav100_s1_reconstruction"]

    assert unav["status"] == "s1r_full_media_qualified_subset_7941_effect_locked"
    assert unav["hugging_face_revision"] == "daeb88bc14eeff48f8a4ac2836b67f6c350f847b"
    assert unav["metadata_qualification"]["status"] == "PASS"
    assert unav["metadata_qualification"]["physical_split_overlap"] == 0
    assert unav["metadata_qualification"]["invalid_intervals"] == 0
    assert unav["eligible_after_known_history"]["train"]["videos"] == 6226
    assert unav["eligible_after_known_history"]["validation"]["videos"] == 1717
    assert unav["eligible_after_known_history"]["test"]["videos"] == 2098
    assert unav["media_probe"]["node_ejs_compat_canary20"] == "PASS_20_OF_20"
    assert unav["media_canary2"]["status"] == "FAIL"
    assert unav["media_canary2"]["failure_class"] == "youtube_http429_bot_authentication"
    assert unav["media_canary2"]["cookies_used"] is False
    assert unav["media_canary2"]["media_files_created"] == 0
    assert unav["permissions"]["train_validation_media_download_allowed"] is False
    completion = unav["archive_completion"]
    assert completion["status"] == "PASS_ARCHIVE_COMPLETE_VERIFIED_EFFECT_LOCKED"
    assert completion["verified_parts"] == 13
    assert completion["archive_total_bytes"] == 65_775_604_313
    audit = unav["archive_member_audit"]
    assert audit["status"] == "PASS"
    assert audit["regular_mp4_count"] == 10_790
    assert audit["missing_metadata_ids"] == 0
    assert audit["unsafe_members"] == 0
    assert audit["test_media_accessed"] is False
    assert unav["permissions"]["full_archive_download_allowed_by_explicit_user_authorization"] is False
    assert unav["permissions"]["archive_member_inventory_allowed"] is False
    assert unav["permissions"]["archive_extraction_allowed"] is False
    canary = unav["canary20_selective_extraction"]
    assert canary["status"] == "PASS_CANARY20_MEDIA_QUALIFIED_EFFECT_LOCKED"
    assert canary["requested"] == 20
    assert canary["train"] == 10 and canary["validation"] == 10 and canary["test"] == 0
    assert canary["feature_extraction_allowed"] is False
    assert canary["effect_access_allowed"] is False
    result = canary["result"]
    assert result["passed"] == 20 and result["failed"] == 0
    assert result["independent_audit_status"] == "PASS_INDEPENDENT_CANARY20_MEDIA_AUDIT"
    assert result["test_media_accessed"] is False
    assert result["feature_extraction_performed"] is False
    assert result["effect_accessed"] is False
    assert result["training_performed"] is False
    assert unav["permissions"]["selective_train_validation_extraction_allowed"] is False
    s1r = unav["s1r_plan_review"]
    assert s1r["status"] == "PASS_PLAN_ONLY_CANARY20_MEDIA_QUALIFIED"
    assert s1r["historical_llp_s1_status"] == "FAIL_UNCHANGED"
    assert s1r["unav_train_role"] == "development_only"
    assert s1r["unav_validation_role"] == "single_internal_confirm_after_freeze"
    assert s1r["unav_test_role"] == "sealed_formal_confirm"
    assert s1r["full_train_validation_media_extraction_allowed"] is False
    assert s1r["feature_extraction_allowed"] is False
    assert s1r["validation_effect_access_allowed"] is False
    assert s1r["test_effect_access_allowed"] is False
    assert s1r["training_allowed"] is False
    full_media = unav["full_train_validation_media_qualification"]
    assert full_media["status"] == "PASS_QUALIFIED_SUBSET_WITH_2_REJECTED_EFFECT_LOCKED"
    assert full_media["requested"] == 7943
    assert full_media["train"] == 6226
    assert full_media["validation"] == 1717
    assert full_media["test"] == 0
    assert full_media["expected_media_bytes"] == 48_993_281_767
    assert full_media["feature_extraction_allowed"] is False
    assert full_media["effect_access_allowed"] is False
    assert full_media["training_allowed"] is False
    full_result = full_media["result"]
    assert full_result["strict_full_status"] == "FAIL_FULL_MEDIA_QUALIFICATION"
    assert full_result["qualified"] == 7941
    assert full_result["rejected"] == 2
    assert full_result["qualified_train"] == 6224
    assert full_result["qualified_validation"] == 1717
    assert full_result["test"] == 0
    assert full_result["label_coverage_train"] == 100
    assert full_result["label_coverage_validation"] == 100
    assert full_result["rejection_is_effect_based"] is False
    assert full_result["qualified_manifest_is_only_downstream_allowlist"] is True
    assert unav["permissions"]["test_effect_access_allowed"] is False
    assert unav["permissions"]["training_allowed"] is False
    assert lock["next_stage"] == "s1r_unav_frozen_feature_contract_review_required"
