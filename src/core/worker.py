from PySide6.QtCore import QObject, Signal
from utils.logger import logger
from utils.app_strings import AppStrings
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, TimeoutError as FutureTimeoutError
from core.search_engine import search_in_files_batch


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
        백그라운드 비동기 검색을 수행하며 배치 단위로 결과를 취합하여 UI 성능을 보호합니다.
        """
        logger.info(AppStrings.LOG_WORKER_STARTED.format(self.search_string))
        try:
            completed = 0
            total = len(self.file_list)
            found_count = 0
            all_results = []

            logger.info(AppStrings.LOG_WORKER_SCANNING.format(total))

            # 1. 시스템 자원 및 파일 규모에 따른 배치 사이즈 결정
            # IPC(인터프로세스 통신) 오버헤드를 줄이기 위해 대규모 검색 시 태스크를 묶어서 던집니다.
            batch_size = 500 if total > 10000 else 100
            batches = [self.file_list[i : i + batch_size] for i in range(0, total, batch_size)]
            
            # 대용량 파일 유무에 따른 워커 수 동적 조절 (메모리 안정성)
            LARGE_FILE_SIZE = 100 * 1024 * 1024
            large_files_count = sum(1 for _, size in self.file_list if size > LARGE_FILE_SIZE)
            max_workers = multiprocessing.cpu_count()
            if large_files_count > 0:
                max_workers = max(1, min(max_workers // 2, 4))

            with ProcessPoolExecutor(max_workers=max_workers) as executor:
                future_to_batch = {
                    executor.submit(search_in_files_batch, b, self.search_string): b 
                    for b in batches
                }

                last_logged_percent = -1
                for future in future_to_batch:
                    if not self.is_running:
                        try:
                            executor.shutdown(wait=False, cancel_futures=True)
                        except TypeError:
                            executor.shutdown(wait=False)
                        break

                    try:
                        # 배치 작업 결과를 수신 (300초 타임아웃)
                        batch_res = future.result(timeout=300)
                        if batch_res:
                            all_results.extend(batch_res)
                            found_count += len(batch_res)
                            # 결과가 발견될 때마다 UI 스레드로 뭉쳐서 보냅니다 (실시간 출력)
                            self.results_found.emit(batch_res)
                    except FutureTimeoutError:
                        logger.warning(AppStrings.LOG_BATCH_TIMEOUT)
                    except Exception as e:
                        logger.error(AppStrings.LOG_BATCH_ERROR.format(e))

                    # 배치가 끝날 때마다 진행률 정보를 방출합니다.
                    batch = future_to_batch[future]
                    completed += len(batch)
                    
                    percent = (completed * 100) // total
                    self.progress_updated.emit(completed, total)
                    
                    # UX 개선: 10% 단위로 로그 탭에 진행 상황을 확실히 출력합니다.
                    # 배치 크기로 인해 특정 퍼센트를 건너뛰더라도 다음 구간에서 반드시 출력되도록 보장합니다.
                    if total >= 1000:
                        current_decile = percent // 10
                        if current_decile > last_logged_percent:
                            logger.info(AppStrings.LOG_WORKER_PROGRESS.format(percent, completed, total))
                            last_logged_percent = current_decile

            if self.is_running:
                logger.info(AppStrings.LOG_WORKER_FINISHED.format(found_count, total))
                self.search_finished.emit(found_count)
            else:
                logger.info(AppStrings.LOG_WORKER_STOPPED)

        except (IOError, OSError) as e:
            logger.error(AppStrings.ERROR_IO_DURING_SEARCH.format(e), exc_info=True)
            self.search_error.emit(str(e))
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
