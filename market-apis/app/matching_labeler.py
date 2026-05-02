from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable

from app.config import get_settings
from app.database import build_repository
from app.models import (
    ProductMatchCandidate,
    ProductMatchLabelRequest,
    ProductMatchLabelValue,
    ProductMatchSummary,
)

TRAINING_MIN_LABELS = 50
TRAINING_MIN_PER_CLASS = 10
RECOMMENDED_PER_CLASS = 30

LabelInput = ProductMatchLabelValue | str


@dataclass(frozen=True)
class TrainingReadiness:
    ready: bool
    useful_labels: int
    same_count: int
    different_count: int
    missing_total: int
    missing_same: int
    missing_different: int
    recommended_missing_same: int
    recommended_missing_different: int


def parse_review_command(raw: str) -> ProductMatchLabelValue | str | None:
    command = raw.strip().lower()
    mapping = {
        "s": ProductMatchLabelValue.SAME,
        "same": ProductMatchLabelValue.SAME,
        "d": ProductMatchLabelValue.DIFFERENT,
        "different": ProductMatchLabelValue.DIFFERENT,
        "u": ProductMatchLabelValue.UNSURE,
        "unsure": ProductMatchLabelValue.UNSURE,
        "k": "skip",
        "skip": "skip",
        "o": "open",
        "open": "open",
        "q": "quit",
        "quit": "quit",
    }
    return mapping.get(command)


def training_readiness(summary: ProductMatchSummary) -> TrainingReadiness:
    same_count = summary.labels_by_value.get(ProductMatchLabelValue.SAME.value, 0)
    different_count = summary.labels_by_value.get(ProductMatchLabelValue.DIFFERENT.value, 0)
    useful_labels = same_count + different_count
    missing_total = max(0, TRAINING_MIN_LABELS - useful_labels)
    missing_same = max(0, TRAINING_MIN_PER_CLASS - same_count)
    missing_different = max(0, TRAINING_MIN_PER_CLASS - different_count)
    return TrainingReadiness(
        ready=missing_total == 0 and missing_same == 0 and missing_different == 0,
        useful_labels=useful_labels,
        same_count=same_count,
        different_count=different_count,
        missing_total=missing_total,
        missing_same=missing_same,
        missing_different=missing_different,
        recommended_missing_same=max(0, RECOMMENDED_PER_CLASS - same_count),
        recommended_missing_different=max(0, RECOMMENDED_PER_CLASS - different_count),
    )


def format_stats(summary: ProductMatchSummary) -> str:
    readiness = training_readiness(summary)
    status = "ready" if readiness.ready else "not_ready"
    lines = [
        "Matching Labeling Stats",
        f"total_candidates: {summary.total_candidates}",
        f"unlabeled_candidates: {summary.unlabeled_candidates}",
        f"labels.same: {readiness.same_count}",
        f"labels.different: {readiness.different_count}",
        f"labels.unsure: {summary.labels_by_value.get(ProductMatchLabelValue.UNSURE.value, 0)}",
        f"training_status: {status}",
        f"useful_labels: {readiness.useful_labels}/{TRAINING_MIN_LABELS}",
        f"missing_total: {readiness.missing_total}",
        f"missing_same: {readiness.missing_same}",
        f"missing_different: {readiness.missing_different}",
        f"recommended_missing_same: {readiness.recommended_missing_same}",
        f"recommended_missing_different: {readiness.recommended_missing_different}",
    ]
    if summary.active_model_version:
        lines.append(f"active_model_version: {summary.active_model_version}")
        lines.append(f"model_predictions_count: {summary.model_predictions_count}")
    return "\n".join(lines)


def format_candidate(candidate: ProductMatchCandidate, index: int, total: int) -> str:
    features = candidate.features
    return "\n".join([
        "",
        f"[{index}/{total}] candidate_id={candidate.id} run_id={candidate.run_id} query={candidate.query!r}",
        f"confidence={candidate.match_confidence:.4f} label={candidate.label or '-'}",
        "",
        "LEFT",
        f"  store: {candidate.left_store_id}",
        f"  title: {candidate.left_title}",
        f"  price: {_format_price(candidate.left_price)}",
        f"  key:   {candidate.left_canonical_key}",
        f"  url:   {candidate.left_product_url}",
        "",
        "RIGHT",
        f"  store: {candidate.right_store_id}",
        f"  title: {candidate.right_title}",
        f"  price: {_format_price(candidate.right_price)}",
        f"  key:   {candidate.right_canonical_key}",
        f"  url:   {candidate.right_product_url}",
        "",
        "FEATURES",
        f"  token_overlap={features.token_overlap:.4f} rare_token_overlap={features.rare_token_overlap:.4f}",
        f"  numeric_token_agreement={features.numeric_token_agreement:.4f} title_similarity={features.title_similarity:.4f}",
        f"  brand_agreement={features.brand_agreement:.4f} category_agreement={features.category_agreement:.4f}",
        f"  accessory_mismatch={features.accessory_mismatch} price_ratio={features.price_ratio}",
        f"  model_suffix_conflict={features.model_suffix_conflict} storage_conflict={features.storage_conflict}",
        f"  screen_size_conflict={features.screen_size_conflict} bundle_conflict={features.bundle_conflict}",
    ])


def review_candidates(
    repository,
    *,
    status: str = "unlabeled",
    limit: int = 50,
    query_text: str | None = None,
    run_id: int | None = None,
    open_urls: bool = False,
    input_func: Callable[[str], str] = input,
    output_func: Callable[[str], None] = print,
) -> int:
    candidates = repository.list_match_candidates(status, limit, query_text, run_id)
    labeled = 0
    if not candidates:
        output_func("No matching candidates found.")
        return labeled

    output_func(_labeling_policy())
    total = len(candidates)
    for index, candidate in enumerate(candidates, start=1):
        output_func(format_candidate(candidate, index, total))
        if open_urls:
            open_candidate_urls(candidate)
        while True:
            raw = input_func("[s]ame [d]ifferent [u]nsure s[k]ip [o]pen [q]uit > ")
            command = parse_review_command(raw)
            if command is None:
                output_func("Invalid command. Use s, d, u, k, o or q.")
                continue
            if command == "open":
                open_candidate_urls(candidate)
                continue
            if command == "skip":
                break
            if command == "quit":
                return labeled
            assert isinstance(command, ProductMatchLabelValue)
            repository.label_match_candidate(
                candidate.id,
                ProductMatchLabelRequest(label=command, comment=None),
            )
            labeled += 1
            output_func(f"Saved label {command.value} for candidate {candidate.id}.")
            break
    return labeled


def open_candidate_urls(candidate: ProductMatchCandidate) -> None:
    for url in [candidate.left_product_url, candidate.right_product_url]:
        if sys.platform == "darwin":
            subprocess.run(["open", url], check=False)
        elif sys.platform.startswith("win"):
            subprocess.run(["cmd", "/c", "start", "", url], check=False)
        else:
            subprocess.run(["xdg-open", url], check=False)


def _labeling_policy() -> str:
    return "\n".join([
        "Labeling policy:",
        "- same: same commercially comparable product; color can differ.",
        "- different: relevant variant/capacity/size/model/condition/bundle/brand differs.",
        "- unsure: title or URL is ambiguous.",
    ])


def _format_price(price: float | None) -> str:
    if price is None:
        return "-"
    return f"{price:,.2f}"


def _repository():
    return build_repository(get_settings().database_url)


def main() -> None:
    parser = argparse.ArgumentParser(description="Review and label product matching candidates.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    review_parser = subparsers.add_parser("review")
    review_parser.add_argument("--limit", type=int, default=50)
    review_parser.add_argument("--status", choices=["unlabeled", "labeled", "all"], default="unlabeled")
    review_parser.add_argument("--query", dest="query_text", default=None)
    review_parser.add_argument("--run-id", type=int, default=None)
    review_parser.add_argument("--open-urls", action="store_true")

    subparsers.add_parser("stats")

    args = parser.parse_args()
    repository = _repository()
    if args.command == "stats":
        print(format_stats(repository.get_match_summary()))
    elif args.command == "review":
        labeled = review_candidates(
            repository,
            status=args.status,
            limit=args.limit,
            query_text=args.query_text,
            run_id=args.run_id,
            open_urls=args.open_urls,
        )
        print(f"labels_saved: {labeled}")


if __name__ == "__main__":
    main()
