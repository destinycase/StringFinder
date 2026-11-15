"""검색 작업자를 빠르게 시작·중지해도 멈추거나 신호가 꼬이지 않는지 확인합니다(chaos, 기본 실행 제외)."""

import random
import threading
import time

import pytest

from core.worker import SearchWorker


@pytest.mark.chaos
def test_chaos_rapid_search_worker_start_stop(tmp_path):
    root = tmp_path / "search_worker_chaos"
    root.mkdir()

    for i in range(120):
        (root / f"f_{i:04d}.txt").write_text("needle data\n", encoding="utf-8")

    errors: list[str] = []
    cycles = 20

    for _ in range(cycles):
        worker = SearchWorker(
            {
                "search_paths": [str(root)],
                "search_string": "needle",
                "extensions": ["txt"],
            }
        )
        worker.signals.error.connect(lambda msg, sink=errors: sink.append(msg))

        thread = threading.Thread(target=worker.run, daemon=True)
        thread.start()
        time.sleep(random.uniform(0.002, 0.02))
        worker.stop()
        thread.join(timeout=8.0)

        assert not thread.is_alive()

    assert not errors
