from datetime import datetime, timezone

from app.database import SearchRepository, product_match_candidates
from app.matching_dataset import evaluate_campaign, sample_campaign
from app.matching_semantic import RerankerPairFeatureBuilder, SemanticPairFeatureBuilder
from app.models import ProductPairFeatures
from app.models import ProductMatchLabelValue


def test_matching_dataset_campaign_sampling_labeling_and_evaluation() -> None:
    repository = SearchRepository("sqlite+pysqlite:///:memory:")
    repository.init_schema()
    repository.seed_stores({"left": "Left", "right": "Right"})
    run_id = repository.create_scrape_run("iphone 15 128gb", "5800")
    with repository.engine.begin() as conn:
        conn.execute(product_match_candidates.insert(), [
            _candidate_row(run_id, 1, "Apple iPhone 15 128GB", "Apple iPhone 15 128 GB Negro", 0.62),
            _candidate_row(run_id, 2, "Apple iPhone 15 128GB", "Apple iPhone 15 Pro 128GB", 0.45),
            _candidate_row(run_id, 3, "Samsung Galaxy S24 FE 256GB", "Galaxy S24 FE 256GB + Galaxy Buds4", 0.50),
            _candidate_row(run_id, 4, 'Smart TV Samsung 50"', 'Smart TV Samsung 55"', 0.42),
            _candidate_row(run_id, 5, "Apple iPhone 14 128GB", "Apple iPhone 14 128 GB", 0.60),
            _candidate_row(run_id, 6, "Notebook i5 16GB 512GB", "Notebook HP i7 16GB 512GB", 0.48),
        ])

    repository.create_matching_dataset_campaign(
        name="matching-v3-test",
        description="test",
        queries=["iphone 15 128gb"],
        query_categories={"iphone 15 128gb": "smartphones_apple"},
        target_train_count=4,
        target_test_count=2,
    )
    added = repository.add_matching_dataset_items("matching-v3-test", [
        {
            "candidate_id": index,
            "query": "iphone 15 128gb",
            "category": "smartphones_apple",
            "selection_bucket": "pool",
            "split": "pool",
        }
        for index in range(1, 7)
    ])

    sample = sample_campaign(
        repository,
        "matching-v3-test",
        target_train_count=4,
        target_test_count=2,
        seed=7,
    )
    rows = repository.list_matching_dataset_rows("matching-v3-test")
    labels = {
        1: ProductMatchLabelValue.SAME.value,
        2: ProductMatchLabelValue.DIFFERENT.value,
        3: ProductMatchLabelValue.DIFFERENT.value,
        4: ProductMatchLabelValue.DIFFERENT.value,
        5: ProductMatchLabelValue.SAME.value,
        6: ProductMatchLabelValue.DIFFERENT.value,
    }
    for row in rows:
        repository.label_matching_dataset_item(row["id"], labels[row["candidate_id"]])

    report = evaluate_campaign(repository, "matching-v3-test")

    assert added == 6
    assert sample["selected_rows"] == 6
    assert sample["split_counts"] == {"test": 2, "train": 4}
    assert report["train_rows"] == 4
    assert report["test_rows"] == 2
    assert "0.95" in report["threshold_report"]
    assert report["features_version"] == "pair_features_v5"


def test_matching_dataset_evaluation_can_add_semantic_and_reranker_features(tmp_path) -> None:
    repository = SearchRepository("sqlite+pysqlite:///:memory:")
    repository.init_schema()
    repository.seed_stores({"left": "Left", "right": "Right"})
    run_id = repository.create_scrape_run("iphone 15 128gb", "5800")
    with repository.engine.begin() as conn:
        conn.execute(product_match_candidates.insert(), [
            _candidate_row(run_id, 1, "Apple iPhone 15 128GB", "Apple iPhone 15 128 GB Negro", 0.62),
            _candidate_row(run_id, 2, "Apple iPhone 15 128GB", "Apple iPhone 15 Pro 128GB", 0.45),
            _candidate_row(run_id, 3, "Samsung Galaxy S24 FE 256GB", "Samsung Galaxy S24 FE Negro", 0.50),
            _candidate_row(run_id, 4, 'Smart TV Samsung 50"', 'Smart TV Samsung 55"', 0.42),
        ])
    repository.create_matching_dataset_campaign(
        name="matching-semantic-test",
        description="test",
        queries=["iphone 15 128gb"],
        query_categories={"iphone 15 128gb": "smartphones_apple"},
        target_train_count=2,
        target_test_count=2,
    )
    repository.add_matching_dataset_items("matching-semantic-test", [
        {
            "candidate_id": index,
            "query": "iphone 15 128gb",
            "category": "smartphones_apple",
            "selection_bucket": "pool",
            "split": "train" if index in {1, 2} else "test",
        }
        for index in range(1, 5)
    ])
    labels = {
        1: ProductMatchLabelValue.SAME.value,
        2: ProductMatchLabelValue.DIFFERENT.value,
        3: ProductMatchLabelValue.SAME.value,
        4: ProductMatchLabelValue.DIFFERENT.value,
    }
    for row in repository.list_matching_dataset_rows("matching-semantic-test"):
        repository.label_matching_dataset_item(row["id"], labels[row["candidate_id"]])

    report = evaluate_campaign(
        repository,
        "matching-semantic-test",
        artifact_path=str(tmp_path / "model-v5.joblib"),
        semantic_builder=SemanticPairFeatureBuilder(FakeEmbedder()),
        semantic_model="fake-local-model",
        reranker_builder=FakeRerankerBuilder(),
        reranker_model="fake-reranker",
    )

    assert report["semantic_embedding_model"] == "fake-local-model"
    assert report["reranker_model"] == "fake-reranker"
    assert "title_embedding_similarity" in report["feature_names"]
    assert "reranker_score_raw_avg" in report["feature_names"]
    assert "reranker_score_same_query_avg" in report["feature_names"]

    import joblib

    artifact = joblib.load(tmp_path / "model-v5.joblib")
    assert artifact["features_version"] == "pair_features_v5"
    assert artifact["semantic_embedding_model"] == "fake-local-model"
    assert artifact["reranker_model"] == "fake-reranker"


def test_freeze_requires_labeled_selected_rows() -> None:
    repository = SearchRepository("sqlite+pysqlite:///:memory:")
    repository.init_schema()
    repository.seed_stores({"left": "Left", "right": "Right"})
    run_id = repository.create_scrape_run("iphone 15 128gb", "5800")
    with repository.engine.begin() as conn:
        conn.execute(product_match_candidates.insert(), [
            _candidate_row(run_id, 1, "Apple iPhone 15 128GB", "Apple iPhone 15 128 GB Negro", 0.62),
        ])
    repository.create_matching_dataset_campaign(
        name="matching-v3-freeze-test",
        description=None,
        queries=["iphone 15 128gb"],
        query_categories={"iphone 15 128gb": "smartphones_apple"},
    )
    repository.add_matching_dataset_items("matching-v3-freeze-test", [{
        "candidate_id": 1,
        "query": "iphone 15 128gb",
        "category": "smartphones_apple",
        "selection_bucket": "random",
        "split": "test",
    }])

    from app.matching_dataset import freeze_campaign

    try:
        freeze_campaign(repository, "matching-v3-freeze-test")
    except RuntimeError as exc:
        assert "unlabeled" in str(exc)
    else:
        raise AssertionError("freeze should fail when selected rows are unlabeled")


def _candidate_row(run_id: int, suffix: int, left_title: str, right_title: str, confidence: float) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "scrape_run_id": run_id,
        "query": "iphone 15 128gb",
        "left_store_id": "left",
        "left_title": left_title,
        "left_product_url": f"https://example.com/left/{suffix}",
        "left_canonical_key": left_title.lower().replace(" ", "-"),
        "left_price": 1000 + suffix,
        "right_store_id": "right",
        "right_title": right_title,
        "right_product_url": f"https://example.com/right/{suffix}",
        "right_canonical_key": right_title.lower().replace(" ", "-"),
        "right_price": 1100 + suffix,
        "features": {
            "token_overlap": 0.5,
            "rare_token_overlap": 0.5,
            "numeric_token_agreement": 1,
            "title_similarity": 0.6,
            "brand_agreement": 1,
            "category_agreement": 1,
            "accessory_mismatch": False,
            "price_ratio": 0.9,
        },
        "match_confidence": confidence,
        "label": None,
        "created_at": now,
        "updated_at": now,
    }


class FakeEmbedder:
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            lowered = text.lower()
            if "iphone 15 pro" in lowered or '55"' in lowered:
                vectors.append([0.0, 1.0])
            elif "iphone" in lowered or "s24 fe" in lowered:
                vectors.append([1.0, 0.0])
            else:
                vectors.append([0.5, 0.5])
        return vectors


class FakeRerankerBuilder(RerankerPairFeatureBuilder):
    def __init__(self) -> None:
        pass

    def enrich_many(self, pairs, base_features) -> list[ProductPairFeatures]:
        enriched = []
        for pair, features in zip(pairs, base_features):
            same_product = "pro" not in pair.right_title.lower() and '55"' not in pair.right_title
            enriched.append(features.model_copy(update={
                "reranker_score_raw_avg": 0.8 if same_product else 0.2,
                "reranker_score_same_query_avg": 0.9 if same_product else 0.1,
            }))
        return enriched
