from PySide6.QtCore import QObject, Signal
from utils.logger import logger
from utils.app_strings import AppStrings
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, TimeoutError as FutureTimeoutError
from core.search_engine import search_in_files_batch, FileScanner, EXCEL_EXTS


class SearchWorker(QObject):
    """
    실제 파일 검색 작업을 수행하는 백그라운드 워커 클래스입니다.
    QThread 상에서 실행되며, 검색 결과를 배치 단위로 묶어서 UI 스레드로 전송합니다.
    """

    # 진행 상황 알림 시그널 (현재 파일 수, 전체 파일 수)
    progress_updated = Signal(int, int)
    # 결과 발견 시그널 (배치 전송: list of (file_path, count, matches))
    results_found = Signal(list)
    # 스킵된 파일 발견 시그널 (배치 전송: list of file_paths)
    skipped_found = Signal(list)
    # 검색 완료 시그널 (찾은 파일 수, 스킵된 파일 수)
    search_finished = Signal(int, int)
    # 검색 에러 시그널
    search_error = Signal(str)
    # 워커 종료 최종 알림 (리소스 정리용)
    finished = Signal()

    def __init__(self, search_engine, file_list, search_string, special_mode=None, search_paths=None, extensions=None):
        """
        워커를 초기화합니다.

        Args:
            search_engine (SearchEngine): 검색을 수행할 엔진 인스턴스
            file_list (list): 검색 대상 파일 경로 리스트 (Legacy / Python 모드용)
            search_string (str): 검색할 문자열
            special_mode (str): 특수 검색 모드 (XML, JSON 등)
            search_paths (list, optional): [Rust전용] 검색할 루트 디렉토리 리스트
            extensions (list, optional): [Rust전용] 파일 확장자 필터
        """
        super().__init__()
        self.search_engine = search_engine
        self.file_list = file_list
        self.search_string = search_string
        self.special_mode = special_mode
        self.search_paths = search_paths
        self.extensions = extensions
        self.is_running = True

        # 성능 최적화: 검색 시작 전 정규식 패턴을 인코딩별로 미리 컴파일
        self.compiled_patterns = {}
        import re

        for encoding in ["utf-8", "cp949", "utf-16le"]:
            try:
                search_bytes = search_string.encode(encoding, errors="ignore")
                self.compiled_patterns[encoding] = re.compile(search_bytes, re.IGNORECASE)
            except Exception:
                pass

    def run(self):
        """
        백그라운드 비동기 검색을 수행하며 배치 단위로 결과를 취합하여 UI 성능을 보호합니다.
        [Phase 2] Rust 병렬 스캔이 가능한 경우(특수 모드 제외), Python 루프를 건너뛰고 Rust 엔진에 위임합니다.
        """
        from core.search_engine import HAS_RUST_ENGINE, search_directory_fast

        logger.info(AppStrings.LOG_WORKER_STARTED.format(self.search_string))

        # [Rust Engine Parallel Mode]
        # 조건:
        # 1. Rust 엔진 로드됨
        # 2. 특수 모드(XML/JSON 파싱 필요) 아님
        # 3. 루트 경로(search_paths)가 제공됨
        if HAS_RUST_ENGINE and not self.special_mode and self.search_paths:
            try:
                logger.info(AppStrings.LOG_RUST_ENGINE_ACTIVATED.format(len(self.search_paths)))
                self.progress_updated.emit(0, 100)  # 시작 알림

                # Rust 엔진 호출 (Blocking이지만 매우 빠름)
                search_res = search_directory_fast(self.search_paths, self.search_string, self.extensions)

                results_list = search_res.get("results", [])
                total_found = 0

                # 결과 방출 (UI 부담을 줄이기 위해 청크 단위로 보낼 수도 있지만,
                # Rust 엔진 결과가 리스트로 넘어오므로 일단 전체 방출하거나 쪼개서 방출)

                # 대량 결과 대응: 500개씩 끊어서 전송
                CHUNK_SIZE = 500
                total_files = len(results_list)

                for i in range(0, total_files, CHUNK_SIZE):
                    if not self.is_running:
                        break
                    chunk = results_list[i : i + CHUNK_SIZE]
                    self.results_found.emit(chunk)
                    total_found += len(chunk)

                    # 렌더링 틱을 위해 잠시 대기? (필요시)
                    # time.sleep(0.001)

                # [Fix] 엑셀 파일 보완 검색
                # Rust 엔진이 건너뛴 엑셀 파일들이 있다면 Python 로직으로 추가 검색 수행
                excel_selected = [
                    e for e in self.extensions if (e if e.startswith(".") else "." + e).lower() in EXCEL_EXTS
                ]

                if excel_selected and self.is_running:
                    logger.info(f"Excel 파일 보완 검색 시작: {excel_selected}")
                    # 엑셀 파일만 골라내는 스캐너 실행
                    scanner = FileScanner(
                        self.search_paths, excel_selected, stop_check_callback=lambda: not self.is_running
                    )
                    excel_files = scanner.scan()

                    if excel_files and self.is_running:
                        # 배치 검색 수행 (Excel은 개별 파일 로딩이 필요하므로 Python 엔진 사용)
                        batch_res = search_in_files_batch(excel_files, self.search_string, None, self.compiled_patterns)
                        if batch_res.get("results"):
                            excel_results = batch_res["results"]
                            self.results_found.emit(excel_results)
                            total_found += len(excel_results)

                # 최종 완료 알림
                self.progress_updated.emit(100, 100)
                logger.info(AppStrings.LOG_WORKER_FINISHED.format(total_found, f"{total_found}(Matches)"))
                self.search_finished.emit(total_found, 0)

            except Exception as e:
                logger.error(AppStrings.LOG_WORKER_ERROR.format(e), exc_info=True)
                self.search_error.emit(str(e))
            finally:
                self.finished.emit()
            return

        # [Legacy / Python Mode]
        try:
            completed = 0
            total = len(self.file_list)
            skipped_files = []
            found_count = 0

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
                    executor.submit(
                        search_in_files_batch, b, self.search_string, self.special_mode, self.compiled_patterns
                    ): b
                    for b in batches
                }

                last_logged_percent = -1
                for future in future_to_batch:
                    if not self.is_running:
                        executor.shutdown(wait=False, cancel_futures=True)
                        break

                    try:
                        batch_res = future.result(timeout=300)
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

                    except FutureTimeoutError:
                        logger.warning(AppStrings.LOG_BATCH_TIMEOUT)
                    except Exception as e:
                        logger.error(AppStrings.LOG_BATCH_ERROR.format(e))

                    batch = future_to_batch[future]
                    completed += len(batch)
                    percent = (completed * 100) // total
                    self.progress_updated.emit(completed, total)

                    if total >= 1000:
                        current_decile = percent // 10
                        if current_decile > last_logged_percent:
                            logger.info(AppStrings.LOG_WORKER_PROGRESS.format(percent, completed, total))
                            last_logged_percent = current_decile

            if self.is_running:
                # 최종 요약 보고 (스킵된 목록 로그 출력은 UI 스레드에서 처리하도록 유도하거나 여기서 수행)
                logger.info(AppStrings.LOG_WORKER_FINISHED.format(found_count, total))
                self.search_finished.emit(found_count, len(skipped_files))
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
        """실제 파일 스캔을 수행합니다. Rust 엔진 가용 시 Smart Scan을 수행합니다."""
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
                logger.info(AppStrings.LOG_SMART_SCAN_STARTED.format(self.search_string))
                found_files = find_files_with_keyword_fast(
                    self.selected_folders, self.search_string, self.selected_exts
                )

                # 파일명 필터가 있다면 2차 필터링 (Python side)
                if self.filename_filter:
                    import fnmatch

                    filtered_files = []
                    for f_info in found_files:
                        # f_info is (path, size)
                        if fnmatch.fnmatch(f_info[0], self.filename_filter):
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
        except Exception as e:
            logger.error(AppStrings.LOG_SCAN_TASK_FAILED.format(e), exc_info=True)
            self.scan_error.emit(str(e))
        finally:
            self.finished.emit()

    def stop(self):
        """스캔 작업을 중단합니다."""
        self.is_running = False
