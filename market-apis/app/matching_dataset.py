from __future__ import annotations

import argparse
import asyncio
import json
import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import httpx

from app.config import get_settings
from app.database import OptionalRepository, build_repository
from app.matching import build_pair_features_from_values
from app.matching_model import (
    FEATURE_NAMES,
    FEATURES_VERSION,
    _features_from_candidates,
    _build_estimator,
    _classification_metrics,
    _load_ml,
    decision_from_probability,
    vectorize_features,
)
from app.matching_semantic import (
    DEFAULT_RERANKER_CACHE_PATH,
    DEFAULT_RERANKER_MODEL,
    DEFAULT_SEMANTIC_CACHE_PATH,
    DEFAULT_SENTENCE_TRANSFORMER_MODEL,
    PairTextValues,
    RerankerPairFeatureBuilder,
    SemanticPairFeatureBuilder,
    build_local_reranker_feature_builder,
    build_local_semantic_feature_builder,
    resolve_reranker_model_name,
    resolve_semantic_model_name,
)
from app.models import ProductMatchLabelValue, ProductPairFeatures, SearchMode
from app.scrapers.registry import build_store_registry
from app.services import SearchService

DEFAULT_QUERY_CATEGORIES = {
    "iphone 13 128gb": "smartphones_apple",
    "iphone 14 128gb": "smartphones_apple",
    "iphone 15 128gb": "smartphones_apple",
    "iphone 15 pro 128gb": "smartphones_apple",
    "iphone 15 pro max 256gb": "smartphones_apple",
    "galaxy s23 128gb": "smartphones_samsung",
    "galaxy s24 256gb": "smartphones_samsung",
    "galaxy s24 fe 256gb": "smartphones_samsung",
    "galaxy s24 ultra 256gb": "smartphones_samsung",
    "galaxy a55 256gb": "smartphones_samsung",
    "smart tv 43": "tv",
    "smart tv 50": "tv",
    "smart tv 55": "tv",
    "smart tv 65": "tv",
    "smart tv samsung 55": "tv",
    "notebook i5 16gb": "notebooks",
    "notebook i7 16gb": "notebooks",
    "notebook ryzen 5 16gb": "notebooks",
    "notebook gamer rtx 4050": "notebooks",
    "macbook air m1": "notebooks",
    "heladera no frost": "home_appliances",
    "lavarropas automatico": "home_appliances",
    "microondas 20 litros": "home_appliances",
    "aire acondicionado 3000 frigorias": "home_appliances",
    "freezer vertical": "home_appliances",
    "funda iphone 15": "accessories_bundles",
    "cargador iphone 15": "accessories_bundles",
    "galaxy buds": "accessories_bundles",
    "smart tv barra sonido": "accessories_bundles",
    "combo samsung s24": "accessories_bundles",
}
TARGET_BUCKET_RATIOS = {
    "uncertainty": 0.40,
    "high_risk": 0.30,
    "random": 0.20,
    "deliberate": 0.10,
}


@dataclass(frozen=True)
class CampaignBuildResult:
    campaign_name: str
    queries_run: int
    items_added: int
    errors: list[str]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and evaluate matching dataset campaigns.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build-campaign")
    build.add_argument("--name", required=True)
    build.add_argument("--description", default=None)
    build.add_argument("--queries-file", default=None)
    build.add_argument("--mode", choices=[mode.value for mode in SearchMode], default=SearchMode.INTERACTIVE.value)
    build.add_argument("--limit", type=int, default=8)
    build.add_argument("--target-train", type=int, default=200)
    build.add_argument("--target-test", type=int, default=100)

    sample = subparsers.add_parser("sample-campaign")
    sample.add_argument("--name", required=True)
    sample.add_argument("--target-train", type=int, default=200)
    sample.add_argument("--target-test", type=int, default=100)
    sample.add_argument("--seed", type=int, default=42)

    review = subparsers.add_parser("review")
    review.add_argument("--name", required=True)
    review.add_argument("--split", choices=["pool", "train", "test", "all"], default="all")
    review.add_argument("--limit", type=int, default=50)

    label = subparsers.add_parser("label")
    label.add_argument("--item-id", type=int, required=True)
    label.add_argument("--label", choices=[value.value for value in ProductMatchLabelValue], required=True)
    label.add_argument("--reason", default=None)
    label.add_argument("--source", default="human_assisted")

    freeze = subparsers.add_parser("freeze")
    freeze.add_argument("--name", required=True)
    freeze.add_argument("--allow-incomplete", action="store_true")

    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument("--name", required=True)
    evaluate.add_argument("--artifact-path", default=None)
    evaluate.add_argument("--semantic-model", default=None)
    evaluate.add_argument("--semantic-cache-path", default=str(DEFAULT_SEMANTIC_CACHE_PATH))
    evaluate.add_argument("--reranker-model", default=None)
    evaluate.add_argument("--reranker-cache-path", default=str(DEFAULT_RERANKER_CACHE_PATH))

    summary = subparsers.add_parser("summary")
    summary.add_argument("--name", required=True)

    args = parser.parse_args()
    repository = build_repository(get_settings().database_url)
    repository.init_schema()

    if args.command == "build-campaign":
        result = asyncio.run(
            build_campaign(
                repository,
                name=args.name,
                description=args.description,
                query_categories=_load_query_categories(args.queries_file),
                mode=SearchMode(args.mode),
                limit=args.limit,
                target_train_count=args.target_train,
                target_test_count=args.target_test,
            )
        )
        print(json.dumps(result.__dict__, indent=2, sort_keys=True))
    elif args.command == "sample-campaign":
        result = sample_campaign(
            repository,
            args.name,
            target_train_count=args.target_train,
            target_test_count=args.target_test,
            seed=args.seed,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
    elif args.command == "review":
        review_campaign(repository, args.name, split=args.split, limit=args.limit)
    elif args.command == "label":
        saved = repository.label_matching_dataset_item(
            args.item_id,
            args.label,
            label_source=args.source,
            label_reason=args.reason,
        )
        print(json.dumps({"saved": saved}, sort_keys=True))
    elif args.command == "freeze":
        freeze_campaign(repository, args.name, allow_incomplete=args.allow_incomplete)
        print(json.dumps({"frozen": args.name}, sort_keys=True))
    elif args.command == "evaluate":
        semantic_builder = (
            build_local_semantic_feature_builder(
                model_name=args.semantic_model or DEFAULT_SENTENCE_TRANSFORMER_MODEL,
                cache_path=Path(args.semantic_cache_path),
            )
            if args.semantic_model
            else None
        )
        semantic_model = resolve_semantic_model_name(args.semantic_model) if args.semantic_model else None
        reranker_builder = (
            build_local_reranker_feature_builder(
                model_name=args.reranker_model or DEFAULT_RERANKER_MODEL,
                cache_path=Path(args.reranker_cache_path),
            )
            if args.reranker_model
            else None
        )
        reranker_model = resolve_reranker_model_name(args.reranker_model) if args.reranker_model else None
        report = evaluate_campaign(
            repository,
            args.name,
            artifact_path=args.artifact_path,
            semantic_builder=semantic_builder,
            semantic_model=semantic_model,
            reranker_builder=reranker_builder,
            reranker_model=reranker_model,
        )
        print(json.dumps(report, indent=2, sort_keys=True))
    elif args.command == "summary":
        print(json.dumps(repository.matching_dataset_summary(args.name), indent=2, default=str, sort_keys=True))


async def build_campaign(
    repository,
    *,
    name: str,
    description: str | None,
    query_categories: dict[str, str],
    mode: SearchMode,
    limit: int,
    target_train_count: int,
    target_test_count: int,
) -> CampaignBuildResult:
    repository.create_matching_dataset_campaign(
        name=name,
        description=description,
        queries=list(query_categories),
        query_categories=query_categories,
        target_train_count=target_train_count,
        target_test_count=target_test_count,
    )
    settings = get_settings()
    errors: list[str] = []
    items_added = 0
    async with httpx.AsyncClient(
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        timeout=httpx.Timeout(8.0, connect=3.0, read=8.0, write=3.0, pool=1.0),
    ) as client:
        registry = build_store_registry(client)
        adapters = [
            adapter
            for store_id, adapter in registry.items()
            if store_id in settings.active_store_ids
        ]
        repository.seed_stores({adapter.store_id: adapter.store_name for adapter in adapters})
        service = SearchService(
            adapters=adapters,
            repository=OptionalRepository(repository),
            location=settings.default_location,
            semantic_enabled=False,
            worker=None,
        )
        for query, category in query_categories.items():
            try:
                response = await service.agent_search(query=query, limit=limit, mode=mode)
                if response.debug_ref is None:
                    errors.append(f"{query}: missing debug_ref")
                    continue
                candidates = repository.list_match_candidates(
                    status="all",
                    limit=200,
                    query_text=query,
                    run_id=response.debug_ref,
                )
                prediction_fields = _predict_candidate_fields(repository, candidates)
                rows = [
                    _dataset_item_from_candidate(
                        candidate,
                        category,
                        prediction_fields.get(candidate.id),
                    )
                    for candidate in candidates
                ]
                items_added += repository.add_matching_dataset_items(name, rows)
            except Exception as exc:
                errors.append(f"{query}: {exc}")
    return CampaignBuildResult(
        campaign_name=name,
        queries_run=len(query_categories),
        items_added=items_added,
        errors=errors,
    )


def sample_campaign(
    repository,
    campaign_name: str,
    *,
    target_train_count: int,
    target_test_count: int,
    seed: int = 42,
) -> dict:
    rng = random.Random(seed)
    rows = repository.list_matching_dataset_rows(campaign_name)
    unique_rows = _dedupe_rows(rows)
    for row in unique_rows:
        row["_bucket"] = _selection_bucket(row)
    target_total = target_train_count + target_test_count
    selected = _select_bucketed(unique_rows, target_total, rng)
    assignments = _split_selected(selected, target_train_count, target_test_count, rng)
    updated = repository.update_matching_dataset_item_splits(campaign_name, assignments)
    return {
        "campaign_name": campaign_name,
        "pool_rows": len(rows),
        "unique_rows": len(unique_rows),
        "selected_rows": len(assignments),
        "updated_rows": updated,
        "split_counts": dict(Counter(split for _, split, _ in assignments)),
        "bucket_counts": dict(Counter(bucket for _, _, bucket in assignments)),
    }


def review_campaign(repository, campaign_name: str, *, split: str, limit: int) -> None:
    selected_split = None if split == "all" else split
    rows = repository.list_matching_dataset_rows(
        campaign_name,
        split=selected_split,
        labeled=False,
        limit=limit,
    )
    if not rows:
        print("No campaign rows found.")
        return
    for index, row in enumerate(rows, start=1):
        print(_format_campaign_row(row, index, len(rows)))
        while True:
            raw = input("[s]ame [d]ifferent [u]nsure s[k]ip [q]uit > ").strip().lower()
            if raw in {"q", "quit"}:
                return
            if raw in {"k", "skip", ""}:
                break
            label = {
                "s": "same",
                "same": "same",
                "d": "different",
                "different": "different",
                "u": "unsure",
                "unsure": "unsure",
            }.get(raw)
            if label is None:
                print("Invalid command.")
                continue
            reason = input("reason > ").strip() or None
            repository.label_matching_dataset_item(
                row["id"],
                label,
                label_source="human_assisted",
                label_reason=reason,
            )
            print(f"Saved {label} for campaign item {row['id']}.")
            break


def freeze_campaign(repository, campaign_name: str, *, allow_incomplete: bool = False) -> None:
    rows = repository.list_matching_dataset_rows(campaign_name)
    selected = [row for row in rows if row["split"] in {"train", "test"}]
    unlabeled = [row for row in selected if row["label"] is None]
    if unlabeled and not allow_incomplete:
        raise RuntimeError(f"Cannot freeze campaign with {len(unlabeled)} unlabeled train/test items.")
    repository.freeze_matching_dataset_campaign(campaign_name)


def evaluate_campaign(
    repository,
    campaign_name: str,
    artifact_path: str | None = None,
    semantic_builder: SemanticPairFeatureBuilder | None = None,
    semantic_model: str | None = None,
    reranker_builder: RerankerPairFeatureBuilder | None = None,
    reranker_model: str | None = None,
) -> dict:
    train_rows = _labeled_binary_rows(
        repository.list_matching_dataset_rows(campaign_name, split="train", labeled=True)
    )
    test_rows = _labeled_binary_rows(
        repository.list_matching_dataset_rows(campaign_name, split="test", labeled=True)
    )
    if not train_rows or not test_rows:
        raise RuntimeError("Campaign needs labeled same/different rows in both train and test splits.")

    ml = _load_ml()
    x_train, y_train = _matrix_from_campaign_rows(
        train_rows,
        semantic_builder=semantic_builder,
        reranker_builder=reranker_builder,
    )
    x_test, y_test = _matrix_from_campaign_rows(
        test_rows,
        semantic_builder=semantic_builder,
        reranker_builder=reranker_builder,
    )
    estimator, algorithm = _build_estimator(ml, len(y_train), sum(y_train), len(y_train) - sum(y_train))
    estimator.fit(x_train, y_train)
    probabilities = estimator.predict_proba(x_test)[:, 1]
    threshold_report = {
        str(threshold): _metrics_at_threshold(y_test, probabilities, threshold)
        for threshold in [0.5, 0.8, 0.9, 0.95]
    }
    predictions = [1 if probability >= 0.5 else 0 for probability in probabilities]
    metrics = _classification_metrics(y_test, predictions, probabilities)
    report = {
        "campaign_name": campaign_name,
        "algorithm": algorithm,
        "features_version": FEATURES_VERSION,
        "feature_names": FEATURE_NAMES,
        "semantic_embedding_model": semantic_model,
        "reranker_model": reranker_model,
        "train_rows": len(train_rows),
        "test_rows": len(test_rows),
        "train_labels": dict(Counter(row["label"] for row in train_rows)),
        "test_labels": dict(Counter(row["label"] for row in test_rows)),
        "metrics_at_0_5": metrics,
        "threshold_report": threshold_report,
        "calibration_buckets": _calibration_buckets(y_test, probabilities),
    }
    if artifact_path:
        path = Path(artifact_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        ml.joblib.dump(
            {
                "version": f"{campaign_name}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
                "algorithm": algorithm,
                "features_version": FEATURES_VERSION,
                "feature_names": FEATURE_NAMES,
                "estimator": estimator,
                "campaign_name": campaign_name,
                "semantic_embedding_model": semantic_model,
                "reranker_model": reranker_model,
                "frozen_test_metrics": report,
            },
            path,
        )
        report["artifact_path"] = str(path)
    return report


def _load_query_categories(path: str | None) -> dict[str, str]:
    if path is None:
        return dict(DEFAULT_QUERY_CATEGORIES)
    data = json.loads(Path(path).read_text())
    if isinstance(data, dict):
        return {str(query): str(category) for query, category in data.items()}
    if isinstance(data, list):
        return {str(item["query"]): str(item["category"]) for item in data}
    raise ValueError("queries file must be a JSON object or a list of query/category objects")


def _predict_candidate_fields(repository, candidates: Sequence) -> dict[int, dict]:
    if not candidates:
        return {}
    model_row = repository.get_active_match_model()
    if model_row is None:
        return {}
    ml = _load_ml()
    bundle = ml.joblib.load(model_row["artifact_path"])
    feature_names = bundle.get("feature_names") or FEATURE_NAMES
    semantic_model = bundle.get("semantic_embedding_model")
    semantic_builder = (
        build_local_semantic_feature_builder(model_name=semantic_model)
        if semantic_model
        else None
    )
    reranker_model = bundle.get("reranker_model")
    reranker_builder = (
        build_local_reranker_feature_builder(model_name=reranker_model)
        if reranker_model
        else None
    )
    features = _features_from_candidates(
        candidates,
        semantic_builder=semantic_builder,
        reranker_builder=reranker_builder,
    )
    probabilities = bundle["estimator"].predict_proba([
        vectorize_features(feature, feature_names)
        for feature in features
    ])[:, 1]
    return {
        candidate.id: {
            "model_version": model_row["version"],
            "model_match_probability": round(float(probability), 6),
            "model_decision": decision_from_probability(float(probability)),
        }
        for candidate, probability in zip(candidates, probabilities)
    }


def _dataset_item_from_candidate(candidate, category: str, prediction: dict | None = None) -> dict:
    prediction = prediction or {}
    return {
        "candidate_id": candidate.id,
        "query": candidate.query,
        "category": category,
        "selection_bucket": "pool",
        "split": "pool",
        "model_version": prediction.get("model_version") or candidate.model_version,
        "model_match_probability": (
            prediction.get("model_match_probability")
            if prediction.get("model_match_probability") is not None
            else candidate.model_match_probability
        ),
        "model_decision": (
            prediction.get("model_decision")
            or (candidate.model_decision.value if candidate.model_decision else None)
        ),
    }


def _dedupe_rows(rows: Sequence[dict]) -> list[dict]:
    seen_pairs: set[tuple[str, str]] = set()
    unique_rows: list[dict] = []
    for row in rows:
        pair_key = tuple(sorted([row["left_product_url"], row["right_product_url"]]))
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)
        unique_rows.append(row)
    return unique_rows


def _selection_bucket(row: dict) -> str:
    features = _features_from_row(row)
    probability = row["model_match_probability"]
    if features.bundle_conflict or row["category"] == "accessories_bundles":
        return "deliberate"
    if probability is not None and 0.2 < probability < 0.8:
        return "uncertainty"
    if probability is not None and probability >= 0.8 and _has_conflict(features):
        return "high_risk"
    if _has_conflict(features):
        return "high_risk"
    return "random"


def _select_bucketed(rows: list[dict], target_total: int, rng: random.Random) -> list[dict]:
    by_bucket: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_bucket[row["_bucket"]].append(row)
    for bucket_rows in by_bucket.values():
        rng.shuffle(bucket_rows)
    selected: list[dict] = []
    selected_ids: set[int] = set()
    for bucket, ratio in TARGET_BUCKET_RATIOS.items():
        quota = int(target_total * ratio)
        for row in by_bucket.get(bucket, [])[:quota]:
            selected.append(row)
            selected_ids.add(row["id"])
    leftovers = [row for row in rows if row["id"] not in selected_ids]
    rng.shuffle(leftovers)
    selected.extend(leftovers[: max(0, target_total - len(selected))])
    return selected[:target_total]


def _split_selected(
    rows: list[dict],
    target_train_count: int,
    target_test_count: int,
    rng: random.Random,
) -> list[tuple[int, str, str]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[_group_key(row)].append(row)
    grouped_rows = list(groups.values())
    rng.shuffle(grouped_rows)

    test_ids: set[int] = set()
    train_ids: set[int] = set()
    for group in grouped_rows:
        group_size = len(group)
        target = (
            test_ids
            if len(test_ids) + group_size <= target_test_count
            else train_ids
        )
        for row in group:
            target.add(row["id"])
    if len(test_ids) < target_test_count:
        for group in grouped_rows:
            if any(row["id"] in test_ids for row in group):
                continue
            if len(test_ids) + len(group) > target_test_count:
                continue
            for row in group:
                if row["id"] in train_ids:
                    train_ids.remove(row["id"])
                test_ids.add(row["id"])
            if len(test_ids) >= target_test_count:
                break
    for row in rows:
        if row["id"] not in test_ids and len(train_ids) < target_train_count:
            train_ids.add(row["id"])

    assignments: list[tuple[int, str, str]] = []
    for row in rows:
        split = "test" if row["id"] in test_ids else "train"
        if split == "train" and len([item for item in assignments if item[1] == "train"]) >= target_train_count:
            continue
        if split == "test" and len([item for item in assignments if item[1] == "test"]) >= target_test_count:
            continue
        assignments.append((row["id"], split, row["_bucket"]))
    return assignments


def _group_key(row: dict) -> str:
    keys = sorted([row["left_canonical_key"], row["right_canonical_key"]])
    return f"{row['category']}|{keys[0]}|{keys[1]}"


def _features_from_row(row: dict) -> ProductPairFeatures:
    base_features = ProductPairFeatures(**(row["features"] or {}))
    return build_pair_features_from_values(
        left_title=row["left_title"],
        right_title=row["right_title"],
        left_price=row["left_price"],
        right_price=row["right_price"],
        left_canonical_key=row["left_canonical_key"],
        right_canonical_key=row["right_canonical_key"],
        base_features=base_features,
    )


def _has_conflict(features: ProductPairFeatures) -> bool:
    return any([
        features.accessory_mismatch,
        features.model_suffix_conflict,
        features.storage_conflict,
        features.screen_size_conflict,
        features.bundle_conflict,
    ])


def _format_campaign_row(row: dict, index: int, total: int) -> str:
    features = _features_from_row(row)
    probability = row["model_match_probability"]
    return "\n".join([
        "",
        f"[{index}/{total}] item_id={row['id']} candidate_id={row['candidate_id']} split={row['split']} bucket={row['selection_bucket']}",
        f"query={row['query']!r} category={row['category']} label={row['label'] or '-'}",
        f"model_probability={probability if probability is not None else '-'} model_decision={row['model_decision'] or '-'}",
        "",
        "LEFT",
        f"  store: {row['left_store_id']}",
        f"  title: {row['left_title']}",
        f"  key:   {row['left_canonical_key']}",
        f"  url:   {row['left_product_url']}",
        "",
        "RIGHT",
        f"  store: {row['right_store_id']}",
        f"  title: {row['right_title']}",
        f"  key:   {row['right_canonical_key']}",
        f"  url:   {row['right_product_url']}",
        "",
        "FEATURES",
        f"  overlap={features.token_overlap:.4f} rare={features.rare_token_overlap:.4f} numeric={features.numeric_token_agreement:.4f}",
        f"  suffix_conflict={features.model_suffix_conflict} storage_conflict={features.storage_conflict}",
        f"  screen_size_conflict={features.screen_size_conflict} bundle_conflict={features.bundle_conflict}",
    ])


def _labeled_binary_rows(rows: list[dict]) -> list[dict]:
    return [
        row
        for row in rows
        if row["label"] in {ProductMatchLabelValue.SAME.value, ProductMatchLabelValue.DIFFERENT.value}
    ]


def _matrix_from_campaign_rows(
    rows: list[dict],
    *,
    semantic_builder: SemanticPairFeatureBuilder | None = None,
    reranker_builder: RerankerPairFeatureBuilder | None = None,
) -> tuple[list[list[float]], list[int]]:
    raw_features = [_features_from_row(row) for row in rows]
    pairs = [
        PairTextValues(
            left_title=row["left_title"],
            right_title=row["right_title"],
            left_canonical_key=row["left_canonical_key"],
            right_canonical_key=row["right_canonical_key"],
        )
        for row in rows
    ]
    if semantic_builder is not None:
        raw_features = semantic_builder.enrich_many(pairs, raw_features)
    if reranker_builder is not None:
        raw_features = reranker_builder.enrich_many(pairs, raw_features)
    features = [vectorize_features(feature) for feature in raw_features]
    labels = [1 if row["label"] == ProductMatchLabelValue.SAME.value else 0 for row in rows]
    return features, labels


def _metrics_at_threshold(labels: list[int], probabilities, threshold: float) -> dict:
    predictions = [1 if probability >= threshold else 0 for probability in probabilities]
    metrics = _classification_metrics(labels, predictions, probabilities)
    metrics["threshold"] = threshold
    return metrics


def _calibration_buckets(labels: list[int], probabilities) -> list[dict]:
    buckets: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for label, probability in zip(labels, probabilities):
        bucket_floor = min(9, int(float(probability) * 10)) / 10
        bucket = f"{bucket_floor:.1f}-{bucket_floor + 0.1:.1f}"
        buckets[bucket].append((label, float(probability)))
    return [
        {
            "bucket": bucket,
            "count": len(values),
            "average_probability": round(sum(prob for _, prob in values) / len(values), 4),
            "empirical_positive_rate": round(sum(label for label, _ in values) / len(values), 4),
        }
        for bucket, values in sorted(buckets.items())
    ]


if __name__ == "__main__":
    main()
