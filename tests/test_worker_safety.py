"""
[test_worker_safety.py]

이 테스트는 워커 스레드 실행 시의 비정상적인 상태(매니저 부재 등)에 대한 안전장치를 검증합니다.

- 테스트 목적:
  1. 시스템 관리 객체가 예기치 않게 소멸되거나 부재한 상황에서도 하위 워커가 크래시 없이 안전하게 종료되는지 확인.

- 주요 검증 사항:
  1. `GlobalManager`가 `None`인 상태에서의 워커 초기화 및 중단 시 안전성.
  2. 비정상적인 실행 환경에서의 `AttributeError` 등 예외 발생 방지.
"""

import unittest
from unittest.mock import MagicMock, patch

import pytest

from core.worker import GlobalExecutor, SearchWorker


class TestWorkerSafety(unittest.TestCase):
    def test_executor_reuse_does_not_submit_probe_task(self):
        """활성 실행기 재사용 시 검증용 더미 작업을 제출하지 않는지 확인합니다."""
        executor = MagicMock()

        with patch.object(GlobalExecutor, "_executor", executor), patch.object(GlobalExecutor, "_is_active", True):
            assert GlobalExecutor.get_executor() is executor

        executor.submit.assert_not_called()

    def test_executor_invalidate_clears_stale_global_reference(self):
        """외부 종료된 실행기를 무효화하면 다음 검색에서 재생성할 수 있는지 확인합니다."""
        executor = MagicMock()
        with patch.object(GlobalExecutor, "_executor", executor), patch.object(GlobalExecutor, "_is_active", True):
            GlobalExecutor.invalidate(executor)
            assert GlobalExecutor._executor is None
            assert GlobalExecutor._is_active is False

    def test_stop_with_none_manager(self):
        """test_stop_with_none_manager 함수."""
        params = {"search_string": "test", "search_paths": ["."], "file_list": []}

        with patch("core.worker.get_global_manager", return_value=None):
            worker = SearchWorker(params)

            import threading

            assert isinstance(worker.stop_event, threading.Event)

            try:
                worker.stop()
            except AttributeError as e:
                pytest.fail(f"SearchWorker.stop() raised AttributeError when stop_event is None: {e}")
            except Exception as e:
                pytest.fail(f"SearchWorker.stop() raised unexpected exception: {e}")

    def test_run_with_none_manager_safely(self):
        """test_run_with_none_manager_safely 함수."""
        params = {"search_string": "test", "search_paths": ["."], "file_list": ["non_existent_file.txt"]}

        with patch("core.worker.get_global_manager", return_value=None):
            worker = SearchWorker(params)
            # 단순히 예외 없이 초기화 및 중단이 가능한지 확인
            worker.is_running.clear()  # 루프 방지
            worker.stop()
            assert not worker.is_running.is_set()
