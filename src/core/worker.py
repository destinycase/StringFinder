from PySide6.QtCore import QObject, Signal
from utils.logger import logger
from utils.app_strings import AppStrings
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from core.search_engine import search_in_file
import time


class SearchWorker(QObject):
    """
    실제 파일 검색 작업을 수행하는 백그라운드 워커 클래스입니다.
    QThread 상에서 실행되며, 검색 결과를 배치 단위로 묶어서 UI 스레드로 전송합니다.
    """

    # 진행 상황 알림 시그널 (현재 파일 수, 전체 파일 수)
    progress_updated = Signal(int, int)
    # 결과 발견 시그널 (배치 전송: list of (file_path, count, matches))
    results_found = Signal(list)
    # 검색 완료 시그널 (총 찾은 파일 수)
    search_finished = Signal(int)
    # 검색 에러 시그널
    search_error = Signal(str)
    # 워커 종료 최종 알림 (리소스 정리용)
    finished = Signal()

    def __init__(self, search_engine, file_list, search_string):
        """
        워커를 초기화합니다.
        
        Args:
            search_engine (SearchEngine): 검색을 수행할 엔진 인스턴스
            file_list (list): 검색 대상 파일 경로 리스트
            search_string (str): 검색할 문자열
        """
        super().__init__()
        self.search_engine = search_engine
        self.file_list = file_list
        self.search_string = search_string
        self.is_running = True

    def run(self):
        """
        백그라운드에서 검색 루프를 실행합니다.
        ProcessPoolExecutor를 사용하여 CPU 코어 수만큼 병렬로 파일을 처리합니다.
        """
        logger.info(AppStrings.LOG_WORKER_STARTED.format(self.search_string))
        try:
            completed = 0
            total = len(self.file_list)
            found_count = 0

            logger.info(AppStrings.LOG_WORKER_SCANNING.format(total))

            # 검색 엔진의 병렬 검색을 여기서 실행
            # (참고: SearchEngine 내부에 이미 병렬 처리가 포함되어 있으므로 배치 단위로 끊어서 시그널 전송 가능)
            # 여기서는 메인 루프를 직접 돌면서 시그널을 즉시 쏘아줌 (실시간 피드백)
            # 단, 너무 잦은 시그널은 UI 성능을 저하시키므로 적절히 조절 필요

            # 배치 전송을 위한 버퍼
            result_buffer = []
            last_emit_time = time.time()
            BATCH_INTERVAL = 0.1  # 100ms
            BATCH_SIZE = 50

            with ProcessPoolExecutor(max_workers=multiprocessing.cpu_count()) as executor:
                future_to_file = {executor.submit(search_in_file, f, self.search_string): f for f in self.file_list}

                for future in future_to_file:
                    if not self.is_running:
                        # 사용자가 중지 버튼을 누른 경우, 실행 대기 중인 작업들을 취소하고 루프 탈출
                        try:
                            executor.shutdown(wait=False, cancel_futures=True)
                        except TypeError:
                            executor.shutdown(wait=False)
                        break

                    res = future.result()
                    if res:
                        result_buffer.append(res)
                        found_count += 1

                    completed += 1

                    # 배치 전송 조건 확인
                    current_time = time.time()
                    if result_buffer and (
                        len(result_buffer) >= BATCH_SIZE or (current_time - last_emit_time) >= BATCH_INTERVAL
                    ):
                        self.results_found.emit(result_buffer)
                        result_buffer = []
                        last_emit_time = current_time

                    # 진행 상황 업데이트
                    if completed % max(1, (total // 100)) == 0 or completed == total:
                        self.progress_updated.emit(completed, total)

            # 남은 결과 전송
            if result_buffer:
                self.results_found.emit(result_buffer)

            if self.is_running:
                logger.info(AppStrings.LOG_WORKER_FINISHED.format(found_count, total))
                self.search_finished.emit(found_count)
            else:
                logger.info(AppStrings.LOG_WORKER_STOPPED)

        except Exception as e:
            logger.error(AppStrings.LOG_WORKER_ERROR.format(str(e)), exc_info=True)
            self.search_error.emit(str(e))
        finally:
            self.finished.emit()

    def stop(self):
        """
        현재 진행 중인 검색 작업을 안전하게 중단하도록 플래그를 설정합니다.
        """
        self.is_running = False
