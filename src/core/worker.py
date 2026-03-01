import multiprocessing
import multiprocessing.managers
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from typing import Any, List, Optional

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from core.search_engine import FileScanner, search_in_files_batch  # noqa: F401
from sf_utils.app_strings import AppStrings
from sf_utils.config_manager import ConfigManager
from sf_utils.constants import Constants
from sf_utils.logger import logger

_global_manager = None
_manager_lock = threading.Lock()


def get_global_manager():
    """전역 매니저 객체를 가져옵니다."""
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
    """전역 매니저 객체를 안전하게 종료합니다."""
    global _global_manager
    with _manager_lock:
        if _global_manager is not None:
            try:
                _global_manager.shutdown()
                _global_manager = None
                logger.debug(AppStrings.LOG_PERF_MANAGER_SHUTDOWN)
            except Exception as e:
                # [Extern Fix] 종료 시 소켓 관련 에러는 디버그 레벨로 처리하여 사용자 노출 최소화
                logger.debug(AppStrings.LOG_PERF_MANAGER_SHUTDOWN_ERROR.format(e))
            finally:
                _global_manager = None


class GlobalExecutor:
    _instance = None
    _executor = None
    _lock = threading.Lock()

    @classmethod
    def get_executor(cls, total_tasks: Optional[int] = None):
        with cls._lock:
            if cls._executor is not None:
                if getattr(cls._executor, "_shutdown", False):
                    cls._executor = None
            if cls._executor is None:
                cpu_count = multiprocessing.cpu_count()
                if cpu_count <= 4:
                    max_workers = max(1, cpu_count - 1)
                elif cpu_count <= 8:
                    max_workers = cpu_count
                else:
                    max_workers = max(8, int(cpu_count * 0.75))

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


class WorkerSignals(QObject):
    progress_updated = Signal(int, int)
    results_found = Signal(list)
    skipped_found = Signal(list)
    search_finished = Signal(int, int, int)
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
        self.exclude_binary = params.get(Constants.PAYLOAD_EXCLUDE_BINARY, True)
        self._last_progress_time: Optional[float] = None
        self.stop_event = threading.Event()
        if self.special_mode and self.special_mode.startswith(Constants.MODE_EXCEL):
            from core.search_engine import EXCEL_EXTS
            self.extensions = [ext.lstrip(".") for ext in EXCEL_EXTS]
            logger.debug(AppStrings.LOG_WKR_EXCEL_SCAN)
        self.is_running = threading.Event()
        self.is_running.set()
        self.existence_only = params.get(Constants.PAYLOAD_EXISTENCE_ONLY, False)
        self.config_manager: ConfigManager = ConfigManager()
        self._total_matches_accumulated = 0
        self._last_mem_check: Optional[float] = None

    def _safe_emit(self, signal, *args):
        try:
            signal.emit(*args)
        except Exception as e:
            logger.debug(f"[Worker] Signal emit failed ({signal}): {e}")

    def _check_safety_limits(self, new_matches_count: int) -> bool:
        """전역 상한 및 메모리 가드 체크"""
        self._total_matches_accumulated += new_matches_count
        if self._total_matches_accumulated > Constants.MAX_TOTAL_MATCHES:
            err_msg = AppStrings.ERROR_LIMIT_REACHED.format(Constants.MAX_TOTAL_MATCHES)
            logger.warning(err_msg)
            self.stop_event.set()
            self.is_running.clear()
            self._safe_emit(self.signals.error, err_msg)
            return False

        now = time.time()
        if self._last_mem_check is None or (now - self._last_mem_check) > 1.0:
            try:
                import psutil
                mem_percent = psutil.virtual_memory().percent
                if mem_percent > Constants.MEMORY_THRESHOLD_PERCENT:
                    err_msg = AppStrings.ERROR_MEMORY_CRITICAL
                    logger.warning(AppStrings.LOG_SYS_MEMORY_WARNING.format(err_msg, mem_percent))
                    self.stop_event.set()
                    self.is_running.clear()
                    self._safe_emit(self.signals.error, err_msg)
                    return False
            except Exception as e:
                logger.debug(AppStrings.LOG_SYS_MEMORY_CHECK_FAIL.format(e))
            self._last_mem_check = now
        return True

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
            if self.use_complex_search:
                logger.info(AppStrings.LOG_WKR_COMPLEX_ACT)
                self._run_python_search()
            elif HAS_RUST_ENGINE and (self.search_paths or self.file_list):
                exclude_hidden = getattr(self, Constants.PAYLOAD_EXCLUDE_HIDDEN, True)
                exclude_binary = getattr(self, Constants.PAYLOAD_EXCLUDE_BINARY, True)
                self._run_rust_search(exclude_hidden=exclude_hidden, exclude_binary=exclude_binary)
            else:
                if not HAS_RUST_ENGINE:
                    err_msg = AppStrings.LOG_SYS_RUST_NO_FALLBACK
                    logger.error(err_msg)
                    self._safe_emit(self.signals.error, err_msg)
                elif not self.search_paths and not self.file_list:
                    logger.warning(AppStrings.LOG_SCH_NO_FILES)
                self._safe_emit(self.signals.search_finished, 0, 0, 0)
                return
        except Exception as e:
            logger.critical(AppStrings.LOG_WKR_BATCH_ERROR.format(e), exc_info=True)
            self._safe_emit(self.signals.error, AppStrings.ERR_CRITICAL_SYSTEM.format(e))
        finally:
            self._safe_emit(self.signals.finished)

    def _run_rust_search(self, exclude_hidden: bool = True, exclude_binary: bool = True):
        from core.search_engine import search_directory_fast, search_files_list_fast
        total_files = len(self.file_list) if self.file_list else 100

        def progress_callback(count):
            if not self.is_running.is_set():
                return
            actual_total = max(total_files, count, 1)
            self._safe_emit(self.signals.progress_updated, count, actual_total)

        def results_callback(batch):
            if not self.is_running.is_set():
                return
            from core.search_engine import _normalize_rust_matches, _extract_marker_skip_reason
            formatted_batch = []
            skipped_batch = []
            current_batch_matches = 0
            for path, matches in batch:
                skip_reason = _extract_marker_skip_reason(matches)
                if skip_reason:
                    skipped_batch.append((path, skip_reason))
                    if hasattr(self, "all_skipped"):
                        self.all_skipped.append((path, skip_reason))
                    continue
                match_tuples, marker_binary_count = _normalize_rust_matches(matches, self.special_mode)
                if marker_binary_count > 0:
                    formatted_batch.append((path, marker_binary_count, [(1, AppStrings.MSG_BINARY_MATCH.format(marker_binary_count), None, None)]))
                    current_batch_matches += marker_binary_count
                elif match_tuples:
                    formatted_batch.append((path, len(match_tuples), match_tuples))
                    current_batch_matches += len(match_tuples)
            if not formatted_batch and not skipped_batch:
                return
            if formatted_batch and not self._check_safety_limits(current_batch_matches):
                return
            if formatted_batch:
                self._safe_emit(self.signals.results_found, formatted_batch)
                if hasattr(self, "all_results"):
                    self.all_results.extend(formatted_batch)
            if skipped_batch:
                self._safe_emit(self.signals.skipped_found, skipped_batch)

        try:
            if hasattr(self, Constants.PAYLOAD_FILE_LIST) and self.file_list:
                paths_only = [f[0] for f in self.file_list]
                search_res = search_files_list_fast(paths_only, self.search_string,
                    special_mode=self.special_mode, exclude_hidden=exclude_hidden,
                    exclude_binary=exclude_binary, stop_event=self.stop_event,
                    progress_callback=progress_callback, results_callback=results_callback)
            else:
                logger.info(AppStrings.LOG_WKR_RUST_ACT.format(len(self.search_paths)))
                search_res = search_directory_fast(self.search_paths, self.search_string, self.extensions,
                    filename_filter=self.filename_filter, special_mode=self.special_mode,
                    exclude_hidden=exclude_hidden, exclude_binary=exclude_binary,
                    stop_event=self.stop_event, progress_callback=progress_callback,
                    results_callback=results_callback, existence_only=self.existence_only)
        except Exception as e:
            error_msg = AppStrings.LOG_SCH_RUST_ENGINE_ERROR.format("SearchWorker.Batch", e)
            logger.error(error_msg, exc_info=True)
            self._safe_emit(self.signals.error, error_msg)
            self._safe_emit(self.signals.search_finished, 0, 0, 0)
            return

        total_found = len(self.all_results)
        total_matches = sum(cnt for _, cnt, _ in self.all_results)
        skipped_list = []
        if isinstance(search_res, dict):
            skipped_list = search_res.get(Constants.PAYLOAD_SKIPPED, [])
        skipped_count = len(skipped_list) + len(getattr(self, "all_skipped", []))
        if skipped_list:
            self._safe_emit(self.signals.skipped_found, skipped_list)
        if self.is_running.is_set():
            self._safe_emit(self.signals.progress_updated, 100, 100)
        self._safe_emit(self.signals.search_finished, total_found, total_matches, skipped_count)
        elapsed = time.time() - self.worker_start_time
        logger.info(AppStrings.LOG_WKR_DONE.format(total_found, total_matches, elapsed))

    def _run_python_search(self, force_python: bool = False):
        if not self.file_list and self.search_paths:
            try:
                scanner = FileScanner(self.search_paths, self.extensions, filename_filter=self.filename_filter,
                    stop_check_callback=lambda: (not self.is_running.is_set()) or (self.stop_event is not None and self.stop_event.is_set()),
                    exclude_hidden=self.exclude_hidden)
                self.file_list = scanner.scan()
            except Exception as e:
                logger.error(AppStrings.LOG_WKR_BATCH_ERROR.format(e), exc_info=True)
                self._safe_emit(self.signals.error, AppStrings.ERR_CRITICAL_SYSTEM.format(e))
                self._safe_emit(self.signals.search_finished, 0, 0, 0)
                return
        if not self.file_list:
            logger.warning(AppStrings.LOG_SCH_NO_FILES)
            self._safe_emit(self.signals.search_finished, 0, 0, 0)
            return
        logger.info(AppStrings.LOG_WKR_RUNNING.format(len(self.file_list)))
        found_count, total_matches, skipped_count = self._run_batch_search(self.file_list, force_python=force_python)
        elapsed = time.time() - self.worker_start_time
        logger.info(AppStrings.LOG_WKR_DONE.format(found_count, total_matches, elapsed))
        self._safe_emit(self.signals.search_finished, found_count, total_matches, skipped_count)

    def _run_batch_search(self, files, force_python=False):
        total = len(files)
        if total == 0:
            return (0, 0, 0)
        if not isinstance(self.stop_event, multiprocessing.managers.BaseProxy):
            manager = get_global_manager()
            if manager:
                old_event = self.stop_event
                self.stop_event = manager.Event()
                if old_event and hasattr(old_event, "is_set") and old_event.is_set():
                    self.stop_event.set()
        batch_size = Constants.BATCH_SIZE_LARGE if total > 10000 else Constants.BATCH_SIZE_NORMAL
        batches = [files[i : i + batch_size] for i in range(0, total, batch_size)]
        self._executor = GlobalExecutor.get_executor(total_tasks=len(batches))
        executor = self._executor
        # [Fix] 멀티프로세싱 직렬화 이슈 해결을 위해 최상위 모듈 함수 참조 보장
        from core.search_engine import search_in_files_batch as search_func
        future_to_batch = {executor.submit(search_func, b, self.search_string, self.special_mode, self.use_complex_search, self.stop_event, force_python, exclude_binary=self.exclude_binary, existence_only=self.existence_only): b for b in batches}
        pending_futures = set(future_to_batch.keys())
        found_count = total_matches = skipped_count = completed = 0
        last_logged_percent = -1
        loop_start_time = time.time()
        try:
            while pending_futures:
                # [상] 행(Hang) 타임아웃 도입: Constants.TIMEOUT_WORKER_HANG (600초) 경과 시 강제 종료
                if (time.time() - loop_start_time) > Constants.TIMEOUT_WORKER_HANG:
                    err_msg = AppStrings.ERROR_LIMIT_REACHED.format(f"Timeout {Constants.TIMEOUT_WORKER_HANG}s")
                    logger.error(f"[Worker] Batch search reached hang timeout ({Constants.TIMEOUT_WORKER_HANG}s). Force stopping.")
                    
                    # [제보 이슈 해결] 타임아웃 시 하위 프로세스에 즉시 중지 신호 전송
                    if self.stop_event:
                        self.stop_event.set()
                    
                    # [제보 이슈 해결] 실행기를 즉시 셧다운하여 좀비 프로세스 방지
                    if self._executor:
                        self._executor.shutdown(wait=False, cancel_futures=True)
                        self._executor = None
                    
                    for f in list(pending_futures):
                        f.cancel()
                    self._safe_emit(self.signals.error, err_msg)
                    break

                if not self.is_running.is_set():
                    for f in list(pending_futures):
                        f.cancel()
                    break
                done, _ = wait(pending_futures, timeout=1.0, return_when=FIRST_COMPLETED)
                if not done:
                    continue
                for future in done:
                    pending_futures.remove(future)
                    try:
                        batch_res = future.result()
                        if batch_res:
                            if batch_res.get(Constants.PAYLOAD_RESULTS):
                                res_list = batch_res[Constants.PAYLOAD_RESULTS]
                                # [중] 집계 정확도 수정: len(m[2])가 아닌 실질 매치 수 m[1]을 합산
                                current_matches = sum(m[1] for m in res_list)
                                if not self._check_safety_limits(current_matches):
                                    for f in list(pending_futures):
                                        f.cancel()
                                    pending_futures.clear()
                                    break
                                found_count += len(res_list)
                                total_matches += current_matches
                                self._safe_emit(self.signals.results_found, res_list)
                                if hasattr(self, "all_results"):
                                    self.all_results.extend(res_list)
                            if batch_res.get(Constants.PAYLOAD_SKIPPED):
                                skip_list = batch_res[Constants.PAYLOAD_SKIPPED]
                                self._safe_emit(self.signals.skipped_found, skip_list)
                                skipped_count += len(skip_list)
                    except Exception as e:
                        logger.error(f"Batch failed: {e}")
                        # [Integrity] 배치 전체 실패 시 모든 파일을 스킵으로 간주하여 보고 (테스트 복구)
                        batch_files = future_to_batch[future]
                        skip_list = [(f[0] if isinstance(f, (list, tuple)) else str(f), f"Batch Error: {e}") for f in batch_files]
                        self._safe_emit(self.signals.skipped_found, skip_list)
                        skipped_count += len(skip_list)
                        if hasattr(self, "all_skipped"):
                            self.all_skipped.extend(skip_list)
                    completed += len(future_to_batch[future])
                    percent = (completed * 100) // total
                    self._safe_emit(self.signals.progress_updated, completed, total)
                    if total >= 1000:
                        decile = percent // 10
                        if decile > last_logged_percent:
                            logger.info(AppStrings.LOG_WKR_PROGRESS.format(percent, completed, total))
                            last_logged_percent = decile
        finally:
            self._executor = None
        return found_count, total_matches, skipped_count

    def stop(self):
        self.is_running.clear()
        if self.stop_event is not None:
            self.stop_event.set()
        if hasattr(self, "_executor") and self._executor:
            try:
                self._executor.shutdown(wait=False, cancel_futures=True)
            except Exception as e:
                logger.debug(f"[Worker] Executor shutdown error: {e}")
            finally:
                self._executor = None
