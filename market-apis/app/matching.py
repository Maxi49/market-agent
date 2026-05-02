from __future__ import annotations

import re
from difflib import SequenceMatcher

from app.models import ProductPairFeatures, ScoredProduct
from app.routing import normalize_text

COMMON_TOKENS = {
    "apple",
    "celular",
    "color",
    "con",
    "de",
    "gb",
    "gen",
    "intel",
    "libre",
    "memoria",
    "negro",
    "nuevo",
    "para",
    "pulgadas",
    "smart",
    "tv",
}
VARIANT_SUFFIXES = {"air", "fe", "lite", "max", "mini", "plus", "pro", "ultra"}
BUNDLE_TOKENS = {"bundle", "combo", "incluye", "kit", "pack"}
BUNDLE_ACCESSORY_TOKENS = {
    "auriculares",
    "barra",
    "buds",
    "cargador",
    "funda",
    "mouse",
    "parlante",
    "teclado",
}


def build_pair_features(left: ScoredProduct, right: ScoredProduct) -> ProductPairFeatures:
    left_title = normalize_text(left.product.title)
    right_title = normalize_text(right.product.title)
    features = build_pair_features_from_values(
        left_title=left_title,
        right_title=right_title,
        left_price=left.product.price,
        right_price=right.product.price,
        left_canonical_key=left.normalized.canonical_key,
        right_canonical_key=right.normalized.canonical_key,
    )
    return features.model_copy(update={
        "brand_agreement": _field_agreement(left.normalized.brand, right.normalized.brand),
        "category_agreement": _field_agreement(left.normalized.category, right.normalized.category),
        "accessory_mismatch": left.normalized.is_accessory != right.normalized.is_accessory,
    })


def build_pair_features_from_values(
    *,
    left_title: str,
    right_title: str,
    left_price: float | None = None,
    right_price: float | None = None,
    left_canonical_key: str | None = None,
    right_canonical_key: str | None = None,
    base_features: ProductPairFeatures | None = None,
) -> ProductPairFeatures:
    left_title = normalize_text(left_title)
    right_title = normalize_text(right_title)
    left_tokens = _tokens(left_title)
    right_tokens = _tokens(right_title)
    left_rare = left_tokens - COMMON_TOKENS
    right_rare = right_tokens - COMMON_TOKENS
    left_storage = _storage_values(left_title, left_canonical_key)
    right_storage = _storage_values(right_title, right_canonical_key)
    left_screen_sizes = _screen_size_values(left_title, left_canonical_key)
    right_screen_sizes = _screen_size_values(right_title, right_canonical_key)

    return ProductPairFeatures(
        token_overlap=_jaccard(left_tokens, right_tokens),
        rare_token_overlap=_jaccard(left_rare, right_rare),
        numeric_token_agreement=_numeric_agreement(left_title, right_title),
        title_similarity=round(SequenceMatcher(None, left_title, right_title).ratio(), 4),
        brand_agreement=base_features.brand_agreement if base_features else 0,
        category_agreement=base_features.category_agreement if base_features else 0,
        accessory_mismatch=base_features.accessory_mismatch if base_features else False,
        model_suffix_conflict=_model_suffix_conflict(left_tokens, right_tokens),
        storage_conflict=_specified_values_conflict(left_storage, right_storage),
        screen_size_conflict=_specified_values_conflict(left_screen_sizes, right_screen_sizes),
        bundle_conflict=_is_bundle(left_title) != _is_bundle(right_title),
        canonical_key_match=bool(
            left_canonical_key
            and right_canonical_key
            and left_canonical_key == right_canonical_key
        ),
        price_ratio=_price_ratio(left_price, right_price),
    )


def estimate_match_confidence(features: ProductPairFeatures) -> float:
    score = (
        features.token_overlap * 0.20
        + features.rare_token_overlap * 0.25
        + features.numeric_token_agreement * 0.20
        + features.title_similarity * 0.20
        + features.brand_agreement * 0.10
        + features.category_agreement * 0.05
    )
    if features.accessory_mismatch:
        score -= 0.25
    if features.model_suffix_conflict:
        score -= 0.20
    if features.storage_conflict:
        score -= 0.25
    if features.screen_size_conflict:
        score -= 0.25
    if features.bundle_conflict:
        score -= 0.20
    if features.price_ratio is not None and features.price_ratio < 0.45:
        score -= 0.10
    return round(max(0, min(1, score)), 4)


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text))


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 0
    union = left | right
    if not union:
        return 0
    return round(len(left & right) / len(union), 4)


def _numeric_agreement(left_title: str, right_title: str) -> float:
    left = set(re.findall(r"\d+(?:\.\d+)?", left_title))
    right = set(re.findall(r"\d+(?:\.\d+)?", right_title))
    if not left and not right:
        return 0
    if not left or not right:
        return 0
    return _jaccard(left, right)


def _field_agreement(left: str | None, right: str | None) -> float:
    if not left or not right:
        return 0
    return 1 if normalize_text(left) == normalize_text(right) else 0


def _price_ratio(left: float | None, right: float | None) -> float | None:
    if left is None or right is None or left <= 0 or right <= 0:
        return None
    return round(min(left, right) / max(left, right), 4)


def _model_suffix_conflict(left_tokens: set[str], right_tokens: set[str]) -> bool:
    left_variants = left_tokens & VARIANT_SUFFIXES
    right_variants = right_tokens & VARIANT_SUFFIXES
    return bool(left_variants.symmetric_difference(right_variants))


def _specified_values_conflict(left: set[str], right: set[str]) -> bool:
    return bool(left and right and left != right)


def _storage_values(*texts: str | None) -> set[str]:
    values: set[str] = set()
    for text in texts:
        if not text:
            continue
        normalized = normalize_text(text)
        for amount, unit in re.findall(r"\b(\d{2,4})\s*(gb|tb)\b", normalized):
            values.add(f"{amount}{unit}")
    return values


def _screen_size_values(*texts: str | None) -> set[str]:
    values: set[str] = set()
    for text in texts:
        if not text:
            continue
        normalized = normalize_text(text)
        for size in re.findall(r"\b(\d{2,3})(?:[.,]\d+)?\s*(?:\"|pulgadas|pulg|inch|inches)", normalized):
            values.add(size)
    return values


def _is_bundle(title: str) -> bool:
    tokens = _tokens(title)
    if re.search(r"\s\+\s", title) or tokens & BUNDLE_TOKENS:
        return True
    return "con" in tokens and bool(tokens & BUNDLE_ACCESSORY_TOKENS)
