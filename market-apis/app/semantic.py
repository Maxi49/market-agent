from __future__ import annotations

from dataclasses import dataclass

from app.database import OptionalRepository
from app.embeddings import (
    DisabledEmbeddingProvider,
    EmbeddingBudgetGuard,
    EmbeddingProvider,
    EmbeddingSettings,
    OpenAIEmbeddingProvider,
    empty_backfill_response,
)
from app.models import EmbeddingBackfillResponse, ScoredProduct, SemanticMatch


class SemanticMatcher:
    def __init__(self, repository: OptionalRepository) -> None:
        self.repository = repository

    def match(self, scored: ScoredProduct) -> SemanticMatch | None:
        embedding = scored.normalized.raw_compact.get("embedding")
        return self.repository.find_semantic_match(scored.normalized.canonical_key, embedding)


@dataclass
class EmbeddingBackfillService:
    repository: OptionalRepository
    settings: EmbeddingSettings
    provider: EmbeddingProvider
    budget_guard: EmbeddingBudgetGuard

    async def run(
        self,
        dry_run: bool = True,
        limit: int | None = None,
        force: bool = False,
    ) -> EmbeddingBackfillResponse:
        if not self.settings.enabled:
            return empty_backfill_response(dry_run=dry_run, error="embeddings_disabled")
        if not self.settings.api_key:
            return empty_backfill_response(dry_run=dry_run, error="openai_api_key_missing")

        effective_limit = min(limit or self.settings.max_items_per_run, self.settings.max_items_per_run)
        candidates = self.repository.list_embedding_candidates(
            model=self.settings.model,
            dimensions=self.settings.dimensions,
            limit=effective_limit,
            force=force,
        )
        if not candidates:
            response = EmbeddingBackfillResponse(
                processed=0,
                skipped=0,
                estimated_tokens=0,
                estimated_cost_usd=0,
                budget_remaining_tokens=self.settings.monthly_token_budget
                - self.repository.used_embedding_tokens_this_month(self.settings.model),
                dry_run=dry_run,
            )
            self.repository.log_embedding_usage(
                self.settings.model,
                response.processed,
                response.estimated_tokens,
                response.estimated_cost_usd,
                dry_run,
                response.errors,
            )
            return response

        used_tokens = self.repository.used_embedding_tokens_this_month(self.settings.model)
        allowed, errors, estimated_tokens, estimated_cost, remaining = self.budget_guard.validate(
            candidates,
            used_tokens,
        )
        skipped = len(candidates) - len(allowed)
        response = EmbeddingBackfillResponse(
            processed=0 if dry_run else len(allowed),
            skipped=skipped,
            estimated_tokens=estimated_tokens,
            estimated_cost_usd=estimated_cost,
            budget_remaining_tokens=remaining,
            errors=errors,
            dry_run=dry_run,
        )

        if dry_run or not allowed:
            self.repository.log_embedding_usage(
                self.settings.model,
                response.processed,
                response.estimated_tokens,
                response.estimated_cost_usd,
                dry_run,
                response.errors,
            )
            return response

        try:
            vectors = await self.provider.embed_texts([candidate.embedding_text for candidate in allowed])
        except Exception as exc:
            response.errors.append(f"embedding_provider_error:{exc}")
            response.processed = 0
            self.repository.log_embedding_usage(
                self.settings.model,
                response.processed,
                response.estimated_tokens,
                response.estimated_cost_usd,
                dry_run,
                response.errors,
            )
            return response

        token_counts = [
            self.budget_guard.estimate_tokens([candidate.embedding_text])
            for candidate in allowed
        ]
        cost_items = [
            self.budget_guard.estimate_cost(token_count)
            for token_count in token_counts
        ]
        self.repository.save_embeddings(
            self.settings.model,
            self.settings.dimensions,
            list(zip(allowed, vectors, token_counts, cost_items)),
        )
        self.repository.log_embedding_usage(
            self.settings.model,
            response.processed,
            response.estimated_tokens,
            response.estimated_cost_usd,
            dry_run,
            response.errors,
        )
        return response


def build_embedding_settings(settings) -> EmbeddingSettings:
    return EmbeddingSettings(
        enabled=settings.embeddings_enabled,
        api_key=settings.openai_api_key,
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
        max_items_per_run=settings.embedding_max_items_per_run,
        monthly_token_budget=settings.embedding_monthly_token_budget,
        estimated_cost_per_1m_tokens=settings.embedding_estimated_cost_per_1m_tokens,
    )


def build_embedding_provider(settings: EmbeddingSettings) -> EmbeddingProvider:
    if not settings.available:
        return DisabledEmbeddingProvider()
    return OpenAIEmbeddingProvider(settings)
