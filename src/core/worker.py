import multiprocessing
import multiprocessing.managers
import threading
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from typing import Any, List, Optional

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from core.search_engine import FileScanner, search_in_files_batch  # noqa: F401
from sf_utils.app_strings import AppStrings
from sf_utils.config_manager import ConfigManager
from sf_utils.constants import Constants
from sf_utils.logger import logger

def _get_adv_setting(key, default):
    return ConfigManager().get_advanced_settings().get(key, default)

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
                # 매니저가 이미 종료되었거나 유효하지 않은 경우 무시하고 진행합니다.
                _global_manager.shutdown()
                _global_manager = None
                logger.debug(AppStrings.LOG_PERF_MANAGER_SHUTDOWN)
            except (EOFError, ConnectionResetError, BrokenPipeError, AttributeError, OSError) as e:
                # IPC 통신 채널이 이미 닫혔거나 매니저 프로세스가 이미 종료된 경우
                logger.debug(AppStrings.LOG_WKR_MANAGER_SHUTDOWN_IGNORED.format(e))
            except Exception as e:
                # 기타 예상치 못한 종료 오류는 조용히 로깅만 수행
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
                # [L-04 Fix] _shutdown 비공개 속성 대신 submit()으로 유효성 검증
                # concurrent.futures의 공개 API를 사용하여 셧다운된 executor 감지
                try:
                    cls._executor.submit(lambda: None).cancel()
                except RuntimeError:
                    # RuntimeError: cannot schedule new futures after shutdown
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
    def shutdown(cls, wait=True, cancel_futures=True):
        """전역 실행기를 종료합니다. wait=True를 기본값으로 하여 서브 프로세스가 확실히 닫히게 합니다."""
        with cls._lock:
            if cls._executor:
                try:
                    logger.debug(f"Shutting down GlobalExecutor (wait={wait})")
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
        self._memory_alert_emitted = False

    def _safe_emit(self, signal, *args):
        try:
            signal.emit(*args)
        except RuntimeError:
            # Signal source has been deleted
            pass
        except Exception:
            pass

    def _stop_for_memory_pressure(self, diagnostic: str = "") -> None:
        """Stop the search and notify the UI once when memory pressure is detected."""
        self.stop_event.set()
        self.is_running.clear()
        if not self._memory_alert_emitted:
            self._memory_alert_emitted = True
            logger.warning("%s%s", AppStrings.ERROR_MEMORY_CRITICAL, f" ({diagnostic})" if diagnostic else "")
            self._safe_emit(self.signals.error, AppStrings.ERROR_MEMORY_CRITICAL)

    @staticmethod
    def _is_memory_skip(skip_list) -> bool:
        return any(
            "ERR_MEMORY_GUARD" in str(reason) or AppStrings.ERROR_MEMORY_CRITICAL in str(reason)
            for _, reason in skip_list
        )
    def _check_safety_limits(self, new_matches_count: int) -> bool:
        """전역 상한 및 메모리 가드 체크"""
        self._total_matches_accumulated += new_matches_count
        if self._total_matches_accumulated > _get_adv_setting(Constants.CONFIG_KEY_MAX_TOTAL_MATCHES, Constants.DEFAULT_MAX_TOTAL_MATCHES):
            err_msg = AppStrings.ERROR_LIMIT_REACHED.format(_get_adv_setting(Constants.CONFIG_KEY_MAX_TOTAL_MATCHES, Constants.DEFAULT_MAX_TOTAL_MATCHES))
            logger.warning(err_msg)
            self.stop_event.set()
            self.is_running.clear()
            self._safe_emit(self.signals.error, err_msg)
            return False

        now = time.time()
        if self._last_mem_check is None or (now - self._last_mem_check) > 1.0:
            try:
                from sf_utils.resource_guard import memory_pressure_detected, memory_pressure_message, memory_snapshot

                snapshot = memory_snapshot()
                if memory_pressure_detected(snapshot):
                    self._stop_for_memory_pressure(memory_pressure_message(snapshot))
                    return False
            except Exception as e:
                logger.debug(AppStrings.LOG_SYS_MEMORY_CHECK_FAIL.format(e))
            self._last_mem_check = now
        return True

    @Slot()
    def run(self):
        ext_info = ",".join(self.extensions) if self.extensions else "*"
        mode_info = self.special_mode if self.special_mode else Constants.MODE_NORMAL
        logger.info(AppStrings.LOG_WKR_STARTED.format(self.search_string, ext_info, mode_info))
        if not hasattr(self, "worker_start_time"):
            self.worker_start_time = time.time()
        # Legacy diagnostics/tests access all_results. Keep only a bounded
        # recent window; the complete result stream is delivered by signals.
        self.all_results: deque[Any] = deque(maxlen=Constants.WORKER_RESULT_RETENTION)
        self.all_skipped: List[Any] = []
        self.skipped_sheets_list: List[Any] = []  # [(file_path, sheet_name), ...] 시트 스킵 목록
        try:
            if self.use_complex_search:
                logger.info(AppStrings.LOG_WKR_COMPLEX_ACT)
                self._run_python_search()
            elif self.search_paths or self.file_list:
                # [H-04 Fix] 하드코딩된 상수 문자열 대신 직접 속성 참조
                self._run_rust_search(exclude_hidden=self.exclude_hidden, exclude_binary=self.exclude_binary)
            else:
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
        self._rust_found_files = 0
        self._rust_total_matches = 0

        self._last_progress_count = -1
        self._last_progress_emit_time = 0.0

        def progress_callback(count):
            if not self.is_running.is_set():
                return
            
            now = time.time()
            # [성능] 시그널 스패밍 방지: 동일 값은 500ms, 변화하는 값은 최소 100ms 간격으로 제한
            if count == self._last_progress_count:
                # 3초 이상 경과 시(하트비트 로그 목적) 가드 통과
                if now - self._last_progress_emit_time < 0.5 and (now - self._last_progress_emit_time) < 3.0:
                    return
            else:
                if now - self._last_progress_emit_time < 0.1:
                    return

            actual_total = max(total_files, count, 1)

            self._safe_emit(self.signals.progress_updated, count, actual_total)
            self._last_progress_count = count
            self._last_progress_emit_time = now

        def results_callback(batch):
            if not self.is_running.is_set():
                return
            # logger.debug(f"[Worker] results_callback received batch of {len(batch)}")
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
                match_tuples, marker_binary_count, sheet_skips = _normalize_rust_matches(matches, self.special_mode, existence_only=self.existence_only)
                # 시트 스킵 정보를 별도 목록에 수집 (파일 스킵과 구분)
                if sheet_skips and hasattr(self, "skipped_sheets_list"):
                    for sheet_name, detail in sheet_skips:
                        self.skipped_sheets_list.append((path, sheet_name, detail))
                if marker_binary_count > 0:
                    formatted_batch.append((path, marker_binary_count, [(1, AppStrings.MSG_BINARY_MATCH.format(marker_binary_count), None, None)]))
                    current_batch_matches += marker_binary_count
                elif match_tuples:
                    formatted_batch.append((path, len(match_tuples), match_tuples))
                    current_batch_matches += len(match_tuples)
            
            if not formatted_batch and not skipped_batch:
                return
            if skipped_batch and self._is_memory_skip(skipped_batch):
                self._stop_for_memory_pressure()
            if formatted_batch and not self._check_safety_limits(current_batch_matches):
                return
            if formatted_batch:
                self._rust_found_files += len(formatted_batch)
                self._rust_total_matches += current_batch_matches
                self.all_results.extend(formatted_batch)
                self._safe_emit(self.signals.results_found, formatted_batch)
            if skipped_batch:
                self._safe_emit(self.signals.skipped_found, skipped_batch)
            # logger.debug(f"[Worker] results_callback processing finished")

        try:
            if hasattr(self, Constants.PAYLOAD_FILE_LIST) and self.file_list:
                paths_only = [f[0] for f in self.file_list]
                search_res = search_files_list_fast(paths_only, self.search_string,
                    special_mode=self.special_mode, exclude_hidden=self.exclude_hidden,
                    exclude_binary=self.exclude_binary, stop_event=self.stop_event,
                    progress_callback=progress_callback, results_callback=results_callback)
            else:
                logger.info(AppStrings.LOG_WKR_RUST_ACT.format(len(self.search_paths)))
                search_res = search_directory_fast(self.search_paths, self.search_string, self.extensions,
                    filename_filter=self.filename_filter, special_mode=self.special_mode,
                    exclude_hidden=self.exclude_hidden, exclude_binary=self.exclude_binary,
                    stop_event=self.stop_event, progress_callback=progress_callback,
                    results_callback=results_callback, existence_only=self.existence_only)
        except Exception as e:
            error_msg = AppStrings.LOG_SCH_RUST_ENGINE_ERROR.format("SearchWorker.Batch", e)
            logger.error(error_msg, exc_info=True)
            self._safe_emit(self.signals.error, error_msg)
            self._safe_emit(self.signals.search_finished, 0, 0, 0)
            return

        total_found = getattr(self, "_rust_found_files", 0)
        total_matches = getattr(self, "_rust_total_matches", 0)
        skipped_list = []
        if isinstance(search_res, dict):
            skipped_list = search_res.get(Constants.PAYLOAD_SKIPPED, [])
        skipped_count = len(skipped_list) + len(getattr(self, "all_skipped", []))
        if skipped_list:
            if self._is_memory_skip(skipped_list):
                self._stop_for_memory_pressure()
            self._safe_emit(self.signals.skipped_found, skipped_list)
        # [Cleanup] 강제 100% 방출은 실제 스캔 수와 불일치할 수 있으므로 제거합니다.
        # 실제 진행률은 이미 Rust 콜백을 통해 정확한 숫자로 전달되었습니다.
        pass
        
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
        # 적응형 배치 전략 (Adaptive Batching): 파일 개수와 총 크기를 모두 고려합니다.
        batches = []
        current_batch: list[str] = []
        current_batch_size = 0
        max_batch_size = Constants.ADAPTIVE_BATCH_SIZE_THRESHOLD
        # 개수 기반 상한선도 두어 오버헤드 방지 (기존 BATCH_SIZE_NORMAL 활용)
        max_batch_count = getattr(Constants, "BATCH_SIZE_LARGE", 500) if total > 10000 else getattr(Constants, "BATCH_SIZE_NORMAL", 100)

        for f_info in files:
            f_path, f_size = f_info
            # 개별 파일이 너무 크면 단독 배치로 처리
            if f_size >= max_batch_size and not current_batch:
                batches.append([f_info])
                continue
            
            if len(current_batch) >= max_batch_count or (current_batch_size + f_size) > max_batch_size:
                if current_batch:
                    batches.append(current_batch)
                current_batch = [f_info]
                current_batch_size = f_size
            else:
                current_batch.append(f_info)
                current_batch_size += f_size
        
        if current_batch:
            batches.append(current_batch)

        self._executor = GlobalExecutor.get_executor(total_tasks=len(batches))
        executor = self._executor
        if not self.is_running.is_set():
            return (0, 0, 0)
            
        # 멀티프로세싱 직렬화 문제를 방지하기 위해 최상위 모듈 함수를 직접 참조합니다.
        from core.search_engine import search_in_files_batch as search_func
        
        future_to_batch = {}
        batch_iter = iter(batches)
        pending_futures = set()

        def submit_next_batch() -> bool:
            try:
                batch = next(batch_iter)
            except StopIteration:
                return False
            if not self.is_running.is_set():
                return False
            future = executor.submit(
                search_func,
                batch,
                self.search_string,
                self.special_mode,
                self.use_complex_search,
                self.stop_event,
                force_python,
                exclude_binary=self.exclude_binary,
                existence_only=self.existence_only,
            )
            future_to_batch[future] = batch
            pending_futures.add(future)
            return True

        try:
            max_in_flight = max(1, int(getattr(executor, "_max_workers", 1)))
            for _ in range(min(max_in_flight, len(batches))):
                if not submit_next_batch():
                    break
        except RuntimeError as e:
            # 시스템 종료 중에 작업이 제출될 경우 조용히 중단합니다. (사용자 명시적 중지 상황)
            if "after shutdown" in str(e):
                logger.debug(AppStrings.LOG_WKR_FUTURE_SCHEDULING_SKIPPED.format(e))
            else:
                raise
        except Exception as e:
            logger.error(AppStrings.LOG_WKR_FUTURE_SUBMISSION_FAIL.format(e))
            raise

        found_count = total_matches = skipped_count = completed = 0
        last_logged_percent = -1
        loop_start_time = time.time()
        try:
            while pending_futures:
                # 작업 지연이 임계치를 초과할 경우 강제로 종료하여 시스템 응답성을 유지합니다.
                if (time.time() - loop_start_time) > _get_adv_setting(Constants.CONFIG_KEY_TIMEOUT_WORKER_HANG, Constants.DEFAULT_TIMEOUT_WORKER_HANG):
                    err_msg = AppStrings.ERROR_LIMIT_REACHED.format(f"Timeout {_get_adv_setting(Constants.CONFIG_KEY_TIMEOUT_WORKER_HANG, Constants.DEFAULT_TIMEOUT_WORKER_HANG)}s")
                    logger.error(AppStrings.LOG_WKR_HANG_TIMEOUT.format(_get_adv_setting(Constants.CONFIG_KEY_TIMEOUT_WORKER_HANG, Constants.DEFAULT_TIMEOUT_WORKER_HANG)))
                    
                    # 타임아웃 발생 시 모든 하위 프로세스에 즉시 중지 신호를 보냅니다.
                    if self.stop_event:
                        self.stop_event.set()
                    
                    # 실행기를 즉시 종료하여 좀비 프로세스가 남지 않도록 합니다.
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
                                # 실질적인 매치 수(m[1])를 합산하여 집계 정확도를 높입니다.
                                current_matches = sum(m[1] for m in res_list)
                                if not self._check_safety_limits(current_matches):
                                    for f in list(pending_futures):
                                        f.cancel()
                                    pending_futures.clear()
                                    break
                                found_count += len(res_list)
                                total_matches += current_matches
                                self.all_results.extend(res_list)
                                self._safe_emit(self.signals.results_found, res_list)
                            if batch_res.get(Constants.PAYLOAD_SKIPPED):
                                skip_list = batch_res[Constants.PAYLOAD_SKIPPED]
                                self._safe_emit(self.signals.skipped_found, skip_list)
                                skipped_count += len(skip_list)
                                if self._is_memory_skip(skip_list):
                                    self._stop_for_memory_pressure()
                                    for pending in list(pending_futures):
                                        pending.cancel()
                                    pending_futures.clear()
                    except Exception as e:
                        logger.error(AppStrings.LOG_WKR_BATCH_FAILED.format(e))
                        # 배치 작업이 실패한 경우 해당 파일들을 검색 스킵으로 처리하여 보고합니다.
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
                    if self.is_running.is_set():
                        try:
                            submit_next_batch()
                        except RuntimeError as e:
                            logger.debug(AppStrings.LOG_WKR_FUTURE_SCHEDULING_SKIPPED.format(e))
        finally:
            # 로컬 참조를 해제하고 실행기의 생명주기 관리는 전역적으로 관리됩니다.
            # 다음 검색 시 _run_batch_search에서 새 Executor 할당.
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
                logger.debug(AppStrings.LOG_WKR_EXECUTOR_SHUTDOWN_ERROR.format(e))
            finally:
                self._executor = None
