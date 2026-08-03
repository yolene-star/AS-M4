import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = (
    REPO_ROOT
    / "intersuit/scripts/prepare_audio_semantic_teacher_t0_assets.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("teacher_t0_assets", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_teacher_confirm_split_is_deterministic_and_disjoint():
    module = load_module()
    expanded = module.read_jsonl(module.DEFAULT_EXPANDED_SPLIT)
    pilot = module.read_jsonl(module.DEFAULT_PILOT_SPLIT)

    confirm_a, student_a, audit_a = module.build_teacher_confirm(
        expanded, pilot
    )
    confirm_b, student_b, audit_b = module.build_teacher_confirm(
        expanded, pilot
    )

    assert confirm_a == confirm_b
    assert student_a == student_b
    assert audit_a == audit_b
    assert len(confirm_a) == 84
    assert len(student_a) == 524
    assert set(audit_a["teacher_confirm_per_label"].values()) == {3}
    assert set(audit_a["physical_overlap"].values()) == {0}

    development, counts = module.build_teacher_development(student_a)
    assert len(development) == 524
    assert len(counts) == 28
    assert min(counts.values()) > 0
    assert all(
        row["teacher_development_subsampled"] is False
        for row in development
    )
    assert {
        row["physical_media_id"] for row in development
    } == {
        row["physical_media_id"] for row in student_a
    }


def test_old_r2_baseline_reproduces_frozen_metrics():
    module = load_module()
    expanded = module.read_jsonl(module.DEFAULT_EXPANDED_SPLIT)
    validation = [
        row
        for row in expanded
        if row["diagnostic_split"] == "validation"
    ]
    source_paths = [
        module.DEFAULT_R2_PER_SAMPLE,
        module.R2_ROOT / "metrics.json",
        module.R2_ROOT / "config.json",
        module.R2_ROOT / "target_lock.json",
        module.R2_ROOT / "manifest_lock.json",
        module.R2_ROOT / "decision.json",
    ]

    rows, lock = module.build_old_r2_lock(
        module.read_jsonl(module.DEFAULT_R2_PER_SAMPLE),
        validation,
        module.read_json(module.R2_ROOT / "metrics.json"),
        module.read_json(module.R2_ROOT / "config.json"),
        module.read_json(module.R2_ROOT / "target_lock.json"),
        source_paths,
        module.INTERSUIT_ROOT
        / "scripts/evaluate_audio_semantic_reconstruction_r2.py",
    )

    assert len(rows) == 196
    assert len({row["sample_id"] for row in rows}) == 196
    assert lock["status"] == "PASS"
    assert lock["golden_reproduction"]["status"] == "PASS"
    assert lock["validation"]["same_order_as_r0_validation"] is True
    assert (
        lock["evaluator"][
            "implementation_sha256_matches_historical_config"
        ]
        is True
    )
