"""
[test_worker.py]

이 테스트는 검색 워커(SearchWorker)의 기본적인 배치 실행, 취소 로직 및 에러 처리 경로를 검증합니다.

- 테스트 목적:
  1. 병렬 실행기(GlobalExecutor)와 연동된 파일 검색 작업의 생명주기 관리 확인.
  2. 사용자 중단 요청이나 타임아웃 발생 시의 안전한 리소스 회수 보장.

- 주요 검증 사항:
  1. 검색 결과의 배치 처리 및 시그널 방출 무결성.
  2. 작업 취소(`stop`) 시의 즉각적인 루프 중단 및 스레드 정리.
  3. 파일 처리 도중 예외 발생 시의 스킵(Skip) 처리 및 결과 리포팅.
"""

from unittest.mock import MagicMock, patch

from core.worker import SearchWorker


def test_worker_result_batching():
    file_list = [("test1.txt", 100), ("test2.txt", 200), ("test3.txt", 300)]
    search_string = "hello"

    worker = SearchWorker({"file_list": file_list, "search_string": search_string, "use_complex_search": True})

    results_received = []
    worker.signals.results_found.connect(lambda r: results_received.append(r))

    with patch("core.worker.GlobalExecutor.get_executor") as mock_get:
        mock_executor = MagicMock()
        mock_get.return_value = mock_executor

        mock_futures = []
        future = MagicMock()
        future.result.return_value = {
            "results": [
                ("test1.txt", 1, [(1, "hello")]),
                ("test2.txt", 1, [(1, "hello")]),
                ("test3.txt", 1, [(1, "hello")]),
            ],
            "skipped": [],
        }
        mock_futures.append(future)

        mock_executor.submit.side_effect = mock_futures

        with patch("core.worker.search_in_files_batch"), patch("core.worker.wait", return_value=(mock_futures, [])):
            worker.run()

    all_found = []
    for batch in results_received:
        for item in batch:
            all_found.append(item[0])

    assert len(all_found) == 3


def test_worker_stop_logic():
    worker = SearchWorker(
        {"file_list": [("f1.txt", 100), ("f2.txt", 100)], "search_string": "search", "use_complex_search": True}
    )
    finished_called = []
    worker.signals.search_finished.connect(
        lambda found, total_matches, skipped: finished_called.append((found, total_matches, skipped))
    )

    with patch("core.worker.GlobalExecutor.get_executor") as mock_get:
        mock_executor = MagicMock()
        mock_get.return_value = mock_executor

        mock_future = MagicMock()
        mock_executor.submit.return_value = mock_future

        with patch("core.worker.wait", return_value=([mock_future], [])):
            worker.stop()
            worker.run()

    assert mock_future.cancel.called
    assert not worker.is_running.is_set()
    assert worker.stop_event.is_set()
    assert finished_called == [(0, 0, 0)]


def test_worker_timeout_handling():
    file_list = [("slow_file.txt", 100)]
    worker = SearchWorker({"file_list": file_list, "search_string": "search", "use_complex_search": True})

    with patch("core.worker.GlobalExecutor.get_executor") as mock_get:
        mock_executor = MagicMock()
        mock_get.return_value = mock_executor

        mock_future = MagicMock()
        mock_future.result.side_effect = Exception("Timeout like error")
        mock_executor.submit.return_value = mock_future

        finished_called = []
        skipped_received = []
        worker.signals.search_finished.connect(
            lambda found, total_matches, skipped: finished_called.append((found, total_matches, skipped))
        )
        worker.signals.skipped_found.connect(lambda s: skipped_received.extend(s))

        with patch("core.worker.search_in_files_batch"), patch("core.worker.wait", return_value=([mock_future], [])):
            worker.run()

        assert finished_called == [(0, 0, 1)]
        assert any(s[0] == "slow_file.txt" for s in skipped_received)


def test_worker_skipped_signal():
    file_list = [("skipped_file.xml", 100)]
    worker = SearchWorker({"file_list": file_list, "search_string": "search", "use_complex_search": True})

    skipped_received = []
    worker.signals.skipped_found.connect(lambda s: skipped_received.extend(s))

    with patch("core.worker.GlobalExecutor.get_executor") as mock_get:
        mock_executor = MagicMock()
        mock_get.return_value = mock_executor

        future = MagicMock()
        future.result.return_value = {"results": [], "skipped": [("skipped_file.xml", "Test reason")]}
        mock_executor.submit.return_value = future

        with patch("core.worker.search_in_files_batch"), patch("core.worker.wait", return_value=([future], [])):
            worker.run()

    assert any(s[0] == "skipped_file.xml" for s in skipped_received)
    assert len(skipped_received) == 1


def test_worker_exception_handling():
    file_list = [("error_file.txt", 100)]
    worker = SearchWorker({"file_list": file_list, "search_string": "search", "use_complex_search": True})

    skipped_received = []
    finished_called = []
    worker.signals.skipped_found.connect(lambda s: skipped_received.extend(s))
    worker.signals.search_finished.connect(
        lambda found, total_matches, skipped: finished_called.append((found, total_matches, skipped))
    )

    with patch("core.worker.GlobalExecutor.get_executor") as mock_get:
        mock_executor = MagicMock()
        mock_get.return_value = mock_executor
        mock_future = MagicMock()
        mock_future.result.side_effect = Exception("File processing error")
        mock_executor.submit.return_value = mock_future

        with patch("core.worker.search_in_files_batch"), patch("core.worker.wait", return_value=([mock_future], [])):
            worker.run()

    assert any(s[0] == "error_file.txt" for s in skipped_received)
    assert finished_called
    assert finished_called[0][0] == 0
    assert finished_called[0][2] >= 1
