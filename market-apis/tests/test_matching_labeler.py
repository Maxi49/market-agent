from datetime import datetime, timezone

from app.database import SearchRepository, product_match_candidates
from app.matching_labeler import (
    format_candidate,
    format_stats,
    parse_review_command,
    review_candidates,
    training_readiness,
)
from app.models import ProductMatchLabelValue


def test_parse_review_command_maps_short_inputs() -> None:
    assert parse_review_command("s") == ProductMatchLabelValue.SAME
    assert parse_review_command("D") == ProductMatchLabelValue.DIFFERENT
    assert parse_review_command(" unsure ") == ProductMatchLabelValue.UNSURE
    assert parse_review_command("k") == "skip"
    assert parse_review_command("o") == "open"
    assert parse_review_command("q") == "quit"
    assert parse_review_command("x") is None


def test_training_readiness_counts_only_same_and_different() -> None:
    repository = _repository_with_labeling_candidates()
    candidates = repository.list_match_candidates(status="all", limit=10)
    repository.label_match_candidate(candidates[0].id, _request(ProductMatchLabelValue.SAME))
    repository.label_match_candidate(candidates[1].id, _request(ProductMatchLabelValue.DIFFERENT))
    repository.label_match_candidate(candidates[2].id, _request(ProductMatchLabelValue.UNSURE))

    summary = repository.get_match_summary()
    readiness = training_readiness(summary)
    stats = format_stats(summary)

    assert readiness.useful_labels == 2
    assert readiness.same_count == 1
    assert readiness.different_count == 1
    assert readiness.ready is False
    assert "training_status: not_ready" in stats
    assert "labels.unsure: 1" in stats


def test_list_match_candidates_filters_by_query_and_run_id() -> None:
    repository = _repository_with_labeling_candidates()
    all_candidates = repository.list_match_candidates(status="all", limit=10)
    iphone_candidates = repository.list_match_candidates(
        status="all",
        limit=10,
        query_text="iphone 15",
    )
    run_candidates = repository.list_match_candidates(
        status="all",
        limit=10,
        run_id=all_candidates[0].run_id,
    )

    assert len(all_candidates) == 3
    assert len(iphone_candidates) == 2
    assert all(candidate.query == "iphone 15" for candidate in iphone_candidates)
    assert len(run_candidates) == 2
    assert all(candidate.run_id == all_candidates[0].run_id for candidate in run_candidates)


def test_review_candidates_labels_from_simulated_input() -> None:
    repository = _repository_with_labeling_candidates()
    outputs: list[str] = []
    inputs = iter(["s", "k", "q"])

    saved = review_candidates(
        repository,
        limit=3,
        input_func=lambda _prompt: next(inputs),
        output_func=outputs.append,
    )
    summary = repository.get_match_summary()

    assert saved == 1
    assert summary.labels_by_value == {"same": 1}
    assert any("Saved label same" in output for output in outputs)


def test_format_candidate_handles_missing_prices() -> None:
    repository = _repository_with_labeling_candidates()
    candidate = repository.list_match_candidates(status="all", limit=10)[-1]

    rendered = format_candidate(candidate, 1, 1)

    assert "price: -" in rendered
    assert "candidate_id=" in rendered
    assert "FEATURES" in rendered


def _repository_with_labeling_candidates() -> SearchRepository:
    repository = SearchRepository("sqlite+pysqlite:///:memory:")
    repository.init_schema()
    repository.seed_stores({"left": "Left", "right": "Right"})
    iphone_run = repository.create_scrape_run("iphone 15", "5800")
    tv_run = repository.create_scrape_run("smart tv 55", "5800")
    with repository.engine.begin() as conn:
        conn.execute(product_match_candidates.insert(), [
            _candidate_row(iphone_run, "iphone 15", "a", 0.49, 1000, 1100),
            _candidate_row(iphone_run, "iphone 15", "b", 0.51, 1000, 1200),
            _candidate_row(tv_run, "smart tv 55", "c", 0.60, None, None),
        ])
    return repository


def _candidate_row(
    run_id: int,
    query: str,
    suffix: str,
    confidence: float,
    left_price: float | None,
    right_price: float | None,
) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "scrape_run_id": run_id,
        "query": query,
        "left_store_id": "left",
        "left_title": f"left title {suffix}",
        "left_product_url": f"https://example.com/left/{suffix}",
        "left_canonical_key": f"left-{suffix}",
        "left_price": left_price,
        "right_store_id": "right",
        "right_title": f"right title {suffix}",
        "right_product_url": f"https://example.com/right/{suffix}",
        "right_canonical_key": f"right-{suffix}",
        "right_price": right_price,
        "features": {
            "token_overlap": 0.5,
            "rare_token_overlap": 0.4,
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


def _request(label: ProductMatchLabelValue):
    from app.models import ProductMatchLabelRequest

    return ProductMatchLabelRequest(label=label, comment=None)
