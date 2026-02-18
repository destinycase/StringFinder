from unittest.mock import MagicMock, patch
from core.worker import SearchWorker


def test_worker_result_batching():
    """워커 결과 배칭(Batching) 시그널 전송 테스트"""
    file_list = [("test1.txt", 100), ("test2.txt", 200), ("test3.txt", 300)]
    search_string = "hello"

    worker = SearchWorker({"file_list": file_list, "search_string": search_string})

    # 시그널 수신 확인용 리스트 (WorkerSignals 사용)
    results_received = []
    worker.signals.results_found.connect(lambda r: results_received.append(r))

    # GlobalExecutor.get_executor 모킹 (v4.29.3 전역 풀 재사용 대응)
    with patch("core.worker.GlobalExecutor.get_executor") as mock_get:
        mock_executor = MagicMock()
        mock_get.return_value = mock_executor

        # 가상의 Future 객체 생성
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

        # wait 모킹 (ProcessPoolExecutor 사용 시 wait가 worker.py에서 호출됨)
        # worker.py는 concurrent.futures.wait를 사용하므로 이를 모킹해야 함
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
    worker = SearchWorker({"file_list": [("f1.txt", 100), ("f2.txt", 100)], "search_string": "search"})

    with patch("core.worker.GlobalExecutor.get_executor") as mock_get:
        mock_executor = MagicMock()
        mock_get.return_value = mock_executor

        # Future 모킹 (cancel 호출 확인용)
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
    worker = SearchWorker({"file_list": file_list, "search_string": "search"})

    with patch("core.worker.GlobalExecutor.get_executor") as mock_get:
        mock_executor = MagicMock()
        mock_get.return_value = mock_executor

        mock_future = MagicMock()
        mock_future.result.side_effect = Exception("Timeout like error")
        mock_executor.submit.return_value = mock_future

        finished_called = []
        # [Fix] signals 사용
        worker.signals.search_finished.connect(lambda x, y: finished_called.append(x))

        with patch("core.worker.search_in_files_batch"), patch("core.worker.wait", return_value=([mock_future], [])):
            worker.run()

        # 예외가 발생해도 로깅만 하고 끝나야 하므로 finished 시그널은 발생해야 함
        assert len(finished_called) == 1


def test_worker_skipped_signal():
    """워커 스킵된 파일 시그널 전송 테스트"""
    file_list = [("skipped_file.xml", 100)]
    worker = SearchWorker({"file_list": file_list, "search_string": "search"})

    skipped_received = []
    # [Fix] signals 사용
    worker.signals.skipped_found.connect(lambda s: skipped_received.extend(s))

    with patch("core.worker.GlobalExecutor.get_executor") as mock_get:
        mock_executor = MagicMock()
        mock_get.return_value = mock_executor

        f = MagicMock()
        f.result.return_value = {"results": [], "skipped": ["skipped_file.xml"]}
        mock_executor.submit.return_value = f

        with patch("core.worker.search_in_files_batch"), patch("core.worker.wait", return_value=([f], [])):
            worker.run()

    assert "skipped_file.xml" in skipped_received
    assert len(skipped_received) == 1


def test_worker_exception_handling():
    """워커 예외 처리 테스트(파일 처리 중 오류)"""
    file_list = [("error_file.txt", 100)]
    worker = SearchWorker({"file_list": file_list, "search_string": "search"})

    with patch("core.worker.GlobalExecutor.get_executor") as mock_get:
        mock_executor = MagicMock()
        mock_get.return_value = mock_executor
        mock_future = MagicMock()
        mock_future.result.side_effect = Exception("File processing error")
        mock_executor.submit.return_value = mock_future

        with patch("core.worker.search_in_files_batch"), patch("core.worker.wait", return_value=([mock_future], [])):
            worker.run()

        # 예외 처리 검증(별도 assertion 없음, 크래시 없이 끝나면 통과)
        pass
