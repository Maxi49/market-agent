from pathlib import Path

import pytest

from app.database import (
    SearchRepository,
    product_match_candidates,
    product_match_models,
    product_match_predictions,
)
from app.matching_model import (
    FEATURES_VERSION,
    RERANKER_FEATURE_NAMES,
    SEMANTIC_FEATURE_NAMES,
    decision_from_probability,
    evaluate_matching_model,
    predict_unlabeled,
    train_matching_model,
    vectorize_features,
)
from app.matching_semantic import (
    DEFAULT_RERANKER_MODEL,
    DEFAULT_SENTENCE_TRANSFORMER_MODEL,
    resolve_reranker_model_name,
    resolve_semantic_model_name,
)
from app.models import ProductMatchLabelRequest, ProductMatchLabelValue, ProductPairFeatures


def test_vectorize_features_is_stable_and_ordered() -> None:
    features = ProductPairFeatures(
        token_overlap=0.1,
        rare_token_overlap=0.2,
        numeric_token_agreement=0.3,
        title_similarity=0.4,
        brand_agreement=1,
        category_agreement=0,
        accessory_mismatch=True,
        model_suffix_conflict=True,
        storage_conflict=True,
        screen_size_conflict=False,
        bundle_conflict=True,
        canonical_key_match=True,
        price_ratio=None,
        title_embedding_similarity=0.91,
        normalized_title_embedding_similarity=0.92,
        canonical_text_embedding_similarity=0.93,
        brand_model_text_embedding_similarity=0.94,
        reranker_score_raw_avg=0.81,
        reranker_score_same_query_avg=0.82,
    )

    assert FEATURES_VERSION == "pair_features_v5"
    assert vectorize_features(features) == [
        0.1,
        0.2,
        0.3,
        0.4,
        1.0,
        0.0,
        1.0,
        1.0,
        1.0,
        0.0,
        1.0,
        1.0,
        0.0,
        0.91,
        0.92,
        0.93,
        0.94,
        0.81,
        0.82,
    ]
    assert vectorize_features(features, feature_names=["token_overlap", "price_ratio"]) == [0.1, 0.0]
    assert vectorize_features(
        ProductPairFeatures(),
        feature_names=["reranker_score_raw_avg", "reranker_score_same_query_avg"],
    ) == [0.0, 0.0]
    assert SEMANTIC_FEATURE_NAMES == [
        "title_embedding_similarity",
        "normalized_title_embedding_similarity",
        "canonical_text_embedding_similarity",
        "brand_model_text_embedding_similarity",
    ]
    assert RERANKER_FEATURE_NAMES == [
        "reranker_score_raw_avg",
        "reranker_score_same_query_avg",
    ]


def test_decision_thresholds_are_conservative() -> None:
    assert decision_from_probability(0.81) == "same"
    assert decision_from_probability(0.19) == "different"
    assert decision_from_probability(0.50) == "unsure"


def test_semantic_model_presets_resolve_to_hugging_face_ids() -> None:
    assert DEFAULT_SENTENCE_TRANSFORMER_MODEL == "BAAI/bge-m3"
    assert resolve_semantic_model_name("bge-m3") == "BAAI/bge-m3"
    assert resolve_semantic_model_name("e5-small") == "intfloat/multilingual-e5-small"
    assert resolve_semantic_model_name("custom/model") == "custom/model"
    assert DEFAULT_RERANKER_MODEL == "BAAI/bge-reranker-v2-m3"
    assert resolve_reranker_model_name("bge-reranker-v2-m3") == "BAAI/bge-reranker-v2-m3"
    assert resolve_reranker_model_name("custom/reranker") == "custom/reranker"


def test_train_fails_with_insufficient_labels(tmp_path: Path) -> None:
    repository = _repository_with_candidates()
    repository.label_match_candidate(
        1,
        ProductMatchLabelRequest(label=ProductMatchLabelValue.UNSURE, comment=None),
    )

    with pytest.raises(RuntimeError, match="Not enough labeled data"):
        train_matching_model(repository, tmp_path / "model.joblib", min_labels=2, min_per_class=1)


def test_train_predict_and_evaluate_matching_model(tmp_path: Path) -> None:
    repository = _repository_with_candidates()
    repository.label_match_candidate(
        1,
        ProductMatchLabelRequest(label=ProductMatchLabelValue.SAME, comment=None),
    )
    repository.label_match_candidate(
        2,
        ProductMatchLabelRequest(label=ProductMatchLabelValue.DIFFERENT, comment=None),
    )

    result = train_matching_model(repository, tmp_path / "model.joblib", min_labels=2, min_per_class=1)
    saved = predict_unlabeled(repository, limit=10)
    metrics = evaluate_matching_model(repository)
    import joblib

    artifact = joblib.load(tmp_path / "model.joblib")

    with repository.engine.connect() as conn:
        models = conn.execute(product_match_models.select()).mappings().all()
        predictions = conn.execute(product_match_predictions.select()).mappings().all()

    candidates = repository.list_match_candidates(status="all", limit=10)
    predicted_candidate = next(candidate for candidate in candidates if candidate.id == 3)

    assert result.labels_count == 2
    assert result.positive_count == 1
    assert result.negative_count == 1
    assert result.artifact_path.endswith("model.joblib")
    assert artifact["features_version"] == "pair_features_v5"
    assert artifact["feature_names"][-2:] == RERANKER_FEATURE_NAMES
    assert artifact["semantic_embedding_model"] is None
    assert artifact["reranker_model"] is None
    assert saved == 1
    assert metrics["labels_count"] == 2
    assert len(models) == 1
    assert models[0]["active"] is True
    assert len(predictions) == 1
    assert predicted_candidate.model_match_probability is not None
    assert predicted_candidate.model_decision in {
        ProductMatchLabelValue.SAME,
        ProductMatchLabelValue.DIFFERENT,
        ProductMatchLabelValue.UNSURE,
    }


def test_training_enriches_legacy_feature_json(tmp_path: Path) -> None:
    repository = SearchRepository("sqlite+pysqlite:///:memory:")
    repository.init_schema()
    repository.seed_stores({"left": "Left", "right": "Right"})
    run_id = repository.create_scrape_run("iphone 15", "5800")
    legacy_features = {
        "token_overlap": 0.9,
        "rare_token_overlap": 0.8,
        "numeric_token_agreement": 1,
        "title_similarity": 0.9,
        "brand_agreement": 1,
        "category_agreement": 1,
        "accessory_mismatch": False,
        "price_ratio": 0.95,
    }
    rows = [
        _candidate_row(
            run_id,
            left_url="https://example.com/left/same",
            right_url="https://example.com/right/same",
            confidence=0.7,
            features=legacy_features,
            left_title="Apple iPhone 15 128GB",
            right_title="Apple iPhone 15 128 GB Negro",
        ),
        _candidate_row(
            run_id,
            left_url="https://example.com/left/different",
            right_url="https://example.com/right/different",
            confidence=0.7,
            features=legacy_features,
            left_title="Apple iPhone 15 128GB",
            right_title="Apple iPhone 15 Pro 256GB",
        ),
    ]
    with repository.engine.begin() as conn:
        conn.execute(product_match_candidates.insert(), rows)
    repository.label_match_candidate(1, ProductMatchLabelRequest(label=ProductMatchLabelValue.SAME, comment=None))
    repository.label_match_candidate(2, ProductMatchLabelRequest(label=ProductMatchLabelValue.DIFFERENT, comment=None))

    result = train_matching_model(repository, tmp_path / "model.joblib", min_labels=2, min_per_class=1)

    assert result.labels_count == 2
    assert result.metrics["evaluation"] == "training_set"


def _repository_with_candidates() -> SearchRepository:
    repository = SearchRepository("sqlite+pysqlite:///:memory:")
    repository.init_schema()
    repository.seed_stores({"left": "Left", "right": "Right"})
    run_id = repository.create_scrape_run("iphone 15", "5800")
    rows = [
        _candidate_row(
            run_id,
            left_url="https://example.com/left/same",
            right_url="https://example.com/right/same",
            confidence=0.7,
            features={
                "token_overlap": 0.9,
                "rare_token_overlap": 0.8,
                "numeric_token_agreement": 1,
                "title_similarity": 0.9,
                "brand_agreement": 1,
                "category_agreement": 1,
                "accessory_mismatch": False,
                "price_ratio": 0.95,
            },
        ),
        _candidate_row(
            run_id,
            left_url="https://example.com/left/different",
            right_url="https://example.com/right/different",
            confidence=0.4,
            features={
                "token_overlap": 0.1,
                "rare_token_overlap": 0.1,
                "numeric_token_agreement": 0,
                "title_similarity": 0.2,
                "brand_agreement": 0,
                "category_agreement": 0,
                "accessory_mismatch": True,
                "price_ratio": 0.2,
            },
        ),
        _candidate_row(
            run_id,
            left_url="https://example.com/left/unlabeled",
            right_url="https://example.com/right/unlabeled",
            confidence=0.55,
            features={
                "token_overlap": 0.5,
                "rare_token_overlap": 0.45,
                "numeric_token_agreement": 1,
                "title_similarity": 0.6,
                "brand_agreement": 1,
                "category_agreement": 1,
                "accessory_mismatch": False,
                "price_ratio": 0.85,
            },
        ),
    ]
    with repository.engine.begin() as conn:
        conn.execute(product_match_candidates.insert(), rows)
    return repository


def _candidate_row(
    run_id: int,
    left_url: str,
    right_url: str,
    confidence: float,
    features: dict,
    left_title: str = "left title",
    right_title: str = "right title",
) -> dict:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    return {
        "scrape_run_id": run_id,
        "query": "iphone 15",
        "left_store_id": "left",
        "left_title": left_title,
        "left_product_url": left_url,
        "left_canonical_key": left_url.rsplit("/", 1)[-1],
        "left_price": 1000,
        "right_store_id": "right",
        "right_title": right_title,
        "right_product_url": right_url,
        "right_canonical_key": right_url.rsplit("/", 1)[-1],
        "right_price": 1100,
        "features": features,
        "match_confidence": confidence,
        "label": None,
        "created_at": now,
        "updated_at": now,
    }
