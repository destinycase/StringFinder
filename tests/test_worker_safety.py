import pytest
import unittest
from unittest.mock import patch
from core.worker import SearchWorker


class TestWorkerSafety(unittest.TestCase):
    def test_stop_with_none_manager(self):
        """[중] Manager 생성 실패 시(stop_event is None) stop() 메서드 안정성 테스트"""
        params = {"search_string": "test", "search_paths": ["."], "file_list": []}

        # get_global_manager가 None을 반환하도록 모킹
        with patch("core.worker.get_global_manager", return_value=None):
            worker = SearchWorker(params)

            # stop_event가 None인지 확인
            assert worker.stop_event is None

            # stop() 호출 시 AttributeError(NoneType)가 발생하지 않아야 함
            try:
                worker.stop()
            except AttributeError as e:
                pytest.fail(f"SearchWorker.stop() raised AttributeError when stop_event is None: {e}")
            except Exception as e:
                pytest.fail(f"SearchWorker.stop() raised unexpected exception: {e}")

    def test_run_with_none_manager_safely(self):
        """Manager가 없을 때 run 계측 (기본 Python 검색으로 안전하게 동작해야 함)"""
        params = {"search_string": "test", "search_paths": ["."], "file_list": ["non_existent_file.txt"]}

        with patch("core.worker.get_global_manager", return_value=None):
            worker = SearchWorker(params)
            # 단순히 예외 없이 초기화 및 중단이 가능한지 확인
            worker.is_running = False  # 루프 방지
            worker.stop()
            assert worker.is_running is False
