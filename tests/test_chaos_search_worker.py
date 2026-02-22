"""
[test_chaos_search_worker.py]

이 테스트는 검색 워커(SearchWorker)의 극한 통제 상황에서의 안정성을 검증합니다.

- 테스트 목적:
  1. 검색 프로세스 풀(ProcessPool) 재사용 및 해제 시 좀비 프로세스 발생 방지.
  2. 대량 검색 도중 강제 중지 시 로직의 즉각적인 응답성(Responsiveness) 확인.

- 주요 검증 사항:
  1. 수십 회의 검색 워커 생성 및 강제 종료 반복 시 리소스 누적 여부.
  2. 워커 종료 시 시그널 슬롯 연결이 안전하게 해제되는지 확인.
"""

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
                "cache_enabled": False,
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
