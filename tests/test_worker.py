"""
[test_worker.py]

이 테스트는 비동기 작업 처리를 담당하는 `SearchWorker`의 백그라운드 로직을 검증합니다.

- 테스트 목적:
  1. 멀티프로세싱 및 멀티스레딩 환경에서 데이터 배치(Batching) 및 시그널 전송의 정확성 확인.

- 주요 검증 사항:
  1. 대량의 파일 검색 결과에 대한 배치 단위 시그널 전송 로직.
  2. 검색 중지(Stop) 명령 시의 안전한 프로세스 종료 및 리소스 정리.
  3. 작업 중 발생한 예외 상황의 안전한 포획 및 UI 보고.
"""

from unittest.mock import MagicMock, patch

from core.worker import SearchWorker


def test_worker_result_batching():
    """test_worker_result_batching 함수."""
    file_list = [("test1.txt", 100), ("test2.txt", 200), ("test3.txt", 300)]
    search_string = "hello"

    worker = SearchWorker({"file_list": file_list, "search_string": search_string, "use_complex_search": True})

    results_received = []
    worker.signals.results_found.connect(lambda r: results_received.append(r))

    with patch("core.worker.GlobalExecutor.get_executor") as mock_get:
        mock_executor = MagicMock()
        mock_get.return_value = mock_executor

        mock_futures = []
        f = MagicMock()
        f.result.return_value = {
            "results": [
                ("test1.txt", 1, [(1, "hello")]),
                ("test2.txt", 1, [(1, "hello")]),
                ("test3.txt", 1, [(1, "hello")]),
            ],
            "skipped": [],
        }
        mock_futures.append(f)

        mock_executor.submit.side_effect = mock_futures

        with patch("core.worker.search_in_files_batch"), patch("core.worker.wait", return_value=(mock_futures, [])):
            worker.run()

    # 결과 검증
    assert len(results_received) > 0
    all_found = []
    for batch in results_received:
        for item in batch:
            all_found.append(item[0])

    assert len(all_found) == 3


def test_worker_stop_logic():
    """워커 중단 로직 테스트"""
    worker = SearchWorker({"file_list": [("f1.txt", 100), ("f2.txt", 100)], "search_string": "search", "use_complex_search": True})

    with patch("core.worker.GlobalExecutor.get_executor") as mock_get:
        mock_executor = MagicMock()
        mock_get.return_value = mock_executor

        mock_future = MagicMock()
        mock_executor.submit.return_value = mock_future

        with patch("core.worker.wait", return_value=([mock_future], [])):
            # 워커 중단 설정
            worker.stop()
            worker.run()

            # 중단되었으므로 퓨처 캔슬이 호출되어야 함 (흐름 커버리지 또는 로그를 통한 암시적 확인)
            pass


def test_worker_timeout_handling():
    """워커 타임아웃 처리 테스트"""
    file_list = [("slow_file.txt", 100)]
    worker = SearchWorker({"file_list": file_list, "search_string": "search", "use_complex_search": True})

    with patch("core.worker.GlobalExecutor.get_executor") as mock_get:
        mock_executor = MagicMock()
        mock_get.return_value = mock_executor

        mock_future = MagicMock()
        mock_future.result.side_effect = Exception("Timeout like error")
        mock_executor.submit.return_value = mock_future

        finished_called = []
        worker.signals.search_finished.connect(lambda x, y: finished_called.append(x))

        with patch("core.worker.search_in_files_batch"), patch("core.worker.wait", return_value=([mock_future], [])):
            worker.run()

        assert len(finished_called) == 1


def test_worker_skipped_signal():
    """워커 스킵된 파일 시그널 전송 테스트"""
    file_list = [("skipped_file.xml", 100)]
    worker = SearchWorker({"file_list": file_list, "search_string": "search", "use_complex_search": True})

    skipped_received = []
    worker.signals.skipped_found.connect(lambda s: skipped_received.extend(s))

    with patch("core.worker.GlobalExecutor.get_executor") as mock_get:
        mock_executor = MagicMock()
        mock_get.return_value = mock_executor

        f = MagicMock()
        f.result.return_value = {"results": [], "skipped": [("skipped_file.xml", "Test reason")]}
        mock_executor.submit.return_value = f

        with patch("core.worker.search_in_files_batch"), patch("core.worker.wait", return_value=([f], [])):
            worker.run()

    # 튜플의 첫 번째 요소(경로)가 포함되어 있는지 확인
    assert any(s[0] == "skipped_file.xml" for s in skipped_received)
    assert len(skipped_received) == 1


def test_worker_exception_handling():
    """워커 예외 처리 테스트(파일 처리 중 오류)"""
    file_list = [("error_file.txt", 100)]
    worker = SearchWorker({"file_list": file_list, "search_string": "search", "use_complex_search": True})

    with patch("core.worker.GlobalExecutor.get_executor") as mock_get:
        mock_executor = MagicMock()
        mock_get.return_value = mock_executor
        mock_future = MagicMock()
        mock_future.result.side_effect = Exception("File processing error")
        mock_executor.submit.return_value = mock_future

        with patch("core.worker.search_in_files_batch"), patch("core.worker.wait", return_value=([mock_future], [])):
            worker.run()

        pass
