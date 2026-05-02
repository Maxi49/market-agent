from app.database import SearchRepository


def test_semantic_matcher_returns_similar_canonical_when_embedding_exists() -> None:
    repository = SearchRepository("sqlite+pysqlite:///:memory:")
    repository.init_schema()
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    with repository.engine.begin() as conn:
        conn.execute(
            __import__("app.database").database.canonical_products.insert(),
            [
                {
                    "canonical_key": "iphone-15",
                    "normalized_title": "Apple iPhone 15",
                    "attributes": {},
                    "embedding": [1.0, 0.0],
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "canonical_key": "iphone-15-alt",
                    "normalized_title": "Celular iPhone 15",
                    "attributes": {},
                    "embedding": [0.99, 0.01],
                    "created_at": now,
                    "updated_at": now,
                },
            ],
        )

    match = repository.find_semantic_match("iphone-15", [1.0, 0.0])

    assert match is not None
    assert match.canonical_key == "iphone-15-alt"
    assert match.score > 0.99
