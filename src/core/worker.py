import multiprocessing
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from typing import Any, List, Optional, Union

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from core.search_engine import FileScanner, search_in_files_batch
from sf_utils.app_strings import AppStrings
from sf_utils.config_manager import ConfigManager
from sf_utils.constants import Constants
from sf_utils.logger import logger

_global_manager = None
_manager_lock = threading.Lock()


def get_global_manager():
    """get_global_manager 함수."""
    global _global_manager
    with _manager_lock:
        if _global_manager is None:
            try:
                _global_manager = multiprocessing.Manager()
                logger.debug(AppStrings.LOG_PERF_MANAGER_INIT)
            except Exception as e:
                logger.error(AppStrings.LOG_PERF_MANAGER_FAIL.format(e))
                return None
    return _global_manager


def shutdown_global_manager():
    """shutdown_global_manager 함수."""
    global _global_manager
    with _manager_lock:
        if _global_manager is not None:
            try:
                _global_manager.shutdown()
                _global_manager = None
                logger.debug(AppStrings.LOG_PERF_MANAGER_SHUTDOWN)
            except Exception as e:
                logger.error(f"Global manager shutdown error: {e}", exc_info=True)
                # 복구 불가능한 프로세스 중단 상황일 수 있으므로 로깅만 강화


class GlobalExecutor:
    _instance = None
    _executor = None
    _lock = threading.Lock()

    @classmethod
    def get_executor(cls, total_tasks: Optional[int] = None):
        with cls._lock:
            if cls._executor is not None:
                # [Fix 2-C] CPython 내부 속성(_broken, _shutdown_thread 등) 의존 제거
                # 버전 업그레이드 시 속성명 변경으로 상태 감지 실패 위험 존재
                # 수정: 소규모 Future 테스트(noop)로 Executor 생존 여부를 공식 API로 확인
                try:
                    test_future = cls._executor.submit(lambda: None)
                    test_future.result(timeout=0.5)
                except Exception:
                    # 테스트 실패 = Executor가 이미 셧다운/망가진 상태
                    cls._executor = None
            if cls._executor is None:
                # [개선] 적응형 워커 정책 (Adaptive Worker Policy)
                cpu_count = multiprocessing.cpu_count()
                if cpu_count <= 4:
                    # 저사양: 코어 1개를 시스템용으로 남겨두어 반응성 확보
                    max_workers = max(1, cpu_count - 1)
                elif cpu_count <= 8:
                    # 중간 사양: 전체 코어 사용 (이미 최적화된 경험적 수치)
                    max_workers = cpu_count
                else:
                    # 고사양: 75% 코어만 사용하여 I/O 병목 및 컨텍스트 스위칭 오버헤드 방지
                    max_workers = max(8, int(cpu_count * 0.75))

                # [개선] 작업량 기반 워커 제한 (Task-based Capping)
                # 배치(Batch) 수가 워커 수보다 적다면 배치 수만큼만 생성하여 오버헤드 최소화
                if total_tasks is not None:
                    max_workers = min(max_workers, total_tasks)

                logger.debug(AppStrings.LOG_WKR_ADAPTIVE_POLICY_INFO.format(cpu_count, max_workers))
                cls._executor = ProcessPoolExecutor(max_workers=max_workers)
            return cls._executor

    @classmethod
    def shutdown(cls, wait=False, cancel_futures=True):
        with cls._lock:
            if cls._executor:
                try:
                    cls._executor.shutdown(wait=wait, cancel_futures=cancel_futures)
                except Exception as e:
                    logger.error(AppStrings.LOG_EXECUTOR_SHUTDOWN_ERROR.format(e))
                finally:
                    cls._executor = None
        pass


class WorkerSignals(QObject):
    progress_updated = Signal(int, int)
    results_found = Signal(list)
    skipped_found = Signal(list)
    search_finished = Signal(int, int)
    scan_finished = Signal(list)
    error = Signal(str)
    finished = Signal()
    scan_started = Signal()


class SearchWorker(QRunnable):
    def __init__(self, params: dict):
        super().__init__()
        self.signals = WorkerSignals()
        self.setAutoDelete(True)
        self.file_list: List[Any] = params.get(Constants.PAYLOAD_FILE_LIST, [])
        self._executor: Optional[ProcessPoolExecutor] = None
        self.search_string = params.get(Constants.PAYLOAD_SEARCH_STRING, "")
        self.special_mode = params.get(Constants.PAYLOAD_SPECIAL_MODE)
        self.search_paths = params.get(Constants.PAYLOAD_SEARCH_PATHS, [])
        self.extensions: List[str] = params.get(Constants.PAYLOAD_EXTENSIONS, [])
        self.filename_filter = params.get(Constants.PAYLOAD_FILENAME_FILTER)
        self.use_complex_search = params.get(Constants.PAYLOAD_USE_COMPLEX_SEARCH, False)
        self.exclude_hidden = params.get(Constants.PAYLOAD_EXCLUDE_HIDDEN, True)
        self._last_progress_time: Optional[float] = None
        manager = get_global_manager()
        if manager:
            self.stop_event = manager.Event()
        else:
            # Manager 실패 시 threading.Event를 Fallback으로 사용 (멀티프로세싱 중단 신호 제한적이나 중단 유도)
            self.stop_event = threading.Event()
            logger.warning(AppStrings.LOG_WKR_STOP_EVENT_FALLBACK)
        if self.special_mode and self.special_mode.startswith(Constants.MODE_EXCEL):
            from core.search_engine import EXCEL_EXTS

            self.extensions = [ext.lstrip(".") for ext in EXCEL_EXTS]
            logger.debug(AppStrings.LOG_WKR_EXCEL_SCAN)
        self.is_running: bool = True
        self.config_manager: ConfigManager = ConfigManager()

    @Slot()
    def run(self):
        from core.search_engine import HAS_RUST_ENGINE

        ext_info = ",".join(self.extensions) if self.extensions else "*"
        mode_info = self.special_mode if self.special_mode else Constants.MODE_NORMAL
        logger.info(AppStrings.LOG_WKR_STARTED.format(self.search_string, ext_info, mode_info))
        if not hasattr(self, "worker_start_time"):
            self.worker_start_time = time.time()
        self.all_results: List[Any] = []
        self.all_skipped: List[Any] = []
        try:
            # [엔진 선택 정책]
            # 특별한 문자 검색(Complex Search) 옵션이 켜져 있으면 유니코드 정확성을 위해 Python 엔진을 사용합니다.
            if self.use_complex_search:
                logger.info(AppStrings.LOG_WKR_COMPLEX_ACT)
                self._run_python_search()
            elif HAS_RUST_ENGINE and self.search_paths:
                exclude_hidden = getattr(self, Constants.PAYLOAD_EXCLUDE_HIDDEN, True)
                self._run_rust_search(exclude_hidden=exclude_hidden)
            else:
                logger.info(AppStrings.LOG_WKR_PYTHON_ACT)
                self._run_python_search()
        except BaseException as e:
            logger.critical(AppStrings.LOG_WKR_BATCH_ERROR.format(e), exc_info=True)
            try:
                self.signals.error.emit(AppStrings.ERR_CRITICAL_SYSTEM.format(e))
            except RuntimeError:
                logger.debug(AppStrings.LOG_WORKER_ERROR_SIGNAL_FAIL)
        finally:
            try:
                self.signals.finished.emit()
            except RuntimeError:
                logger.debug(AppStrings.LOG_WORKER_FINISH_SIGNAL_FAIL)

    def _run_rust_search(self, exclude_hidden: bool = True):
        from core.search_engine import search_directory_fast, search_files_list_fast

        total_files = len(self.file_list) if self.file_list else 0

        def progress_callback(count):
            if not self.is_running:
                return
            try:
                self.signals.progress_updated.emit(count, total_files)
            except RuntimeError:
                pass

        try:
            if hasattr(self, Constants.PAYLOAD_FILE_LIST) and self.file_list:
                paths_only = [f[0] for f in self.file_list]
                search_res = search_files_list_fast(
                    paths_only,
                    self.search_string,
                    special_mode=self.special_mode,
                    exclude_hidden=exclude_hidden,
                    stop_event=self.stop_event,
                    progress_callback=progress_callback,
                )
            else:
                logger.info(AppStrings.LOG_WKR_RUST_ACT.format(len(self.search_paths)))
                search_res = search_directory_fast(
                    self.search_paths,
                    self.search_string,
                    self.extensions,
                    filename_filter=self.filename_filter,
                    special_mode=self.special_mode,
                    exclude_hidden=exclude_hidden,
                    stop_event=self.stop_event,
                    progress_callback=progress_callback,
                )
        except BaseException as e:
            logger.error(AppStrings.LOG_SCH_RUST_ENGINE_ERROR.format(e))
            logger.info(AppStrings.LOG_SYS_RUST_RUNTIME_FALLBACK)
            self._run_python_search()
            return
        results_list: List[Any] = search_res.get(Constants.PAYLOAD_RESULTS, []) or []
        skipped_list = search_res.get(Constants.PAYLOAD_SKIPPED, []) or []
        if hasattr(self, "all_results"):
            self.all_results.extend(results_list)
        if hasattr(self, "all_skipped"):
            self.all_skipped.extend(skipped_list)
        total_found = len(results_list)
        total_matches = sum(cnt for _, cnt, _ in results_list)
        skipped_count = len(skipped_list)
        if skipped_list:
            self.signals.skipped_found.emit(list(skipped_list))
        CHUNK_SIZE = 500
        for i in range(0, len(results_list), CHUNK_SIZE):
            if not self.is_running:
                break
            chunk = results_list[i : i + CHUNK_SIZE]
            try:
                self.signals.results_found.emit(list(chunk))
            except RuntimeError:
                logger.debug(AppStrings.LOG_WORKER_FINISH_SIGNAL_FAIL)
                break
        if not self.is_running:
            logger.info(AppStrings.LOG_WKR_STOPPED)
        else:
            self.signals.progress_updated.emit(100, 100)
        elapsed = time.time() - self.worker_start_time
        logger.info(AppStrings.LOG_WKR_DONE.format(total_found, total_matches, elapsed))
        try:
            self.signals.search_finished.emit(total_found, skipped_count)
        except RuntimeError:
            logger.debug(AppStrings.LOG_WORKER_FINISH_SIGNAL_FAIL)

    def _run_python_search(self):
        if not self.file_list and self.search_paths:
            try:
                scanner = FileScanner(
                    self.search_paths,
                    self.extensions,
                    filename_filter=self.filename_filter,
                    stop_check_callback=lambda: (not self.is_running)
                    or (self.stop_event is not None and self.stop_event.is_set()),
                    exclude_hidden=self.exclude_hidden,
                )
                self.file_list = scanner.scan()
            except BaseException as e:
                logger.error(AppStrings.LOG_WKR_BATCH_ERROR.format(e), exc_info=True)
                try:
                    self.signals.error.emit(AppStrings.ERR_CRITICAL_SYSTEM.format(e))
                except RuntimeError:
                    logger.debug(AppStrings.LOG_WORKER_ERROR_SIGNAL_FAIL)
                self.signals.search_finished.emit(0, 0)
                return
        if not self.file_list:
            logger.warning(AppStrings.LOG_SCH_NO_FILES)
            self.signals.search_finished.emit(0, 0)
            return
        logger.info(AppStrings.LOG_WKR_RUNNING.format(len(self.file_list)))
        found_count = 0
        total_matches = 0
        found_count, total_matches, skipped_count = self._run_batch_search(self.file_list, is_excel_fallback=False)
        if not self.is_running:
            logger.info(AppStrings.LOG_WKR_STOPPED)
        elapsed = time.time() - self.worker_start_time
        logger.info(AppStrings.LOG_WKR_DONE.format(found_count, total_matches, elapsed))
        self.signals.search_finished.emit(found_count, skipped_count)

    @staticmethod
    def _cancel_pending_futures(pending_futures):
        for future in list(pending_futures):
            try:
                future.cancel()
            except Exception as e:
                logger.debug(f"Future cancel error: {e}")
                # 작업 취소 실패는 무시 가능하나 로깅 수준을 유지함

    def _run_batch_search(self, files, is_excel_fallback=False):
        total = len(files)
        if total == 0:
            return 0 if is_excel_fallback else (0, 0, 0)
        batch_size = Constants.BATCH_SIZE_LARGE if total > 10000 else Constants.BATCH_SIZE_NORMAL
        batches = [files[i : i + batch_size] for i in range(0, total, batch_size)]
        self._executor = GlobalExecutor.get_executor(total_tasks=len(batches))
        executor = self._executor
        future_to_batch = {}
        found_count = 0
        total_matches = 0
        skipped_count = 0
        completed = 0
        last_logged_percent = -1
        try:
            future_to_batch = {
                executor.submit(
                    search_in_files_batch,
                    b,
                    self.search_string,
                    self.special_mode,
                    self.use_complex_search,
                    self.stop_event,
                ): b
                for b in batches
            }
            pending_futures = set(future_to_batch.keys())
            while pending_futures:
                if not self.is_running:
                    logger.info(AppStrings.LOG_EXECUTOR_STOPPING)
                    self._cancel_pending_futures(pending_futures)
                    pending_futures.clear()
                    break
                if not hasattr(self, "_last_progress_time") or self._last_progress_time is None:
                    self._last_progress_time = time.time()
                done, not_done = wait(pending_futures, timeout=1.0, return_when=FIRST_COMPLETED)
                if not done:
                    elapsed = time.time() - self._last_progress_time
                    if elapsed > Constants.TIMEOUT_WORKER_HANG:
                        logger.critical(AppStrings.LOG_WKR_HANG_DETECTED.format(elapsed))
                        self._cancel_pending_futures(pending_futures)
                        pending_futures.clear()
                        self.signals.error.emit(AppStrings.ERROR_WKR_HANG_RECOVERY)
                        break
                    continue
                self._last_progress_time = time.time()
                for future in done:
                    pending_futures.remove(future)
                    try:
                        batch_res = future.result()
                        if batch_res:
                            if batch_res.get(Constants.PAYLOAD_RESULTS):
                                res_list = batch_res[Constants.PAYLOAD_RESULTS]
                                found_count += len(res_list)
                                self.signals.results_found.emit(res_list)
                                total_matches += sum(cnt for _, cnt, _ in res_list)
                                if hasattr(self, "all_results"):
                                    self.all_results.extend(res_list)
                            if batch_res.get(Constants.PAYLOAD_SKIPPED):
                                skip_list = batch_res[Constants.PAYLOAD_SKIPPED]
                                self.signals.skipped_found.emit(skip_list)
                                skipped_count += len(skip_list)
                                if hasattr(self, "all_skipped"):
                                    self.all_skipped.extend(skip_list)
                    except Exception as e:
                        logger.error(AppStrings.LOG_WKR_BATCH_RETRY.format(e))
                        failed_batch = future_to_batch.get(future, [])
                        recovered = False
                        # 피클링 오류가 난 배치는 현재 프로세스에서 한 번 재시도합니다.
                        if failed_batch and "pickle" in str(e).lower():
                            try:
                                batch_res = search_in_files_batch(
                                    failed_batch,
                                    self.search_string,
                                    self.special_mode,
                                    self.use_complex_search,
                                    self.stop_event,
                                )
                                if batch_res:
                                    if batch_res.get(Constants.PAYLOAD_RESULTS):
                                        res_list = batch_res[Constants.PAYLOAD_RESULTS]
                                        found_count += len(res_list)
                                        self.signals.results_found.emit(res_list)
                                        total_matches += sum(cnt for _, cnt, _ in res_list)
                                        if hasattr(self, "all_results"):
                                            self.all_results.extend(res_list)
                                    if batch_res.get(Constants.PAYLOAD_SKIPPED):
                                        skip_list = batch_res[Constants.PAYLOAD_SKIPPED]
                                        self.signals.skipped_found.emit(skip_list)
                                        skipped_count += len(skip_list)
                                        if hasattr(self, "all_skipped"):
                                            self.all_skipped.extend(skip_list)
                                recovered = True
                            except Exception as fallback_err:
                                logger.error(AppStrings.LOG_WKR_BATCH_RETRY.format(fallback_err))
                        if failed_batch and not recovered:
                            failed_files = [f[0] for f in failed_batch[:5]]
                            logger.error(AppStrings.LOG_WKR_BATCH_FAILED_DETAIL.format(len(failed_batch), failed_files))
                            error_msg = AppStrings.LOG_WKR_BATCH_ERROR_DETAIL.format(len(failed_batch), e)
                            failed_skips = [(file_path, error_msg) for file_path, _ in failed_batch]
                            self.signals.skipped_found.emit(failed_skips)
                            skipped_count += len(failed_skips)
                            if hasattr(self, "all_skipped"):
                                self.all_skipped.extend(failed_skips)
                    batch = future_to_batch.get(future, [])
                    completed += len(batch)
                    percent = (completed * 100) // total
                    self.signals.progress_updated.emit(completed, total)
                    if total >= 1000:
                        current_decile = percent // 10
                        if current_decile > last_logged_percent:
                            logger.info(AppStrings.LOG_WKR_PROGRESS.format(percent, completed, total))
                            last_logged_percent = current_decile
        except Exception as e:
            if self.is_running:
                logger.error(AppStrings.ERR_BATCH_SEARCH_FAIL.format(e))
                raise e
        finally:
            self._executor = None
        return found_count, total_matches, skipped_count

    def stop(self):
        self.is_running = False
        if self.stop_event is not None:
            self.stop_event.set()
        logger.debug(AppStrings.LOG_WKR_STOP_SIGNAL)
        if hasattr(self, "_executor") and self._executor:
            try:
                logger.info(AppStrings.LOG_EXECUTOR_FORCE_STOP)
                # 전역 실행기를 직접 종료하지 않고 현재 워커의 중지 신호만 전달합니다.
                # (다른 워커 실행에 영향 방지)
                self._executor = None
            except Exception as e:
                logger.debug(AppStrings.LOG_EXECUTOR_SHUTDOWN_ERROR.format(e))
            finally:
                self._executor = None


class ScanWorker(QRunnable):
    def __init__(
        self,
        selected_folders: List[str],
        selected_exts: List[str],
        filename_filter: Optional[Union[str, List[str]]],
        search_string: Optional[str] = None,
        disable_smart_scan: bool = False,
        special_mode: Optional[str] = None,
        exclude_hidden: bool = True,
        use_complex_search: bool = False,
    ):
        super().__init__()
        self.signals = WorkerSignals()
        self.setAutoDelete(True)
        self.selected_folders: list = selected_folders
        self.selected_exts: list = selected_exts
        self.filename_filter = filename_filter
        self.search_string: Optional[str] = search_string
        self.disable_smart_scan: bool = disable_smart_scan
        self.special_mode: Optional[str] = special_mode
        self.exclude_hidden: bool = exclude_hidden
        self.use_complex_search: bool = use_complex_search
        self.is_running: bool = True
        self.stop_event = threading.Event()

    def _python_scan(self):
        scanner = FileScanner(
            self.selected_folders,
            self.selected_exts,
            self.filename_filter,
            stop_check_callback=lambda: (not self.is_running) or self.stop_event.is_set(),
            exclude_hidden=self.exclude_hidden,
        )
        return scanner.scan()

    @Slot()
    def run(self):
        self.signals.scan_started.emit()
        try:
            from core.search_engine import HAS_RUST_ENGINE, find_files_with_keyword_fast

            # [엔진 선택 정책]
            # '특별한 문자열 검색'이 켜져 있거나 Rust 엔진이 없으면 Python 스캔 사용
            use_rust_scan = (
                HAS_RUST_ENGINE
                and not self.use_complex_search
                and self.search_string
                and self.selected_folders
                and not self.disable_smart_scan
            )

            scan_type_str = AppStrings.LOG_SCH_SCAN_TYPE_RUST if use_rust_scan else AppStrings.LOG_SCH_SCAN_TYPE_PYTHON
            logger.info(AppStrings.LOG_SCH_SCAN_STARTED_TYPE.format(scan_type_str))

            scan_start_time = time.time()
            if use_rust_scan:
                if not self.search_string:
                    raise ValueError(AppStrings.LOG_SCH_EMPTY_QUERY)
                smart_scan_ret = find_files_with_keyword_fast(
                    self.selected_folders,
                    self.search_string,
                    self.selected_exts,
                    special_mode=self.special_mode,
                    filename_filter=self.filename_filter,
                    exclude_hidden=self.exclude_hidden,
                    stop_event=self.stop_event,
                    return_skipped=True,
                )
                if isinstance(smart_scan_ret, tuple) and len(smart_scan_ret) == 2:
                    found_files, smart_skipped = smart_scan_ret
                else:
                    found_files = smart_scan_ret
                    smart_skipped = []

                # [개선] 더 이상 전역 폴백을 하지 않고 발생한 스킵 목록만 관리합니다.
                if smart_skipped:
                    self.signals.skipped_found.emit(list(smart_skipped))
            else:
                found_files = self._python_scan()
                smart_skipped = []

            if self.is_running:
                scan_duration = time.time() - scan_start_time
                skipped_count = len(smart_skipped)

                # 결과 요약 로그 출력
                if skipped_count > 0:
                    logger.info(
                        AppStrings.LOG_SCH_SCAN_DONE_WITH_SKIPPED.format(len(found_files), skipped_count, scan_duration)
                    )
                    # 건너뛴 파일 목록 출력
                    logger.info(AppStrings.LOG_SCH_SKIPPED_FILES_LIST)
                    for path, reason in smart_skipped:
                        logger.info(f"   └─ {path} ({reason})")
                else:
                    logger.info(AppStrings.LOG_SCH_SCAN_DONE.format(len(found_files), scan_duration))

                try:
                    self.signals.scan_finished.emit(found_files)
                except RuntimeError:
                    logger.debug(AppStrings.LOG_WORKER_FINISH_SIGNAL_FAIL)
            else:
                logger.info(AppStrings.LOG_WKR_STOPPED)
            return
        except (IOError, OSError) as e:
            logger.error(AppStrings.ERROR_IO_GENERIC.format(e))
            self.signals.error.emit(str(e))
        except BaseException as e:
            logger.error(AppStrings.LOG_WKR_BATCH_ERROR.format(e), exc_info=True)
            self.signals.error.emit(str(e))
        finally:
            self.signals.finished.emit()

    def stop(self):
        self.is_running = False
        self.stop_event.set()
        logger.debug(AppStrings.LOG_WKR_STOP_SIGNAL)

    def cleanup(self):
        self.stop()
