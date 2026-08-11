"""Official-metric adapters and fail-closed fixtures for selector V2."""

from __future__ import annotations

import hashlib
import importlib.util
import importlib.metadata
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np


LONGVALE_COMMIT = "2eb88ce3f72458faea6f3db8f841b41e6485a814"
LONGVALE_METRIC_SHA256 = "9d52e32b4c93421705953e91f7fd2bac02e66637be22efca994982186b9118d5"
UNAV_COMMIT = "1609deba4fbaf25222c6ee6c7d43b0d0ddfab20d"
UNAV_METRIC_SHA256 = "31499a26a08ff602f3e67f738d75873cce9e39fcf20d55f7a55c3b75a3bb7f9d"
METRIC_DEPENDENCY_VERSIONS = {"pycocoevalcap": "1.2", "pycocotools": "2.0.11"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_longvale_fixture(annotation: dict, predictions: list[dict]) -> None:
    if not annotation or not isinstance(annotation, dict):
        raise ValueError("LongVALE annotation must be a non-empty object")
    predicted_pairs = set()
    for row in predictions:
        if row.get("task") != "seg_captioning":
            raise ValueError("LongVALE golden predictions must use task=seg_captioning")
        video_id = str(row["video_id"])
        sentence_id = int(row["info"]["sentence_id"])
        if video_id not in annotation or not 0 <= sentence_id < len(annotation[video_id]["sentences"]):
            raise ValueError("LongVALE prediction references an unknown sentence")
        predicted_pairs.add((video_id, sentence_id))
    expected_pairs = {
        (video_id, sentence_id)
        for video_id, item in annotation.items()
        for sentence_id in range(len(item["sentences"]))
    }
    if predicted_pairs != expected_pairs:
        raise ValueError("LongVALE fixture must predict every annotated sentence exactly once")


def validate_unav_fixture(annotation: dict, predictions: dict) -> None:
    database = annotation.get("database")
    if not isinstance(database, dict) or not database:
        raise ValueError("UnAV annotation must contain a non-empty database")
    required = {"video-id", "t-start", "t-end", "label", "score"}
    if set(predictions) != required:
        raise ValueError(f"UnAV predictions must contain exactly {sorted(required)}")
    lengths = {len(predictions[key]) for key in required}
    if len(lengths) != 1 or next(iter(lengths)) == 0:
        raise ValueError("UnAV prediction columns must be non-empty and equal length")


def run_longvale_golden(
    *,
    official_repo: Path,
    annotation_path: Path,
    predictions_path: Path,
    pycocoevalcap_root: Path | None = None,
) -> dict[str, float]:
    metric_path = official_repo / "longvalellm/eval/metric.py"
    _require_frozen_source(metric_path, LONGVALE_METRIC_SHA256)
    if pycocoevalcap_root is not None:
        sys.path.insert(0, str(pycocoevalcap_root))
    if importlib.util.find_spec("pycocoevalcap") is None:
        raise RuntimeError("缺少 pycocoevalcap；LongVALE 正式 metric gate 不允许 skip")
    _require_metric_dependency_versions()
    annotation = json.loads(annotation_path.read_text(encoding="utf-8"))
    predictions = json.loads(predictions_path.read_text(encoding="utf-8"))
    validate_longvale_fixture(annotation, predictions)
    module = _load_module("longvale_official_metric_v2", metric_path)
    _install_writable_ptb_tokenizer()
    raw = module.seg_captioning_metrics(predictions, str(annotation_path))
    normalized = {
        "BLEU4": float(raw["BLEU4"]),
        "METEOR": float(raw["METEOR"]),
        "ROUGE-L": float(raw.get("Rouge", raw.get("ROUGE_L"))),
        "CIDEr": float(raw["CIDEr"]),
    }
    return normalized


def run_longvale_meteor_per_sample(
    *,
    official_repo: Path,
    annotation_path: Path,
    predictions_path: Path,
    pycocoevalcap_root: Path | None = None,
) -> dict:
    """Return the official METEOR aggregate and its paired per-sample scores.

    LongVALE's frozen implementation discards the second value returned by
    ``Meteor.compute_score``.  The second value is needed for a paired physical
    video bootstrap, so this adapter preserves it while using the same frozen
    source contract, PTB tokenizer, dependency versions and pycocoevalcap
    scorer as ``run_longvale_golden``.
    """

    metric_path = official_repo / "longvalellm/eval/metric.py"
    _require_frozen_source(metric_path, LONGVALE_METRIC_SHA256)
    if pycocoevalcap_root is not None:
        sys.path.insert(0, str(pycocoevalcap_root))
    if importlib.util.find_spec("pycocoevalcap") is None:
        raise RuntimeError("缺少 pycocoevalcap；LongVALE 正式 metric gate 不允许 skip")
    _require_metric_dependency_versions()

    from pycocoevalcap.meteor.meteor import Meteor
    from pycocoevalcap.tokenizer.ptbtokenizer import PTBTokenizer

    annotation = json.loads(annotation_path.read_text(encoding="utf-8"))
    predictions = json.loads(predictions_path.read_text(encoding="utf-8"))
    validate_longvale_fixture(annotation, predictions)
    _install_writable_ptb_tokenizer()

    ground_truth = {}
    predicted = {}
    sample_keys = []
    for row in predictions:
        video_id = str(row["video_id"])
        sentence_id = int(row["info"]["sentence_id"])
        sample_key = f"{video_id}_{sentence_id}"
        if sample_key in ground_truth:
            raise ValueError(f"LongVALE prediction pair duplicated: {sample_key}")
        ground_truth[sample_key] = [{
            "image_id": sample_key,
            "caption": str(annotation[video_id]["sentences"][sentence_id]),
        }]
        predicted[sample_key] = [{
            "image_id": sample_key,
            "caption": str(row["answer"]),
        }]
        sample_keys.append((video_id, sentence_id, sample_key))

    tokenizer = PTBTokenizer()
    ground_truth = tokenizer.tokenize(ground_truth)
    predicted = tokenizer.tokenize(predicted)
    aggregate, scores = Meteor().compute_score(ground_truth, predicted)
    score_values = [float(value) for value in scores]
    if len(score_values) != len(sample_keys):
        raise ValueError("LongVALE METEOR per-sample score count drift")
    per_sample_mean = float(np.mean(score_values))
    if not np.isclose(float(aggregate), per_sample_mean, rtol=0.0, atol=1e-12):
        raise ValueError(
            "LongVALE METEOR aggregate/per-sample mean drift: "
            f"aggregate={aggregate}, per_sample_mean={per_sample_mean}"
        )
    return {
        "METEOR": float(aggregate),
        "per_sample_mean": per_sample_mean,
        "per_sample": [
            {"video_id": video_id, "sentence_id": sentence_id, "score": score}
            for (video_id, sentence_id, _), score in zip(sample_keys, score_values)
        ],
    }


def run_unav_golden(
    *,
    official_repo: Path,
    annotation_path: Path,
    predictions_path: Path,
) -> dict:
    metric_path = official_repo / "libs/utils/metrics.py"
    _require_frozen_source(metric_path, UNAV_METRIC_SHA256)
    annotation = json.loads(annotation_path.read_text(encoding="utf-8"))
    predictions = json.loads(predictions_path.read_text(encoding="utf-8"))
    validate_unav_fixture(annotation, predictions)
    # Official UnAV at the frozen commit still uses the removed NumPy alias.
    # The alias is numerically identical and is scoped to this compatibility call.
    had_np_float = hasattr(np, "float")
    previous_np_float = getattr(np, "float", None)
    np.float = float
    try:
        module = _load_module("unav_official_metric_v2", metric_path)
        evaluator = module.ANETdetection(
            str(annotation_path),
            split="validation",
            tiou_thresholds=np.linspace(0.1, 0.9, 9),
            num_workers=1,
            dataset_name="unav-v2-golden",
        )
        arrays = {key: np.asarray(value) for key, value in predictions.items()}
        map_by_tiou, average_map = evaluator.evaluate(arrays, verbose=False)
    finally:
        if had_np_float:
            np.float = previous_np_float
        else:
            delattr(np, "float")
    return {
        "average_mAP": float(average_map),
        "mAP_by_tIoU": [float(value) for value in map_by_tiou],
        "tIoU_thresholds": [float(value) for value in np.linspace(0.1, 0.9, 9)],
    }


def assert_golden_results(longvale: dict, unav: dict) -> None:
    expected_longvale = {"BLEU4": 1.0, "METEOR": 1.0, "ROUGE-L": 1.0, "CIDEr": 10.0}
    for name, expected in expected_longvale.items():
        if not np.isclose(longvale[name], expected, rtol=0.0, atol=1e-6):
            raise AssertionError(f"LongVALE {name} golden mismatch: {longvale[name]} != {expected}")
    if not np.isclose(unav["average_mAP"], 1.0, rtol=0.0, atol=1e-12):
        raise AssertionError("UnAV average mAP golden mismatch")
    if not np.allclose(unav["mAP_by_tIoU"], np.ones(9), rtol=0.0, atol=1e-12):
        raise AssertionError("UnAV mAP-by-tIoU golden mismatch")


def _require_frozen_source(path: Path, expected_sha256: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"official metric source missing: {path}")
    actual = sha256(path)
    if actual != expected_sha256:
        raise ValueError(f"official metric source drift: {path}; expected={expected_sha256}; actual={actual}")


def _load_module(name: str, path: Path):
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load official metric: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _require_metric_dependency_versions() -> None:
    for package, expected in METRIC_DEPENDENCY_VERSIONS.items():
        try:
            actual = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError as error:
            raise RuntimeError(f"缺少正式指标依赖：{package}=={expected}") from error
        if actual != expected:
            raise RuntimeError(f"正式指标依赖漂移：{package} expected={expected} actual={actual}")


def _install_writable_ptb_tokenizer() -> None:
    """Keep official PTB semantics while moving only its temp file to /tmp.

    pycocoevalcap 1.2 writes the input beside its jar. Conda site-packages is
    intentionally read-only in the Codex sandbox, so the unmodified wrapper
    fails before Java starts. The command, jar, flags and punctuation filtering
    remain identical; only the temporary input path becomes writable.
    """

    from pycocoevalcap.tokenizer import ptbtokenizer

    def tokenize(self, captions_for_image):
        tokenizer_dir = Path(ptbtokenizer.__file__).resolve().parent
        command = [
            "java",
            "-cp",
            ptbtokenizer.STANFORD_CORENLP_3_4_1_JAR,
            "edu.stanford.nlp.process.PTBTokenizer",
            "-preserveLines",
            "-lowerCase",
        ]
        final = {}
        image_ids = [key for key, values in captions_for_image.items() for _ in range(len(values))]
        sentences = "\n".join(
            caption["caption"].replace("\n", " ")
            for values in captions_for_image.values()
            for caption in values
        )
        with tempfile.NamedTemporaryFile(delete=False, dir="/tmp") as handle:
            handle.write(sentences.encode())
            temporary_path = Path(handle.name)
        try:
            command.append(str(temporary_path))
            process = subprocess.Popen(command, cwd=tokenizer_dir, stdout=subprocess.PIPE)
            token_lines = process.communicate(input=sentences.rstrip())[0].decode().split("\n")
        finally:
            os.remove(temporary_path)
        for image_id, line in zip(image_ids, token_lines):
            final.setdefault(image_id, []).append(
                " ".join(word for word in line.rstrip().split(" ") if word not in ptbtokenizer.PUNCTUATIONS)
            )
        return final

    ptbtokenizer.PTBTokenizer.tokenize = tokenize
