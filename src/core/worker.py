from PySide6.QtCore import QObject, Signal
from utils.logger import logger
from utils.app_strings import AppStrings
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from core.search_engine import search_in_file


class SearchWorker(QObject):
    """
    실제 검색 작업을 수행하는 워커 클래스 (백그라운드 스레드에서 실행)
    """

    # 진행 상황 알림 시그널 (현재 파일 수, 전체 파일 수)
    progress_updated = Signal(int, int)
    # 결과 발견 시그널 (파일 경로, 찾은 횟수, 매칭 라인 데이터)
    result_found = Signal(str, int, list)
    # 검색 완료 시그널 (총 찾은 파일 수)
    search_finished = Signal(int)
    # 검색 에러 시그널
    search_error = Signal(str)
    # 워커 종료 최종 알림 (리소스 정리용)
    finished = Signal()

    def __init__(self, search_engine, file_list, search_string):
        super().__init__()
        self.search_engine = search_engine
        self.file_list = file_list
        self.search_string = search_string
        self.is_running = True

    def run(self):
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

            # 실제 구현에서는 ProcessPoolExecutor를 사용하되, result()를 순차적으로 받으며 신호 전송
            with ProcessPoolExecutor(max_workers=multiprocessing.cpu_count()) as executor:
                future_to_file = {executor.submit(search_in_file, f, self.search_string): f for f in self.file_list}

                for future in future_to_file:
                    if not self.is_running:
                        # 파이썬 3.9+에서 지원하는 cancel_futures=True를 활용하여 즉시 종료 시도
                        try:
                            executor.shutdown(wait=False, cancel_futures=True)
                        except TypeError:
                            executor.shutdown(wait=False)
                        break

                    res = future.result()
                    if res:
                        file_path, count, matches = res
                        self.result_found.emit(file_path, count, matches)
                        found_count += 1

                    completed += 1
                    # 1% 단위 또는 100개 단위로 진행 상황 업데이트 (UI 부하 감소)
                    if completed % max(1, (total // 100)) == 0 or completed == total:
                        self.progress_updated.emit(completed, total)

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
        self.is_running = False
