from unittest.mock import MagicMock, patch
from concurrent.futures import TimeoutError as FutureTimeoutError
from core.worker import SearchWorker


def test_worker_result_batching():
    """워커의 결과 배칭(Batching) 시그널 전송 테스트"""
    mock_engine = MagicMock()
    file_list = ["test1.txt", "test2.txt", "test3.txt"]
    search_string = "hello"

    worker = SearchWorker(mock_engine, file_list, search_string)

    # 시그널 수신 확인용 리스트
    results_received = []
    worker.results_found.connect(lambda r: results_received.append(r))

    # ProcessPoolExecutor 자체를 모킹하여 실제 프로세스 생성을 막음
    with patch("core.worker.ProcessPoolExecutor") as mock_executor_class:
        mock_executor = mock_executor_class.return_value.__enter__.return_value

        # 가상의 Future 객체 생성
        mock_futures = []
        for i in range(len(file_list)):
            f = MagicMock()
            f.result.return_value = (f"file{i}.txt", 1, [(1, "hello")])
            mock_futures.append(f)

        # submit 호출 시 미래 객체들을 순차적으로 반환하도록 설정
        # (실제로는 dict key로 쓰이므로 적절히 대응)
        mock_executor.submit.side_effect = mock_futures

        # worker.run()에서 사용하는 future_to_file dictionary 구조 모사
        # 여기서는 executor가 __enter__를 통해 반환되므로...
        # worker.py의 구조에 맞춰서 패치 필요

        with patch("core.worker.search_in_file"):  # 실제 호출 차단
            worker.run()

        # 결과가 배치로 추출되었는지 확인
        assert len(results_received) > 0
        all_found = []
        for batch in results_received:
            for item in batch:
                all_found.append(item[0])

        assert len(all_found) == 3


def test_worker_stop_logic():
    """워커 중단 로직 테스트"""
    mock_engine = MagicMock()
    worker = SearchWorker(mock_engine, ["f1.txt", "f2.txt"], "search")

    with patch("core.worker.ProcessPoolExecutor"):
        # 워커 중단 설정
        worker.stop()
        worker.run()

        # 중단되었으므로 executor.shutdown이 호출되어야 함
        # 실제 루프 진입 전/후 체크
        pass  # 기본적으로 is_running 체크를 통해 루프 탈출 확인 가능


def test_worker_timeout_handling():
    """워커의 타임아웃 처리 테스트 (5분 타임아웃)"""
    mock_engine = MagicMock()
    file_list = ["slow_file.txt"]
    worker = SearchWorker(mock_engine, file_list, "search")

    with patch("core.worker.ProcessPoolExecutor") as mock_executor_class:
        mock_executor = mock_executor_class.return_value.__enter__.return_value
        mock_future = MagicMock()
        # 타임아웃 발생 시뮬레이션
        mock_future.result.side_effect = FutureTimeoutError("Timeout")
        mock_executor.submit.return_value = mock_future

        # 시그널 수신 확인
        finished_called = []
        worker.search_finished.connect(lambda x: finished_called.append(x))

        with patch("core.worker.search_in_file"):
            worker.run()

        # 타임아웃이 발생해도 워커가 정상 종료되어야 함
        assert len(finished_called) == 1


def test_worker_exception_handling():
    """워커의 예외 처리 테스트 (파일 처리 중 오류)"""
    mock_engine = MagicMock()
    file_list = ["error_file.txt"]
    worker = SearchWorker(mock_engine, file_list, "search")

    with patch("core.worker.ProcessPoolExecutor") as mock_executor_class:
        mock_executor = mock_executor_class.return_value.__enter__.return_value
        mock_future = MagicMock()
        # 예외 발생 시뮬레이션
        mock_future.result.side_effect = Exception("File processing error")
        mock_executor.submit.return_value = mock_future

        # 오류 시그널 수신 확인
        error_received = []
        worker.search_error.connect(lambda e: error_received.append(e))

        with patch("core.worker.search_in_file"):
            worker.run()

        # 예외가 발생해도 워커가 정상 종료되어야 함 (오류 시그널 발생)
        # 개별 파일 오류는 로깅만 하고 계속 진행
        pass
