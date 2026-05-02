from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any, Protocol

from app.matching_model import (
    FEATURE_NAMES,
    _features_from_candidates,
    decision_from_probability,
    vectorize_features,
)
from app.matching_semantic import (
    PairTextValues,
    build_local_reranker_feature_builder,
    build_local_semantic_feature_builder,
)
from app.models import ProductPairFeatures

_logger = logging.getLogger(__name__)


class MatchPredictor(Protocol):
    def predict_unlabeled(self, limit: int) -> int: ...
    def prewarm(self) -> str | None: ...


class MatchingPredictor:
    def __init__(self, repository) -> None:
        self.repository = repository
        self._lock = threading.Lock()
        self._model_version: str | None = None
        self._artifact_path: str | None = None
        self._bundle: dict[str, Any] | None = None
        self._feature_names: list[str] = FEATURE_NAMES
        self._semantic_builder = None
        self._reranker_builder = None

    def prewarm(self) -> str | None:
        with self._lock:
            self._ensure_loaded_locked()
            if self._bundle is None:
                return None
            warmup_pair = [PairTextValues(left_title="warmup product left", right_title="warmup product right")]
            warmup_features = [ProductPairFeatures()]
            if self._semantic_builder is not None:
                warmup_features = self._semantic_builder.enrich_many(warmup_pair, warmup_features)
            if self._reranker_builder is not None:
                self._reranker_builder.enrich_many(warmup_pair, warmup_features)
            return self._model_version

    def predict_unlabeled(self, limit: int) -> int:
        with self._lock:
            self._ensure_loaded_locked()
            if self._bundle is None:
                return 0
            candidates = self.repository.get_unlabeled_match_candidates_for_prediction(limit)
            if not candidates:
                return 0
            features = _features_from_candidates(
                candidates,
                semantic_builder=self._semantic_builder,
                reranker_builder=self._reranker_builder,
            )
            probabilities = self._bundle["estimator"].predict_proba([
                vectorize_features(feature, self._feature_names)
                for feature in features
            ])[:, 1]
            predictions = [
                (
                    candidate.id,
                    round(float(probability), 6),
                    decision_from_probability(float(probability)),
                )
                for candidate, probability in zip(candidates, probabilities)
            ]
            return self.repository.save_match_predictions(self._model_version, predictions)

    def _ensure_loaded_locked(self) -> None:
        model_row = self.repository.get_active_match_model()
        if model_row is None:
            self._clear()
            return
        artifact_path = str(model_row["artifact_path"])
        model_version = str(model_row["version"])
        if self._bundle is not None and self._model_version == model_version and self._artifact_path == artifact_path:
            return

        try:
            import joblib
        except ImportError as exc:
            raise RuntimeError(
                'Matching model dependencies are missing. Install them with: pip install -e ".[ml-text]"'
            ) from exc

        bundle = joblib.load(Path(artifact_path))
        self._bundle = bundle
        self._model_version = model_version
        self._artifact_path = artifact_path
        self._feature_names = bundle.get("feature_names") or FEATURE_NAMES
        semantic_model = bundle.get("semantic_embedding_model")
        reranker_model = bundle.get("reranker_model")
        self._semantic_builder = (
            build_local_semantic_feature_builder(model_name=semantic_model)
            if semantic_model
            else None
        )
        self._reranker_builder = (
            build_local_reranker_feature_builder(model_name=reranker_model)
            if reranker_model
            else None
        )

    def _clear(self) -> None:
        self._model_version = None
        self._artifact_path = None
        self._bundle = None
        self._feature_names = FEATURE_NAMES
        self._semantic_builder = None
        self._reranker_builder = None


class MatchingPredictionWorker:
    def __init__(
        self,
        predictor: MatchPredictor,
        *,
        default_limit: int = 1000,
        prewarm: bool = False,
    ) -> None:
        self.predictor = predictor
        self.default_limit = default_limit
        self.prewarm_enabled = prewarm
        self._queue: asyncio.Queue[int | None] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._prewarm_task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.create_task(self._run())
        if self.prewarm_enabled:
            self._prewarm_task = asyncio.create_task(self._prewarm())

    async def stop(self) -> None:
        self._queue.put_nowait(None)
        if self._task:
            await self._task
        if self._prewarm_task:
            await self._prewarm_task

    def enqueue(self, limit: int | None = None) -> None:
        value = limit or self.default_limit
        if self._loop is None:
            self._queue.put_nowait(value)
            return
        self._loop.call_soon_threadsafe(self._queue.put_nowait, value)

    async def _prewarm(self) -> None:
        try:
            version = await asyncio.to_thread(self.predictor.prewarm)
            if version:
                _logger.info("matching_predictor prewarmed model=%s", version)
        except Exception as exc:
            _logger.error("matching_predictor prewarm error: %s", exc)

    async def _run(self) -> None:
        while True:
            limit = await self._queue.get()
            if limit is None:
                break
            try:
                saved = await asyncio.to_thread(self.predictor.predict_unlabeled, limit)
                _logger.info("matching_predictor saved_predictions=%s", saved)
            except Exception as exc:
                _logger.error("matching_predictor error: %s", exc)
