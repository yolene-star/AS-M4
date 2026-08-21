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
