from __future__ import annotations

import argparse
import importlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Protocol, TypeAlias, cast

from app.config import get_settings
from app.database import build_repository
from app.matching import build_pair_features_from_values
from app.matching_semantic import (
    DEFAULT_RERANKER_CACHE_PATH,
    DEFAULT_RERANKER_MODEL,
    DEFAULT_SEMANTIC_CACHE_PATH,
    DEFAULT_SENTENCE_TRANSFORMER_MODEL,
    RERANKER_FEATURE_NAMES,
    SEMANTIC_FEATURE_NAMES,
    PairTextValues,
    RerankerPairFeatureBuilder,
    SemanticPairFeatureBuilder,
    build_local_reranker_feature_builder,
    build_local_semantic_feature_builder,
    feature_value,
    resolve_reranker_model_name,
    resolve_semantic_model_name,
)
from app.models import (
    ProductMatchCandidate,
    ProductMatchLabelValue,
    ProductPairFeatures,
)

FEATURES_VERSION = "pair_features_v5"
BASE_FEATURE_NAMES = [
    "token_overlap",
    "rare_token_overlap",
    "numeric_token_agreement",
    "title_similarity",
    "brand_agreement",
    "category_agreement",
    "accessory_mismatch",
    "model_suffix_conflict",
    "storage_conflict",
    "screen_size_conflict",
    "bundle_conflict",
    "canonical_key_match",
    "price_ratio",
]
FEATURE_NAMES = BASE_FEATURE_NAMES + SEMANTIC_FEATURE_NAMES + RERANKER_FEATURE_NAMES
DEFAULT_ARTIFACT_PATH = Path("artifacts/matching/model.joblib")
DataRow: TypeAlias = dict[str, object]
MetricValue: TypeAlias = str | int | float
MetricReport: TypeAlias = dict[str, MetricValue]
ModelBundle: TypeAlias = dict[str, object]


class MatchEstimator(Protocol):
    def fit(self, features: Sequence[Sequence[float]], labels: Sequence[int]) -> object: ...

    def predict_proba(self, features: Sequence[Sequence[float]]) -> object: ...


class _JoblibModule(Protocol):
    def dump(self, value: object, filename: str | Path) -> object: ...

    def load(self, filename: str | Path) -> object: ...


class _MLBundle(Protocol):
    joblib: _JoblibModule
    LogisticRegression: Callable[..., MatchEstimator]
    CalibratedClassifierCV: Callable[..., MatchEstimator]
    train_test_split: Callable[..., list[object]]


class MatchingModelRepository(Protocol):
    def get_match_training_rows(self) -> list[DataRow]: ...
    def save_match_model(
        self,
        *,
        version: str,
        algorithm: str,
        features_version: str,
        artifact_path: str,
        labels_count: int,
        positive_count: int,
        negative_count: int,
        metrics: MetricReport,
        active: bool,
    ) -> object: ...
    def get_active_match_model(self) -> DataRow | None: ...
    def get_unlabeled_match_candidates_for_prediction(self, limit: int) -> list[ProductMatchCandidate]: ...
    def save_match_predictions(
        self, version: str, predictions: Sequence[tuple[int, float, str]]
    ) -> int: ...


@dataclass
class TrainingResult:
    version: str
    algorithm: str
    artifact_path: str
    labels_count: int
    positive_count: int
    negative_count: int
    metrics: MetricReport


def vectorize_features(
    features: ProductPairFeatures,
    feature_names: Sequence[str] | None = None,
) -> list[float]:
    values = features.model_dump()
    vector: list[float] = []
    for name in feature_names or FEATURE_NAMES:
        if name in {
            "accessory_mismatch",
            "model_suffix_conflict",
            "storage_conflict",
            "screen_size_conflict",
            "bundle_conflict",
            "canonical_key_match",
        }:
            vector.append(1.0 if values.get(name) else 0.0)
        elif name in SEMANTIC_FEATURE_NAMES or name in RERANKER_FEATURE_NAMES:
            vector.append(feature_value(features, name))
        else:
            vector.append(float(values.get(name) or 0))
    return vector


def decision_from_probability(probability: float) -> str:
    if probability >= 0.60:
        return ProductMatchLabelValue.SAME.value
    if probability <= 0.20:
        return ProductMatchLabelValue.DIFFERENT.value
    return ProductMatchLabelValue.UNSURE.value


def train_matching_model(
    repository: MatchingModelRepository,
    artifact_path: Path = DEFAULT_ARTIFACT_PATH,
    min_labels: int = 50,
    min_per_class: int = 10,
    semantic_builder: SemanticPairFeatureBuilder | None = None,
    semantic_model: str | None = None,
    reranker_builder: RerankerPairFeatureBuilder | None = None,
    reranker_model: str | None = None,
) -> TrainingResult:
    ml = _load_ml()
    rows = repository.get_match_training_rows()
    features, labels = _training_matrix(
        rows,
        semantic_builder=semantic_builder,
        reranker_builder=reranker_builder,
    )
    positive_count = sum(labels)
    negative_count = len(labels) - positive_count
    if (
        len(labels) < min_labels
        or positive_count < min_per_class
        or negative_count < min_per_class
    ):
        raise RuntimeError(
            "Not enough labeled data to train matching model: "
            + f"labels={len(labels)}, same={positive_count}, different={negative_count}, "
            + f"required labels>={min_labels} and each class>={min_per_class}."
        )

    estimator, algorithm = _build_estimator(
        ml, len(labels), positive_count, negative_count
    )
    metrics = _validation_metrics(
        ml,
        features,
        labels,
        len(labels),
        positive_count,
        negative_count,
    )
    _ = estimator.fit(features, labels)
    version = datetime.now(timezone.utc).strftime("match-%Y%m%d%H%M%S")

    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    _ = ml.joblib.dump(
        {
            "version": version,
            "algorithm": algorithm,
            "features_version": FEATURES_VERSION,
            "feature_names": FEATURE_NAMES,
            "estimator": estimator,
            "semantic_embedding_model": (
                resolve_semantic_model_name(semantic_model) if semantic_model else None
            ),
            "reranker_model": (
                resolve_reranker_model_name(reranker_model) if reranker_model else None
            ),
        },
        artifact_path,
    )
    _ = repository.save_match_model(
        version=version,
        algorithm=algorithm,
        features_version=FEATURES_VERSION,
        artifact_path=str(artifact_path),
        labels_count=len(labels),
        positive_count=positive_count,
        negative_count=negative_count,
        metrics=metrics,
        active=True,
    )
    return TrainingResult(
        version=version,
        algorithm=algorithm,
        artifact_path=str(artifact_path),
        labels_count=len(labels),
        positive_count=positive_count,
        negative_count=negative_count,
        metrics=metrics,
    )


def predict_unlabeled(repository: MatchingModelRepository, limit: int = 1000) -> int:
    ml = _load_ml()
    model_row = repository.get_active_match_model()
    if model_row is None:
        raise RuntimeError("No active matching model found.")
    bundle = cast(ModelBundle, ml.joblib.load(_required_str(model_row, "artifact_path")))
    estimator = cast(MatchEstimator, bundle["estimator"])
    feature_names = cast(Sequence[str], bundle.get("feature_names") or FEATURE_NAMES)
    semantic_builder = _semantic_builder_from_bundle(bundle)
    reranker_builder = _reranker_builder_from_bundle(bundle)
    candidates = repository.get_unlabeled_match_candidates_for_prediction(limit)
    if not candidates:
        return 0
    features = [
        vectorize_features(feature, feature_names)
        for feature in _features_from_candidates(
            candidates,
            semantic_builder=semantic_builder,
            reranker_builder=reranker_builder,
        )
    ]
    probabilities = _positive_probabilities(estimator.predict_proba(features))
    predictions = [
        (
            candidate.id,
            round(float(probability), 6),
            decision_from_probability(float(probability)),
        )
        for candidate, probability in zip(candidates, probabilities)
    ]
    return repository.save_match_predictions(_required_str(model_row, "version"), predictions)


def evaluate_matching_model(repository: MatchingModelRepository) -> MetricReport:
    ml = _load_ml()
    model_row = repository.get_active_match_model()
    if model_row is None:
        raise RuntimeError("No active matching model found.")
    rows = repository.get_match_training_rows()
    bundle = cast(ModelBundle, ml.joblib.load(_required_str(model_row, "artifact_path")))
    feature_names = cast(Sequence[str], bundle.get("feature_names") or FEATURE_NAMES)
    semantic_builder = _semantic_builder_from_bundle(bundle)
    reranker_builder = _reranker_builder_from_bundle(bundle)
    features, labels = _training_matrix(
        rows,
        semantic_builder=semantic_builder,
        reranker_builder=reranker_builder,
        feature_names=feature_names,
    )
    if not labels:
        raise RuntimeError("No labeled data available for evaluation.")
    estimator = cast(MatchEstimator, bundle["estimator"])
    probabilities = _positive_probabilities(estimator.predict_proba(features))
    predictions = [1 if probability >= 0.5 else 0 for probability in probabilities]
    return _classification_metrics(labels, predictions, probabilities)


def _training_matrix(
    rows: Sequence[DataRow],
    *,
    semantic_builder: SemanticPairFeatureBuilder | None = None,
    reranker_builder: RerankerPairFeatureBuilder | None = None,
    feature_names: Sequence[str] | None = None,
) -> tuple[list[list[float]], list[int]]:
    raw_features: list[ProductPairFeatures] = []
    pairs: list[PairTextValues] = []
    labels: list[int] = []
    for row in rows:
        label = row["label"]
        if label == ProductMatchLabelValue.SAME.value:
            labels.append(1)
        elif label == ProductMatchLabelValue.DIFFERENT.value:
            labels.append(0)
        else:
            continue
        raw_features.append(_features_from_training_row(row))
        pairs.append(_pair_from_training_row(row))
    if semantic_builder is not None:
        raw_features = semantic_builder.enrich_many(pairs, raw_features)
    if reranker_builder is not None:
        raw_features = reranker_builder.enrich_many(pairs, raw_features)
    features = [vectorize_features(feature, feature_names) for feature in raw_features]
    return features, labels


def _features_from_training_row(row: DataRow) -> ProductPairFeatures:
    base_features = ProductPairFeatures.model_validate(_features_dict(row))
    if not row.get("left_title") or not row.get("right_title"):
        return base_features
    return build_pair_features_from_values(
        left_title=_required_str(row, "left_title"),
        right_title=_required_str(row, "right_title"),
        left_price=_optional_float(row, "left_price"),
        right_price=_optional_float(row, "right_price"),
        left_canonical_key=_optional_str(row, "left_canonical_key"),
        right_canonical_key=_optional_str(row, "right_canonical_key"),
        base_features=base_features,
    )


def _features_from_candidate(candidate: ProductMatchCandidate) -> ProductPairFeatures:
    return build_pair_features_from_values(
        left_title=candidate.left_title,
        right_title=candidate.right_title,
        left_price=candidate.left_price,
        right_price=candidate.right_price,
        left_canonical_key=candidate.left_canonical_key,
        right_canonical_key=candidate.right_canonical_key,
        base_features=candidate.features,
    )


def _features_from_candidates(
    candidates: Sequence[ProductMatchCandidate],
    *,
    semantic_builder: SemanticPairFeatureBuilder | None = None,
    reranker_builder: RerankerPairFeatureBuilder | None = None,
) -> list[ProductPairFeatures]:
    features = [_features_from_candidate(candidate) for candidate in candidates]
    pairs = [
        PairTextValues(
            left_title=candidate.left_title,
            right_title=candidate.right_title,
            left_canonical_key=candidate.left_canonical_key,
            right_canonical_key=candidate.right_canonical_key,
        )
        for candidate in candidates
    ]
    if semantic_builder is not None:
        features = semantic_builder.enrich_many(pairs, features)
    if reranker_builder is not None:
        features = reranker_builder.enrich_many(pairs, features)
    return features


def _pair_from_training_row(row: DataRow) -> PairTextValues:
    return PairTextValues(
        left_title=_optional_str(row, "left_title") or "",
        right_title=_optional_str(row, "right_title") or "",
        left_canonical_key=_optional_str(row, "left_canonical_key"),
        right_canonical_key=_optional_str(row, "right_canonical_key"),
    )


def _semantic_builder_from_bundle(bundle: ModelBundle) -> SemanticPairFeatureBuilder | None:
    model_name = _optional_str(bundle, "semantic_embedding_model")
    if not model_name:
        return None
    return build_local_semantic_feature_builder(model_name=model_name)


def _reranker_builder_from_bundle(bundle: ModelBundle) -> RerankerPairFeatureBuilder | None:
    model_name = _optional_str(bundle, "reranker_model")
    if not model_name:
        return None
    return build_local_reranker_feature_builder(model_name=model_name)


def _build_estimator(
    ml: _MLBundle, labels_count: int, positive_count: int, negative_count: int
) -> tuple[MatchEstimator, str]:
    base = ml.LogisticRegression(class_weight="balanced", max_iter=1000)
    if labels_count >= 100 and positive_count >= 20 and negative_count >= 20:
        return ml.CalibratedClassifierCV(
            base, method="sigmoid", cv=3
        ), "logistic_regression_calibrated_sigmoid"
    return base, "logistic_regression"


def _validation_metrics(
    ml: _MLBundle,
    features: list[list[float]],
    labels: list[int],
    labels_count: int,
    positive_count: int,
    negative_count: int,
) -> MetricReport:
    if labels_count >= 50 and positive_count >= 10 and negative_count >= 10:
        split = ml.train_test_split(
            features,
            labels,
            test_size=0.2,
            random_state=42,
            stratify=labels,
        )
        x_train = cast(list[list[float]], split[0])
        x_test = cast(list[list[float]], split[1])
        y_train = cast(list[int], split[2])
        y_test = cast(list[int], split[3])
        estimator, _ = _build_estimator(
            ml, len(y_train), sum(y_train), len(y_train) - sum(y_train)
        )
        _ = estimator.fit(x_train, y_train)
        probabilities = _positive_probabilities(estimator.predict_proba(x_test))
        predictions = [1 if probability >= 0.5 else 0 for probability in probabilities]
        metrics = _classification_metrics(y_test, predictions, probabilities)
        metrics["evaluation"] = "held_out_20_percent"
        metrics["train_labels_count"] = len(y_train)
        return metrics

    estimator, _ = _build_estimator(ml, labels_count, positive_count, negative_count)
    _ = estimator.fit(features, labels)
    probabilities = _positive_probabilities(estimator.predict_proba(features))
    predictions = [1 if probability >= 0.5 else 0 for probability in probabilities]
    metrics = _classification_metrics(labels, predictions, probabilities)
    metrics["evaluation"] = "training_set"
    return metrics


def _classification_metrics(
    labels: list[int], predictions: list[int], probabilities: Sequence[float]
) -> MetricReport:
    total = len(labels)
    tp = sum(1 for truth, pred in zip(labels, predictions) if truth == 1 and pred == 1)
    tn = sum(1 for truth, pred in zip(labels, predictions) if truth == 0 and pred == 0)
    fp = sum(1 for truth, pred in zip(labels, predictions) if truth == 0 and pred == 1)
    fn = sum(1 for truth, pred in zip(labels, predictions) if truth == 1 and pred == 0)
    precision = tp / (tp + fp) if tp + fp else 0
    recall = tp / (tp + fn) if tp + fn else 0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0
    brier = (
        sum((float(prob) - truth) ** 2 for prob, truth in zip(probabilities, labels))
        / total
    )
    return {
        "accuracy": round((tp + tn) / total, 4) if total else 0,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "brier": round(brier, 6),
        "labels_count": total,
        "positive_count": sum(labels),
        "negative_count": total - sum(labels),
    }


def _positive_probabilities(values: object) -> list[float]:
    matrix = cast(Sequence[Sequence[float]], values)
    return [float(row[1]) for row in matrix]


def _features_dict(row: DataRow) -> dict[str, object]:
    value = row.get("features")
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _required_str(row: dict[str, object], key: str) -> str:
    value = row[key]
    if not isinstance(value, str):
        raise TypeError(f"Expected {key} to be str")
    return value


def _optional_str(row: dict[str, object], key: str) -> str | None:
    value = row.get(key)
    if value is None or isinstance(value, str):
        return value
    return str(value)


def _optional_float(row: DataRow, key: str) -> float | None:
    value = row.get(key)
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _required_int(row: DataRow, key: str) -> int:
    value = row[key]
    if not isinstance(value, int):
        raise TypeError(f"Expected {key} to be int")
    return value


def _load_ml() -> _MLBundle:
    try:
        joblib = importlib.import_module("joblib")
        sklearn_calibration = importlib.import_module("sklearn.calibration")
        sklearn_linear_model = importlib.import_module("sklearn.linear_model")
        sklearn_model_selection = importlib.import_module("sklearn.model_selection")
    except ImportError as exc:
        raise RuntimeError(
            'Matching model dependencies are missing. Install them with: pip install -e ".[ml]"'
        ) from exc

    return cast(
        _MLBundle,
        cast(object, SimpleNamespace(
            joblib=cast(_JoblibModule, cast(object, joblib)),
            CalibratedClassifierCV=cast(
                Callable[..., MatchEstimator],
                getattr(sklearn_calibration, "CalibratedClassifierCV"),
            ),
            LogisticRegression=cast(
                Callable[..., MatchEstimator],
                getattr(sklearn_linear_model, "LogisticRegression"),
            ),
            train_test_split=cast(
                Callable[..., list[object]],
                getattr(sklearn_model_selection, "train_test_split"),
            ),
        )),
    )


features_from_candidates = _features_from_candidates
build_estimator = _build_estimator
classification_metrics = _classification_metrics
load_ml = _load_ml


def _repository() -> MatchingModelRepository:
    return cast(MatchingModelRepository, cast(object, build_repository(get_settings().database_url)))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Train and run local product matching model."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    train_parser = subparsers.add_parser("train")
    _ = train_parser.add_argument("--artifact-path", default=str(DEFAULT_ARTIFACT_PATH))
    _ = train_parser.add_argument("--min-labels", type=int, default=50)
    _ = train_parser.add_argument("--min-per-class", type=int, default=10)
    _ = train_parser.add_argument("--semantic-model", default=None)
    _ = train_parser.add_argument(
        "--semantic-cache-path", default=str(DEFAULT_SEMANTIC_CACHE_PATH)
    )
    _ = train_parser.add_argument("--reranker-model", default=None)
    _ = train_parser.add_argument(
        "--reranker-cache-path", default=str(DEFAULT_RERANKER_CACHE_PATH)
    )

    predict_parser = subparsers.add_parser("predict-unlabeled")
    _ = predict_parser.add_argument("--limit", type=int, default=1000)

    _ = subparsers.add_parser("evaluate")

    args = cast(dict[str, object], vars(parser.parse_args()))
    command = _required_str(args, "command")
    repository = _repository()
    try:
        if command == "train":
            semantic_arg = _optional_str(args, "semantic_model")
            reranker_arg = _optional_str(args, "reranker_model")
            semantic_builder = (
                build_local_semantic_feature_builder(
                    model_name=semantic_arg or DEFAULT_SENTENCE_TRANSFORMER_MODEL,
                    cache_path=Path(_required_str(args, "semantic_cache_path")),
                )
                if semantic_arg
                else None
            )
            semantic_model = (
                resolve_semantic_model_name(semantic_arg)
                if semantic_arg
                else None
            )
            reranker_builder = (
                build_local_reranker_feature_builder(
                    model_name=reranker_arg or DEFAULT_RERANKER_MODEL,
                    cache_path=Path(_required_str(args, "reranker_cache_path")),
                )
                if reranker_arg
                else None
            )
            reranker_model = (
                resolve_reranker_model_name(reranker_arg)
                if reranker_arg
                else None
            )
            result = train_matching_model(
                repository,
                artifact_path=Path(_required_str(args, "artifact_path")),
                min_labels=_required_int(args, "min_labels"),
                min_per_class=_required_int(args, "min_per_class"),
                semantic_builder=semantic_builder,
                semantic_model=semantic_model,
                reranker_builder=reranker_builder,
                reranker_model=reranker_model,
            )
            print(result)
        elif command == "predict-unlabeled":
            print(
                {"predictions_saved": predict_unlabeled(repository, limit=_required_int(args, "limit"))}
            )
        elif command == "evaluate":
            print(evaluate_matching_model(repository))
    except RuntimeError as exc:
        parser.exit(1, f"error: {exc}\n")


if __name__ == "__main__":
    main()
