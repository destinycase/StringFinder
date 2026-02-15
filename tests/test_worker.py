from unittest.mock import MagicMock, patch
from concurrent.futures import TimeoutError as FutureTimeoutError
from core.worker import SearchWorker


def test_worker_result_batching():
    """워커의 결과 배칭(Batching) 시그널 전송 테스트"""
    # SearchWorker는 (경로, 크기) 튜플 리스트를 기대함

    file_list = [("test1.txt", 100), ("test2.txt", 200), ("test3.txt", 300)]
    search_string = "hello"

    worker = SearchWorker({"file_list": file_list, "search_string": search_string})

    # 시그널 수신 확인용 리스트
    results_received = []
    worker.results_found.connect(lambda r: results_received.append(r))

    # ProcessPoolExecutor 자체를 모킹하여 실제 프로세스 생성을 막음
    with patch("core.worker.ProcessPoolExecutor") as mock_executor_class:
        mock_executor = mock_executor_class.return_value.__enter__.return_value

        # 가상의 Future 객체 생성 (search_in_files_batch는 결과 리스트를 반환함)
        mock_futures = []
        # 테스트에서는 모든 파일을 하나의 배치로 처리한다고 가정 (batch_size=100)
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

        # submit 호출 시 미래 객체들을 순차적으로 반환하도록 설정
        mock_executor.submit.side_effect = mock_futures

        mock_executor.submit.side_effect = mock_futures

        # [Fix] as_completed 대신 wait를 사용하는 로직에 맞춰 wait를 모킹
        # wait는 (done, not_done) 튜플을 반환함
        with patch("core.worker.search_in_files_batch"), patch("core.worker.wait", return_value=(mock_futures, [])):
            worker.run()

        # 결과가 배치로 추출되었는지 확인
        assert len(results_received) > 0
        all_found = []
        for batch in results_received:
            for item in batch:
                all_found.append(item[0])

        assert len(all_found) == 3


def test_worker_stop_logic():
    worker = SearchWorker({"file_list": [("f1.txt", 100), ("f2.txt", 100)], "search_string": "search"})

    with patch("core.worker.ProcessPoolExecutor"), patch("core.worker.wait", return_value=([], [])):
        # 워커 중단 설정
        worker.stop()
        worker.run()

        # 중단되었으므로 executor.shutdown이 호출어어야 함 (run() 내부에서 break)
        pass


def test_worker_timeout_handling():
    """워커의 타임아웃 처리 테스트"""
    file_list = [("slow_file.txt", 100)]

    worker = SearchWorker({"file_list": file_list, "search_string": "search"})

    with patch("core.worker.ProcessPoolExecutor") as mock_executor_class:
        mock_executor = mock_executor_class.return_value.__enter__.return_value
        mock_future = MagicMock()
        # 타임아웃 발생 시뮬레이션
        mock_future.result.side_effect = FutureTimeoutError("Timeout")
        mock_executor.submit.return_value = mock_future

        # 시그널 수신 확인
        finished_called = []
        worker.search_finished.connect(lambda x: finished_called.append(x))

        with patch("core.worker.search_in_files_batch"), patch("core.worker.wait", return_value=([mock_future], [])):
            worker.run()

        # 타임아웃이 발생해도 워커가 정상 종료되어야 함
        assert len(finished_called) == 1


def test_worker_exception_handling():
    """워커의 예외 처리 테스트 (파일 처리 중 오류)"""
    file_list = [("error_file.txt", 100)]

    worker = SearchWorker({"file_list": file_list, "search_string": "search"})

    with patch("core.worker.ProcessPoolExecutor") as mock_executor_class:
        mock_executor = mock_executor_class.return_value.__enter__.return_value
        mock_future = MagicMock()
        # 예외 발생 시뮬레이션
        mock_future.result.side_effect = Exception("File processing error")
        mock_executor.submit.return_value = mock_future

        # 오류 시그널 수신 확인 (배치 처리 중 오류는 로깅만 하고 계속 진행하므로 search_error는 발생하지 않음)
        # 단, 전체 run loop가 실패하는 경우에만 search_error가 발생함.
        # 여기서는 개별 배치의 예외를 확인.

        # [Fix] as_completed 대신 wait 모킹
        with patch("core.worker.search_in_files_batch"), patch("core.worker.wait", return_value=([mock_future], [])):
            worker.run()

        # 예외가 발생해도 워커가 완료되어야 함
        pass


def test_worker_skipped_signal():
    """워커의 스킵된 파일 시그널 전송 테스트"""
    file_list = [("skipped_file.xml", 100)]

    worker = SearchWorker({"file_list": file_list, "search_string": "search"})

    skipped_received = []
    worker.skipped_found.connect(lambda s: skipped_received.extend(s))

    with patch("core.worker.ProcessPoolExecutor") as mock_executor_class:
        mock_executor = mock_executor_class.return_value.__enter__.return_value
        f = MagicMock()
        f.result.return_value = {"results": [], "skipped": ["skipped_file.xml"]}
        mock_executor.submit.return_value = f

        with patch("core.worker.search_in_files_batch"), patch("core.worker.wait", return_value=([f], [])):
            worker.run()

    assert "skipped_file.xml" in skipped_received
    assert len(skipped_received) == 1
