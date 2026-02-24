"""
[test_chaos_scan_worker.py]

이 테스트는 통합 검색 워커(SearchWorker)의 수명 주기 관리 및 동시성 안정성을 검증합니다.

- 테스트 목적:
  1. 검색 시작 직후 중단, 반복적인 재시작 상황에서 스레드가 정상적으로 해제되는지 확인.
  2. 비정상적인 워커 종료 시 시그널 전달 무결성 및 리소스 누수 방지.

- 주요 검증 사항:
  1. 빠른 주기의 Start/Stop 반복을 통한 교착 상태 유발 시도.
  2. 워커 종료 시점과 결과 전달 시점 간의 레이스 컨디션 해결 여부.
"""

import random
import threading
import time

import pytest

from core.worker import SearchWorker


@pytest.mark.chaos
def test_chaos_rapid_search_worker_start_stop(tmp_path):
    root = tmp_path / "chaos_data"
    root.mkdir()

    for i in range(200):
        (root / f"f_{i:04d}.txt").write_text("needle data\n", encoding="utf-8")

    errors: list[str] = []
    cycles = 25

    for _ in range(cycles):
        worker = SearchWorker(
            {
                "search_paths": [str(root)],
                "extensions": ["txt"],
                "filename_filter": None,
                "search_string": "needle",
            }
        )
        worker.signals.error.connect(lambda msg, sink=errors: sink.append(msg))

        thread = threading.Thread(target=worker.run, daemon=True)
        thread.start()
        time.sleep(random.uniform(0.001, 0.015))
        worker.stop()
        thread.join(timeout=5.0)

        assert not thread.is_alive()

    assert not errors
