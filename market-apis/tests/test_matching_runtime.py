import asyncio
import threading

from app.matching_runtime import MatchingPredictionWorker


def test_matching_prediction_worker_enqueue_is_thread_safe() -> None:
    predictor = FakePredictor()

    async def run_worker() -> None:
        worker = MatchingPredictionWorker(predictor, default_limit=7)
        await worker.start()
        thread = threading.Thread(target=worker.enqueue)
        thread.start()
        thread.join()

        for _ in range(20):
            if predictor.calls:
                break
            await asyncio.sleep(0.01)

        await worker.stop()

    asyncio.run(run_worker())

    assert predictor.calls == [7]


class FakePredictor:
    def __init__(self) -> None:
        self.calls: list[int] = []

    def predict_unlabeled(self, limit: int) -> int:
        self.calls.append(limit)
        return 1

    def prewarm(self) -> str | None:
        return None
