from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol

from app.models import ProductPairFeatures
from app.routing import normalize_text

SEMANTIC_FEATURE_NAMES = [
    "title_embedding_similarity",
    "normalized_title_embedding_similarity",
    "canonical_text_embedding_similarity",
    "brand_model_text_embedding_similarity",
]
RERANKER_FEATURE_NAMES = [
    "reranker_score_raw_avg",
    "reranker_score_same_query_avg",
]
DEFAULT_SENTENCE_TRANSFORMER_MODEL = "BAAI/bge-m3"
DEFAULT_RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"
DEFAULT_SEMANTIC_CACHE_PATH = Path("artifacts/matching/semantic_embedding_cache.joblib")
DEFAULT_RERANKER_CACHE_PATH = Path("artifacts/matching/reranker_score_cache.joblib")
MODEL_PRESETS = {
    "bge-m3": "BAAI/bge-m3",
    "e5-small": "intfloat/multilingual-e5-small",
    "e5-base": "intfloat/multilingual-e5-base",
    "minilm": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    "mpnet": "sentence-transformers/paraphrase-multilingual-mpnet-base-v2",
}
RERANKER_MODEL_PRESETS = {
    "bge-reranker-v2-m3": DEFAULT_RERANKER_MODEL,
}


class TextEmbedder(Protocol):
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        ...


class PairReranker(Protocol):
    def score_pairs(
        self,
        pairs: list[tuple[str, str]],
        *,
        variants: list[str] | None = None,
    ) -> list[float]:
        ...


@dataclass(frozen=True)
class PairTextValues:
    left_title: str
    right_title: str
    left_canonical_key: str | None = None
    right_canonical_key: str | None = None


class SentenceTransformerTextEmbedder:
    def __init__(self, model_name: str = DEFAULT_SENTENCE_TRANSFORMER_MODEL) -> None:
        self.model_name = model_name
        self._model = None

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError(
                    'Local semantic matching dependencies are missing. '
                    'Install them with: pip install -e ".[ml-text]"'
                ) from exc
            self._model = SentenceTransformer(self.model_name)
        embeddings = self._model.encode(
            texts,
            batch_size=32,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [embedding.tolist() for embedding in embeddings]


class CachedTextEmbedder:
    def __init__(
        self,
        embedder: TextEmbedder,
        *,
        model_name: str,
        cache_path: Path | None = DEFAULT_SEMANTIC_CACHE_PATH,
    ) -> None:
        self.embedder = embedder
        self.model_name = model_name
        self.cache_path = cache_path
        self._cache: dict[str, list[float]] = {}
        self._loaded = False

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self._load_cache()
        keys = [self._cache_key(text) for text in texts]
        missing_texts: list[str] = []
        missing_keys: list[str] = []
        for text, key in zip(texts, keys):
            if key not in self._cache:
                missing_texts.append(text)
                missing_keys.append(key)
        if missing_texts:
            vectors = self.embedder.embed_texts(missing_texts)
            for key, vector in zip(missing_keys, vectors):
                self._cache[key] = vector
            self._save_cache()
        return [self._cache[key] for key in keys]

    def _cache_key(self, text: str) -> str:
        digest = hashlib.sha256()
        digest.update(self.model_name.encode("utf-8"))
        digest.update(text.encode("utf-8"))
        return digest.hexdigest()

    def _load_cache(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if self.cache_path is None or not self.cache_path.exists():
            return
        try:
            import joblib

            cached = joblib.load(self.cache_path)
        except Exception:
            return
        if isinstance(cached, dict):
            self._cache = {
                str(key): value
                for key, value in cached.items()
                if isinstance(value, list)
            }

    def _save_cache(self) -> None:
        if self.cache_path is None:
            return
        try:
            import joblib

            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            joblib.dump(self._cache, self.cache_path)
        except Exception:
            return


class CrossEncoderPairReranker:
    def __init__(self, model_name: str = DEFAULT_RERANKER_MODEL) -> None:
        self.model_name = model_name
        self._model = None

    def score_pairs(
        self,
        pairs: list[tuple[str, str]],
        *,
        variants: list[str] | None = None,
    ) -> list[float]:
        if not pairs:
            return []
        if self._model is None:
            try:
                from sentence_transformers import CrossEncoder
            except ImportError as exc:
                raise RuntimeError(
                    'Local reranker dependencies are missing. '
                    'Install them with: pip install -e ".[ml-text]"'
                ) from exc
            self._model = CrossEncoder(self.model_name)
        scores = self._model.predict(
            pairs,
            batch_size=16,
            show_progress_bar=False,
        )
        return [float(score) for score in scores]


class CachedPairReranker:
    def __init__(
        self,
        reranker: PairReranker,
        *,
        model_name: str,
        cache_path: Path | None = DEFAULT_RERANKER_CACHE_PATH,
    ) -> None:
        self.reranker = reranker
        self.model_name = model_name
        self.cache_path = cache_path
        self._cache: dict[str, float] = {}
        self._loaded = False

    def score_pairs(
        self,
        pairs: list[tuple[str, str]],
        *,
        variants: list[str] | None = None,
    ) -> list[float]:
        self._load_cache()
        variants = variants or ["raw"] * len(pairs)
        keys = [
            self._cache_key(variant, left, right)
            for variant, (left, right) in zip(variants, pairs)
        ]
        missing_pairs: list[tuple[str, str]] = []
        missing_keys: list[str] = []
        for pair, key in zip(pairs, keys):
            if key not in self._cache:
                missing_pairs.append(pair)
                missing_keys.append(key)
        if missing_pairs:
            scores = self.reranker.score_pairs(missing_pairs)
            for key, score in zip(missing_keys, scores):
                self._cache[key] = float(score)
            self._save_cache()
        return [self._cache[key] for key in keys]

    def _cache_key(self, variant: str, left: str, right: str) -> str:
        digest = hashlib.sha256()
        digest.update(self.model_name.encode("utf-8"))
        digest.update(variant.encode("utf-8"))
        digest.update(b"\0")
        digest.update(left.encode("utf-8"))
        digest.update(b"\0")
        digest.update(right.encode("utf-8"))
        return digest.hexdigest()

    def _load_cache(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if self.cache_path is None or not self.cache_path.exists():
            return
        try:
            import joblib

            cached = joblib.load(self.cache_path)
        except Exception:
            return
        if isinstance(cached, dict):
            self._cache = {
                str(key): float(value)
                for key, value in cached.items()
                if isinstance(value, int | float)
            }

    def _save_cache(self) -> None:
        if self.cache_path is None:
            return
        try:
            import joblib

            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            joblib.dump(self._cache, self.cache_path)
        except Exception:
            return


class SemanticPairFeatureBuilder:
    def __init__(self, embedder: TextEmbedder) -> None:
        self.embedder = embedder

    def enrich_many(
        self,
        pairs: Iterable[PairTextValues],
        base_features: Iterable[ProductPairFeatures],
    ) -> list[ProductPairFeatures]:
        pair_list = list(pairs)
        feature_list = list(base_features)
        texts = _unique_texts(_semantic_texts(pair) for pair in pair_list)
        vectors_by_text = {
            text: vector
            for text, vector in zip(texts, self.embedder.embed_texts(texts))
        }
        enriched: list[ProductPairFeatures] = []
        for pair, features in zip(pair_list, feature_list):
            title_texts, normalized_texts, canonical_texts, brand_model_texts = _semantic_texts(pair)
            enriched.append(features.model_copy(update={
                "title_embedding_similarity": _pair_similarity(title_texts, vectors_by_text),
                "normalized_title_embedding_similarity": _pair_similarity(normalized_texts, vectors_by_text),
                "canonical_text_embedding_similarity": _pair_similarity(canonical_texts, vectors_by_text),
                "brand_model_text_embedding_similarity": _pair_similarity(brand_model_texts, vectors_by_text),
            }))
        return enriched


class RerankerPairFeatureBuilder:
    def __init__(self, reranker: PairReranker) -> None:
        self.reranker = reranker

    def enrich_many(
        self,
        pairs: Iterable[PairTextValues],
        base_features: Iterable[ProductPairFeatures],
    ) -> list[ProductPairFeatures]:
        pair_list = list(pairs)
        feature_list = list(base_features)
        scored_pairs: list[tuple[str, str]] = []
        variants: list[str] = []
        for pair in pair_list:
            text_pairs, pair_variants = _reranker_text_pairs(pair)
            scored_pairs.extend(text_pairs)
            variants.extend(pair_variants)
        scores = self.reranker.score_pairs(scored_pairs, variants=variants)
        enriched: list[ProductPairFeatures] = []
        for index, features in enumerate(feature_list):
            raw_left_right, raw_right_left, same_left_right, same_right_left = scores[index * 4:index * 4 + 4]
            enriched.append(features.model_copy(update={
                "reranker_score_raw_avg": round((raw_left_right + raw_right_left) / 2, 6),
                "reranker_score_same_query_avg": round((same_left_right + same_right_left) / 2, 6),
            }))
        return enriched


def build_local_semantic_feature_builder(
    model_name: str = DEFAULT_SENTENCE_TRANSFORMER_MODEL,
    cache_path: Path | None = DEFAULT_SEMANTIC_CACHE_PATH,
) -> SemanticPairFeatureBuilder:
    resolved_model_name = resolve_semantic_model_name(model_name)
    return SemanticPairFeatureBuilder(
        CachedTextEmbedder(
            SentenceTransformerTextEmbedder(resolved_model_name),
            model_name=resolved_model_name,
            cache_path=cache_path,
        )
    )


def build_local_reranker_feature_builder(
    model_name: str = DEFAULT_RERANKER_MODEL,
    cache_path: Path | None = DEFAULT_RERANKER_CACHE_PATH,
) -> RerankerPairFeatureBuilder:
    resolved_model_name = resolve_reranker_model_name(model_name)
    return RerankerPairFeatureBuilder(
        CachedPairReranker(
            CrossEncoderPairReranker(resolved_model_name),
            model_name=resolved_model_name,
            cache_path=cache_path,
        )
    )


def resolve_semantic_model_name(model_name: str | None) -> str:
    if not model_name:
        return DEFAULT_SENTENCE_TRANSFORMER_MODEL
    return MODEL_PRESETS.get(model_name, model_name)


def resolve_reranker_model_name(model_name: str | None) -> str:
    if not model_name:
        return DEFAULT_RERANKER_MODEL
    return RERANKER_MODEL_PRESETS.get(model_name, model_name)


def feature_value(features: ProductPairFeatures, name: str) -> float:
    value = getattr(features, name)
    if value is None:
        return 0.0
    return float(value)


def _semantic_texts(pair: PairTextValues) -> tuple[tuple[str, str], tuple[str, str], tuple[str, str], tuple[str, str]]:
    left_title = pair.left_title.strip()
    right_title = pair.right_title.strip()
    left_normalized = normalize_text(left_title)
    right_normalized = normalize_text(right_title)
    return (
        (left_title, right_title),
        (left_normalized, right_normalized),
        (_canonical_text(left_normalized, pair.left_canonical_key), _canonical_text(right_normalized, pair.right_canonical_key)),
        (_brand_model_text(left_normalized, pair.left_canonical_key), _brand_model_text(right_normalized, pair.right_canonical_key)),
    )


def _canonical_text(normalized_title: str, canonical_key: str | None) -> str:
    if canonical_key:
        return f"title: {normalized_title} | canonical: {normalize_text(canonical_key)}"
    return f"title: {normalized_title}"


def _brand_model_text(normalized_title: str, canonical_key: str | None) -> str:
    source = normalize_text(canonical_key or normalized_title)
    tokens = source.split()
    return " ".join(tokens[:6]) or normalized_title


def _unique_texts(text_groups: Iterable[tuple[tuple[str, str], ...]]) -> list[str]:
    seen: set[str] = set()
    values: list[str] = []
    for group in text_groups:
        for left, right in group:
            for text in (left, right):
                if text and text not in seen:
                    seen.add(text)
                    values.append(text)
    return values


def _pair_similarity(texts: tuple[str, str], vectors_by_text: dict[str, list[float]]) -> float:
    left, right = texts
    if not left or not right:
        return 0.0
    return round(_cosine(vectors_by_text[left], vectors_by_text[right]), 4)


def _reranker_text_pairs(pair: PairTextValues) -> tuple[list[tuple[str, str]], list[str]]:
    left = pair.left_title.strip()
    right = pair.right_title.strip()
    return (
        [
            (left, right),
            (right, left),
            (_same_query_text(left), right),
            (_same_query_text(right), left),
        ],
        ["raw", "raw", "same_query", "same_query"],
    )


def _same_query_text(title: str) -> str:
    return f"Es exactamente el mismo producto comercial: {title.strip()}"


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return max(-1.0, min(1.0, dot / (left_norm * right_norm)))
