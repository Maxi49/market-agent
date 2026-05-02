from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from app.models import EmbeddingBackfillResponse


@dataclass(frozen=True)
class EmbeddingSettings:
    enabled: bool
    api_key: str | None
    model: str = "text-embedding-3-small"
    dimensions: int = 1536
    max_items_per_run: int = 500
    monthly_token_budget: int = 1_000_000
    estimated_cost_per_1m_tokens: float = 0.02

    @property
    def available(self) -> bool:
        return self.enabled and bool(self.api_key)


@dataclass(frozen=True)
class EmbeddingCandidate:
    canonical_key: str
    embedding_text: str
    embedding_text_hash: str


@dataclass(frozen=True)
class EmbeddingResult:
    canonical_key: str
    embedding: list[float]
    token_count: int


class EmbeddingProvider(Protocol):
    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        ...


class DisabledEmbeddingProvider:
    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("Embeddings are disabled.")


class OpenAIEmbeddingProvider:
    def __init__(self, settings: EmbeddingSettings) -> None:
        self.settings = settings

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        from openai import AsyncOpenAI

        if not self.settings.available:
            raise RuntimeError("OpenAI embeddings are not configured.")
        client = AsyncOpenAI(api_key=self.settings.api_key)
        response = await client.embeddings.create(
            model=self.settings.model,
            input=texts,
            dimensions=self.settings.dimensions,
            encoding_format="float",
        )
        return [item.embedding for item in response.data]


class EmbeddingBudgetGuard:
    def __init__(self, settings: EmbeddingSettings) -> None:
        self.settings = settings

    def estimate_tokens(self, texts: list[str]) -> int:
        # Conservative approximation: roughly 4 chars per token, plus small per-item overhead.
        return sum(max(1, math.ceil(len(text) / 4) + 4) for text in texts)

    def estimate_cost(self, token_count: int) -> float:
        return round((token_count / 1_000_000) * self.settings.estimated_cost_per_1m_tokens, 8)

    def validate(
        self,
        candidates: list[EmbeddingCandidate],
        used_tokens_this_month: int,
    ) -> tuple[list[EmbeddingCandidate], list[str], int, float, int]:
        errors: list[str] = []
        limited = candidates[: self.settings.max_items_per_run]
        skipped_by_limit = len(candidates) - len(limited)
        if skipped_by_limit > 0:
            errors.append(f"max_items_limit_applied:{skipped_by_limit}")

        estimated_tokens = self.estimate_tokens([candidate.embedding_text for candidate in limited])
        remaining = max(0, self.settings.monthly_token_budget - used_tokens_this_month)
        if estimated_tokens > remaining:
            errors.append("monthly_token_budget_exceeded")
            return [], errors, estimated_tokens, self.estimate_cost(estimated_tokens), remaining

        return limited, errors, estimated_tokens, self.estimate_cost(estimated_tokens), remaining - estimated_tokens


def build_embedding_text(
    normalized_title: str,
    brand: str | None,
    model: str | None,
    category: str | None,
    attributes: dict,
) -> str:
    parts = [
        f"title: {normalized_title}",
        f"brand: {brand}" if brand else "",
        f"model: {model}" if model else "",
        f"category: {category}" if category else "",
        "attributes: "
        + ", ".join(f"{key}={value}" for key, value in sorted((attributes or {}).items())),
    ]
    return " | ".join(part for part in parts if part and part != "attributes: ")


def embedding_hash(text: str, model: str, dimensions: int) -> str:
    digest = hashlib.sha256()
    digest.update(model.encode("utf-8"))
    digest.update(str(dimensions).encode("utf-8"))
    digest.update(text.encode("utf-8"))
    return digest.hexdigest()


def empty_backfill_response(dry_run: bool, error: str) -> EmbeddingBackfillResponse:
    return EmbeddingBackfillResponse(dry_run=dry_run, errors=[error])


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
