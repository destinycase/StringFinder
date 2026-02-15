from PySide6.QtCore import QObject, Signal
from utils.logger import logger
from utils.app_strings import AppStrings
from utils.constants import Constants
import os
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from core.search_engine import search_in_files_batch, FileScanner, EXCEL_EXTS


# 배치 검색 타임아웃 (초) - 대용량 파일 검색 시 조절 가능


class SearchWorker(QObject):
    """
    실제 파일 검색 작업을 수행하는 백그라운드 워커 클래스입니다.
    QThread 상에서 실행되며, 검색 결과를 배치 단위로 묶어서 UI 스레드로 전송합니다.
    """

    # 진행 상황 알림 시그널 (현재 처리된 파일 수, 전체 대상 파일 수)
    progress_updated = Signal(int, int)
    # 검색 결과 발견 시그널 (배치 전송: [(파일 경로, 매칭 수, 상세 매칭 리스트), ...])
    results_found = Signal(list)
    # 스킵된 파일 발견 시그널 (배치 전송: [(파일 경로, 스킵 사유), ...])
    skipped_found = Signal(list)
    # 검색 작업 완료 시그널 (매칭된 총 파일 수, 스킵된 총 파일 수)
    search_finished = Signal(int, int)
    # 검색 과정 중 발생한 에러 시그널 (에러 메시지 전달)
    search_error = Signal(str)
    # 워커 종료 최종 알림 (리소스 정리용)
    finished = Signal()

    def __init__(self, params: dict):
        """
        워커를 초기화합니다.

        Args:
            params (dict): 검색 파라미터 딕셔너리
                - file_list (list): 검색 대상 파일 경로 리스트 (Legacy / Python 모드용)
                - search_string (str): 검색할 문자열
                - special_mode (str): 특수 검색 모드 (XML, JSON 등)
                - search_paths (list, optional): [Rust전용] 검색할 루트 디렉토리 리스트
                - extensions (list, optional): [Rust전용] 파일 확장자 필터
                - filename_filter (str 또는 list, optional): 파일명 필터링 조건
        """
        super().__init__()
        self.file_list = params.get("file_list", [])
        self.search_string = params.get("search_string", "")
        self.special_mode = params.get("special_mode")
        self.search_paths = params.get("search_paths")
        self.extensions = params.get("extensions")
        self.filename_filter = params.get("filename_filter")
        # 워커 실행 상태 플래그 (중단 요청 시 False로 변경됨)
        self.is_running = True

    def run(self):
        """
        백그라운드 비동기 검색을 수행하며 배치 단위로 결과를 취합하여 UI 성능을 보호합니다.

        [Phase 2/3 Strategy]
        1. Rust 엔진 가용 여부와 특수 모드(XML/JSON) 선택 여부를 판단합니다.
        2. Rust 엔진 사용 가능 시:
           - Python 루프를 건너뛰고 Rust의 병렬 검색(sf_engine)을 호출하여 극대화된 성능을 발휘합니다.
           - 검색 결과는 리스트 형태로 반환되며, 이를 적절한 청크(Chunk)로 나누어 UI에 전달합니다.
           - 엑셀 파일(.xlsx 등)은 Rust 엔진에서 처리 불가하므로, 별도의 Python 로직으로 후처리(Fallback)합니다.
        3. Legacy / Python Mode:
           - ProcessPoolExecutor를 사용하여 멀티 프로세스 검색을 수행합니다.
           - 대용량 파일 검색 시 메모리 사용량을 고려하여 배치 사이즈와 워커 수를 동적으로 조절합니다.
        """
        from core.search_engine import HAS_RUST_ENGINE, search_directory_fast

        logger.info(AppStrings.LOG_WKR_STARTED.format(self.search_string))

        # [Rust Engine Parallel Mode]
        # 조건:
        # 1. Rust 엔진 로드됨
        # 2. 특수 모드(XML/JSON 파싱 필요) 아님
        # 3. 루트 경로(search_paths)가 제공됨
        if HAS_RUST_ENGINE and not self.special_mode and self.search_paths:
            try:
                logger.info(AppStrings.LOG_WKR_RUST_ACT.format(len(self.search_paths)))
                self.progress_updated.emit(0, 100)  # 시작을 알리기 위해 0% 진행도 송신
                total_found = 0

                # [단계적 검색] 폴더 단위로 검색을 쪼개어 실행함으로써, 사용자의 중단 요청에 즉각 반응할 수 있도록 합니다.
                for path in self.search_paths:
                    if not self.is_running:
                        break

                    # Rust 엔진 호출 (폴더 단위로 개별 호출)
                    search_res = search_directory_fast([path], self.search_string, self.extensions)
                    results_list = search_res.get("results", [])

                    # 결과 방출 (UI 부담을 줄이기 위해 청크 단위로 보낼 수도 있지만,
                    # Rust 엔진 결과가 리스트로 넘어오므로 일단 전체 방출하거나 쪼개서 방출)

                    # 대량 결과 대응: 500개씩 끊어서 전송
                    CHUNK_SIZE = 500
                    total_files_in_path = len(results_list)

                    for i in range(0, total_files_in_path, CHUNK_SIZE):
                        if not self.is_running:
                            break
                        chunk = results_list[i : i + CHUNK_SIZE]
                        self.results_found.emit(chunk)
                        total_found += len(chunk)

                        # [GIL] 메인 UI 스레드가 이벤트를 처리할 수 있도록 아주 짧게 양보합니다.
                        import time

                        time.sleep(Constants.YIELD_SLEEP_TIME)

                    # 렌더링 틱을 위해 잠시 대기? (필요시)
                    # time.sleep(0.001)

                # [Fix] 엑셀 파일 보완 검색
                # Rust 엔진이 건너뛴 엑셀 파일들이 있다면 Python 로직으로 추가 검색 수행
                excel_selected = [
                    e for e in self.extensions if (e if e.startswith(".") else "." + e).lower() in EXCEL_EXTS
                ]

                if excel_selected and self.is_running:
                    logger.info(AppStrings.LOG_WKR_EXCEL_SCAN)
                    # 엑셀 파일만 골라내는 스캐너 실행 (파일명 필터 적용)
                    scanner = FileScanner(
                        self.search_paths,
                        excel_selected,
                        filename_filter=self.filename_filter,
                        stop_check_callback=lambda: not self.is_running,
                    )
                    excel_files = scanner.scan()

                    if excel_files and self.is_running:
                        logger.info(AppStrings.LOG_WKR_RUNNING.format(len(excel_files)))
                        # [Fix] 엑셀 검색 시에도 파일명 필터 적용 (FileScanner에서 이미 필터링되어 넘어오지만 명시적 유지)
                        # 배치 검색 수행 (Excel은 개별 파일 로딩이 필요하므로 Python 엔진 사용)
                        # [Responsive] 배치 검색 대신 개별/묶음 처리를 통해 중단 요청 수시 확인
                        excel_batch_size = 10
                        for i in range(0, len(excel_files), excel_batch_size):
                            if not self.is_running:
                                break
                            chunk = excel_files[i : i + excel_batch_size]
                            batch_res = search_in_files_batch(chunk, self.search_string, None)
                            if batch_res.get("results"):
                                excel_results = batch_res["results"]
                                self.results_found.emit(excel_results)
                                total_found += len(excel_results)

                            # [GIL] 메인 스레드에 제어권을 잠시 넘겨 중지 클릭 등을 처리하게 합니다.
                            import time

                            time.sleep(Constants.YIELD_SLEEP_TIME)

                if not self.is_running:
                    logger.info(AppStrings.LOG_WKR_STOPPED)

                # 최종 완료 알림
                self.progress_updated.emit(100, 100)
                logger.info(AppStrings.LOG_WKR_DONE.format(total_found, f"{total_found}(Matches)"))
                self.search_finished.emit(total_found, 0)

            except (IOError, OSError, RuntimeError) as e:
                logger.error(AppStrings.LOG_WKR_BATCH_ERROR.format(e), exc_info=True)
                self.search_error.emit(str(e))
            except Exception as e:
                # 예상치 못한 시스템 오류는 원본 에러를 최대한 남깁니다.
                logger.critical(AppStrings.LOG_WKR_BATCH_ERROR.format(e), exc_info=True)
                self.search_error.emit(f"Critical System Error: {e}")
            finally:
                self.finished.emit()
            return

        # [Legacy / Python Mode]
        try:
            completed = 0
            total = len(self.file_list)
            skipped_files = []
            found_count = 0

            logger.info(AppStrings.LOG_WKR_RUNNING.format(total))

            # 1. 시스템 자원 및 파일 규모에 따른 배치 사이즈 결정
            # IPC(인터프로세스 통신) 오버헤드를 줄이기 위해 대규모 검색 시 태스크를 묶어서 던집니다.
            batch_size = Constants.BATCH_SIZE_LARGE if total > 10000 else Constants.BATCH_SIZE_NORMAL
            batches = [self.file_list[i : i + batch_size] for i in range(0, total, batch_size)]

            # 대용량 파일이 포함된 경우, 메모리 부족을 방지하기 위해 동시 실행 워커 수를 제한합니다.
            large_files_count = sum(1 for _, size in self.file_list if size > Constants.THRESHOLD_LARGE_FILE)
            max_workers = multiprocessing.cpu_count()
            if large_files_count > 0:
                # 대용량 파일 처리 시 CPU 코어의 절반 이하 또는 최대 4개까지만 사용하도록 제한
                max_workers = max(1, min(max_workers // 2, 4))

            with ProcessPoolExecutor(max_workers=max_workers) as executor:
                future_to_batch = {
                    executor.submit(search_in_files_batch, b, self.search_string, self.special_mode): b for b in batches
                }

                last_logged_percent = -1

                pending_futures = set(future_to_batch.keys())

                while pending_futures:
                    if not self.is_running:
                        executor.shutdown(wait=False, cancel_futures=True)
                        break

                    # [Fix] as_completed 대신 wait 사용 (1초마다 상태 체크하여 좀비 프로세스 방지)
                    done, not_done = wait(pending_futures, timeout=1.0, return_when=FIRST_COMPLETED)

                    if not done:
                        # 1초 동안 완료된 작업이 없음 -> is_running 체크 후 계속 대기
                        # [Finding 4] 워커 루프 무한 대기 방지 (회로 차단)
                        if not hasattr(self, "_waited_seconds"):
                            self._waited_seconds = 0
                        self._waited_seconds += 1

                        # 10분 이상 완료된 작업이 없으면 무언가 잘못된 것으로 간주하고 강제 종료
                        if self._waited_seconds > Constants.TIMEOUT_WORKER_HANG:
                            logger.error(f"Search timeout: No progress for {Constants.TIMEOUT_WORKER_HANG}s. Aborting.")
                            self.is_running = False
                            break
                        continue

                    self._waited_seconds = 0  # 작업이 완료되었으므로 대기 시간 초기화

                    for future in done:
                        pending_futures.remove(future)
                        try:
                            # 이미 완료된 future이므로 timeout 없이 즉시 결과 반환 가능
                            # 단, future 자체의 예외(Timeout 등)는 여기서 catch
                            batch_res = future.result()

                            if batch_res:
                                # 결과 처리
                                if batch_res.get("results"):
                                    res_list = batch_res["results"]
                                    found_count += len(res_list)
                                    self.results_found.emit(res_list)

                                # 스킵된 파일 처리
                                if batch_res.get("skipped"):
                                    skip_list = batch_res["skipped"]
                                    skipped_files.extend(skip_list)
                                    self.skipped_found.emit(skip_list)

                        except Exception as e:
                            logger.error(AppStrings.LOG_WKR_BATCH_RETRY.format(e), exc_info=True)

                        batch = future_to_batch[future]
                        completed += len(batch)
                        percent = (completed * 100) // total
                        self.progress_updated.emit(completed, total)

                        if total >= 1000:
                            current_decile = percent // 10
                            if current_decile > last_logged_percent:
                                logger.info(AppStrings.LOG_WKR_PROGRESS.format(percent, completed, total))
                                last_logged_percent = current_decile

            # 최종 요약 보고 (중단된 경우에도 지금까지의 결과를 알림)
            if not self.is_running:
                logger.info(AppStrings.LOG_WKR_STOPPED)

            logger.info(AppStrings.LOG_WKR_DONE.format(found_count, total))
            self.search_finished.emit(found_count, len(skipped_files))

        except (IOError, OSError) as e:
            logger.error(AppStrings.ERROR_IO_DURING_SEARCH.format(e), exc_info=True)
            self.search_error.emit(str(e))
        except Exception as e:
            logger.error(AppStrings.LOG_WKR_ERROR.format(str(e)), exc_info=True)
            self.search_error.emit(str(e))
        finally:
            self.finished.emit()

    def stop(self):
        """
        현재 진행 중인 검색 작업을 안전하게 중단하도록 플래그를 설정합니다.
        """
        self.is_running = False
        logger.info(AppStrings.LOG_WKR_STOP_SIGNAL)


class ScanWorker(QObject):
    """
    파일 목록을 스캔하는 백그라운드 워커 클래스입니다.
    대규모 폴더 스캔 시 UI 프리징을 방지하기 위해 별도 스레드에서 작동합니다.
    """

    # 스캔 시작 알림
    scan_started = Signal()
    # 스캔 완료 시그널 (파일 목록 리스트 전달)
    scan_finished = Signal(list)
    # 스캔 오류 시그널
    scan_error = Signal(str)
    # 워커 종료 최종 알림
    finished = Signal()

    def __init__(self, selected_folders, selected_exts, filename_filter, search_string=None):
        super().__init__()
        self.selected_folders = selected_folders
        self.selected_exts = selected_exts
        self.filename_filter = filename_filter
        self.search_string = search_string
        self.is_running = True

    def run(self):
        """
        실제 파일 스캔을 수행합니다. Rust 엔진 가용 시 Smart Scan을 수행합니다.

        [Process Flow]
        1. Rust 엔진(sf_engine)이 로드되어 있고, 검색어(키워드)가 있는 경우 'Smart Scan'을 우선 시도합니다.
           - Smart Scan: 파일 내용을 모두 읽지 않고, 키워드가 포함된 파일 목록만 빠르게 추출합니다.
           - 추출된 목록에 대해 파일명 필터(Wildcard)를 2차적으로 적용합니다.
        2. 그 외의 경우(Legacy):
           - Python의 os.walk/scandir을 사용하여 순차적으로 디렉토리를 순회합니다.
           - 확장자 및 파일명 필터를 적용하여 검색 대상 파일을 수집합니다.
        """
        self.scan_started.emit()
        try:
            from core.search_engine import HAS_RUST_ENGINE, find_files_with_keyword_fast

            # [Phase 3 Smart Scan condition]
            # 1. Rust 엔진 사용 가능
            # 2. 검색어(search_string)가 존재함
            # 3. 파일명 필터가 없음 (Rust 엔진은 파일명 필터 미지원, 확장자 필터만 지원하므로)
            #    -> 파일명 필터가 있으면 Python으로 돌려서 필터링해야 안전함.
            #    -> 또는 Rust가 다 가져온 뒤 Python에서 2차 필터링? (이게 나음)
            #    -> 근데 find_files_with_keyword는 '내용'을 검사함. 파일명 필터랑 무관하게 내용은 있어야 함.
            #    -> 따라서 Rust로 내용 있는 것만 추린 뒤, Python에서 파일명 필터 적용하면 됨.

            use_smart_scan = HAS_RUST_ENGINE and self.search_string and self.selected_folders

            if use_smart_scan:
                # Rust Smart Scan: 내용이 있는 파일만 1차적으로 가져옴
                logger.info(AppStrings.LOG_SCH_SMART_SCAN_STARTED.format(self.search_string))
                found_files = []

                # [Granular Scan] 폴더 단위로 쪼개어 호출함으로써 중단 응답성 확보
                for folder in self.selected_folders:
                    if not self.is_running:
                        break

                    folder_files = find_files_with_keyword_fast([folder], self.search_string, self.selected_exts)
                    found_files.extend(folder_files)

                # 파일명 필터가 있다면 2차 필터링 (Python side)
                if self.filename_filter and self.is_running:
                    import fnmatch

                    filtered_files = []
                    # 필터가 단일 문자열이면 리스트로 변환하여 처리 통일
                    filters = [self.filename_filter] if isinstance(self.filename_filter, str) else self.filename_filter

                    for f_info in found_files:
                        path = f_info[0]
                        filename = os.path.basename(path)
                        # 여러 필터 중 하나라도 매칭되면 포함 (OR 조건)
                        is_matched = False
                        for f_pattern in filters:
                            # fnmatch는 와일드카드 지원, 단순 포함 여부는 *pattern* 형태로 처리 가능
                            # 사용자가 입력한 단어가 포함되기만 하면 되므로 *word* 형태로 체크
                            pattern = f"*{f_pattern}*" if "*" not in f_pattern else f_pattern
                            if fnmatch.fnmatch(filename.lower(), pattern.lower()):
                                is_matched = True
                                break

                        if is_matched:
                            filtered_files.append(f_info)
                    found_files = filtered_files

                if self.is_running:
                    self.scan_finished.emit(found_files)
                return

            # Legacy Scan (Python os.walk)
            scanner = FileScanner(
                self.selected_folders,
                self.selected_exts,
                self.filename_filter,
                stop_check_callback=lambda: not self.is_running,
            )
            file_list = scanner.scan()

            if self.is_running:
                self.scan_finished.emit(file_list)
        except (IOError, OSError) as e:
            logger.error(AppStrings.ERROR_IO_GENERIC.format(e))
            self.scan_error.emit(str(e))
        except Exception as e:
            logger.error(AppStrings.LOG_WKR_BATCH_ERROR.format(e), exc_info=True)
            self.scan_error.emit(str(e))
        finally:
            self.finished.emit()

    def stop(self):
        """스캔 작업을 중단합니다."""
        self.is_running = False
        logger.info(AppStrings.LOG_WKR_STOP_SIGNAL)
