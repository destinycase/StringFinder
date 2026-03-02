
import os
import sys
import time
import pytest
from unittest.mock import MagicMock, patch

# Add src to path
sys.path.append(os.path.abspath("src"))

from core.worker import SearchWorker
from sf_utils.constants import Constants

class MockSignals:
    def __init__(self):
        self.progress_updated = MagicMock()
        self.results_found = MagicMock()
        self.skipped_found = MagicMock()
        self.search_finished = MagicMock()
        self.scan_finished = MagicMock()
        self.error = MagicMock()
        self.finished = MagicMock()
        self.scan_started = MagicMock()

@pytest.fixture
def mock_worker():
    # SearchWorker는 params 딕셔너리를 인자로 받음
    params = {
        Constants.PAYLOAD_FILE_LIST: [("dummy_path", 100)],
        Constants.PAYLOAD_SEARCH_STRING: "search_term",
        Constants.PAYLOAD_USE_COMPLEX_SEARCH: True  # Python/Batch 경로 강제 진입
    }
    worker = SearchWorker(params)
    worker.signals = MockSignals()  # type: ignore
    return worker

def test_worker_hang_timeout_resource_cleanup(mock_worker):
    """[v4.63.5/6] 타임아웃 시 자원이 실제로 회수되는지 검증"""
    
    from concurrent.futures import Future

    # Constants.TIMEOUT_WORKER_HANG을 아주 짧게 모킹 (0.5초)
    with patch("sf_utils.constants.Constants.TIMEOUT_WORKER_HANG", 0.5):
        # GlobalExecutor.get_executor를 모킹하여 가짜 실행기 반환
        mock_executor = MagicMock()
        # submit 호출 시 완료되지 않는 Future 반환
        mock_executor.submit.return_value = Future()
        
        with patch("core.worker.GlobalExecutor.get_executor", return_value=mock_executor):
            # 파일 1개 배치
            mock_worker.files = [("test.txt", 100)]
            
            # 검색 엔진 호출을 피하기 위해 _run_rust_search 등은 무시하고 run() 호출
            # run() 내에서 use_complex_search=True이므로 _run_python_search -> _run_batch_search 진입
            start_time = time.time()
            mock_worker.run()
            duration = time.time() - start_time
            
            # 검증 1: 타임아웃(0.5s) 부근에서 종료되었는지
            assert duration < 2.0
            
            # 검증 2: 에러 시그널이 발생했는지
            mock_worker.signals.error.emit.assert_called()
            args, _ = mock_worker.signals.error.emit.call_args
            assert "Timeout" in args[0]
            
            # 검증 3: stop_event가 set 되었는지
            assert mock_worker.stop_event.is_set()
            
            # 검증 4: executor가 셧다운(None) 되었는지 (SearchWorker 내부 상태)
            assert mock_worker._executor is None
            # 그리고 실제 executor 객체에 대해 shutdown이 호출되었는지
            mock_executor.shutdown.assert_called_with(wait=False, cancel_futures=True)

def test_worker_stop_immediate_shutdown(mock_worker):
    """사용자 중지(stop) 요청 시 즉시 executor가 해제되는지 확인"""
    mock_worker.stop()
    assert mock_worker.is_running.is_set() is False
    assert mock_worker.stop_event.is_set()
    assert mock_worker._executor is None
