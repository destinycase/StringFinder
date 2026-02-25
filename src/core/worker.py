import multiprocessing
import multiprocessing.managers
import threading
import time
import psutil
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from typing import Any, List, Optional

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from core.search_engine import FileScanner, search_in_files_batch
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
                logger.error(AppStrings.LOG_PERF_MANAGER_SHUTDOWN_ERROR.format(e), exc_info=True)
            finally:
                _global_manager = None
                # 복구 불가능한 프로세스 중단 상황일 수 있으므로 로깅만 강화


class GlobalExecutor:
    _instance = None
    _executor = None
    _lock = threading.Lock()

    @classmethod
    def get_executor(cls, total_tasks: Optional[int] = None):
        with cls._lock:
            if cls._executor is not None:
                # 의미 없는(noop) Future 제출 + 0.5초 블로킹 제거
                # ProcessPoolExecutor._shutdown 은 .shutdown() 호출 즉시 True로 전환되는
                # CPython 3.x 전체에서 안정적으로 사용 가능한 공식 속성임.
                # 이 방식으로 블로킹 없이 Executor 생존 여부를 즉시 확인한다.
                if getattr(cls._executor, "_shutdown", False):
                    cls._executor = None
            if cls._executor is None:
                # 적응형 워커 정책 (Adaptive Worker Policy)
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

                # 작업량 기반 워커 제한 (작업 기반 워커 제한)
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
        # [M-05] Defer Manager initialization to improve performance for small file sets (Set A)
        # stop_event will be upgraded to a shared Manager.Event() only if a Python multiprocess search is needed.
        self.stop_event = threading.Event()
        if self.special_mode and self.special_mode.startswith(Constants.MODE_EXCEL):
            from core.search_engine import EXCEL_EXTS

            self.extensions = [ext.lstrip(".") for ext in EXCEL_EXTS]
            logger.debug(AppStrings.LOG_WKR_EXCEL_SCAN)
        # 비원자적인 bool 대신 threading.Event를 사용하여 스레드 안전성 확보
        self.is_running = threading.Event()
        self.is_running.set()
        self.is_boolean = params.get("is_boolean", False)
        self.config_manager: ConfigManager = ConfigManager()
        self._total_matches_accumulated = 0

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
            elif HAS_RUST_ENGINE and (self.search_paths or self.file_list):
                exclude_hidden = getattr(self, Constants.PAYLOAD_EXCLUDE_HIDDEN, True)
                exclude_binary = getattr(self, Constants.PAYLOAD_EXCLUDE_BINARY, True)
                self._run_rust_search(exclude_hidden=exclude_hidden, exclude_binary=exclude_binary)
            else:
                # [Policy] Python 엔진은 '특별한 문자열 검색' 시에만 동작함.
                # Rust 엔진이 없거나 검색 경로가 없는 경우 에러 처리하고 종료.
                if not HAS_RUST_ENGINE:
                    err_msg = AppStrings.LOG_SYS_RUST_NO_FALLBACK
                    logger.error(err_msg)
                    self.signals.error.emit(err_msg)
                elif not self.search_paths and not self.file_list:
                    logger.warning(AppStrings.LOG_SCH_NO_FILES)

                self.signals.search_finished.emit(0, 0, 0)
                return
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

    def _run_rust_search(self, exclude_hidden: bool = True, exclude_binary: bool = True):
        from core.search_engine import search_directory_fast, search_files_list_fast

        # file_list가 없는 경우(search_directory_fast 등) total_files가 0으로 고정되는 현상 방지
        total_files = len(self.file_list) if self.file_list else 100  # 임시 값, 아래에서 갱신됨

        def progress_callback(count):
            if not self.is_running.is_set():
                return
            try:
                # Rust 엔진에서 오는 count가 total_files보다 클 수 있으므로
                # 진행률이 100%를 넘지 않도록 가드하고, total_files가 0인 경우 방어
                actual_total = max(total_files, count, 1)
                self.signals.progress_updated.emit(count, actual_total)
            except RuntimeError:
                pass

        def results_callback(batch):
            if not self.is_running.is_set():
                return
            from core.search_engine import _normalize_rust_matches, _extract_marker_skip_reason

            formatted_batch = []
            skipped_batch = []
            for path, matches in batch:
                skip_reason = _extract_marker_skip_reason(matches)
                if skip_reason:
                    skipped_batch.append((path, skip_reason))
                    if hasattr(self, "all_skipped"):
                        self.all_skipped.append((path, skip_reason))
                    continue

                match_tuples, marker_binary_count = _normalize_rust_matches(matches, self.special_mode)
                if marker_binary_count > 0:
                    formatted_batch.append(
                        (
                            path,
                            marker_binary_count,
                            [(1, AppStrings.MSG_BINARY_MATCH.format(marker_binary_count), None, None)],
                        )
                    )
                elif match_tuples:
                    # [Safety] 파일당 매치 수 제한 적용
                    if len(match_tuples) > Constants.MAX_PER_FILE_MATCHES:
                        match_tuples = match_tuples[: Constants.MAX_PER_FILE_MATCHES]
                        match_tuples.append(
                            (
                                -1,
                                AppStrings.MSG_MATCH_LIMIT_PER_FILE.format(Constants.MAX_PER_FILE_MATCHES),
                                None,
                                None,
                            )
                        )
                    formatted_batch.append((path, len(match_tuples), match_tuples))

            if formatted_batch:
                # [Safety] 글로벌 매치 상한 및 메모리 가드 체크
                current_batch_matches = sum(cnt for _, cnt, _ in formatted_batch)
                self._total_matches_accumulated += current_batch_matches

                # 1. 글로벌 매치 상한 체크
                if self._total_matches_accumulated > Constants.MAX_TOTAL_MATCHES:
                    err_msg = AppStrings.ERROR_LIMIT_REACHED.format(Constants.MAX_TOTAL_MATCHES)
                    logger.warning(err_msg)
                    self.stop_event.set()
                    self.is_running.clear()
                    self.signals.error.emit(err_msg)
                    return

                # 2. 메모리 가드 체크 (주기적으로 수행)
                try:
                    mem_percent = psutil.virtual_memory().percent
                    if mem_percent > Constants.MEMORY_THRESHOLD_PERCENT:
                        err_msg = AppStrings.ERROR_MEMORY_CRITICAL
                        logger.warning(AppStrings.LOG_SYS_MEMORY_WARNING.format(err_msg, mem_percent))
                        self.stop_event.set()
                        self.is_running.clear()
                        self.signals.error.emit(err_msg)
                        return
                except Exception as e:
                    logger.debug(AppStrings.LOG_SYS_MEMORY_CHECK_FAIL.format(e))

                try:
                    self.signals.results_found.emit(formatted_batch)
                    # [Memory] 스트리밍 시에도 전체 결과 수집은 유지 (최종 통계 및 정렬용)
                    if hasattr(self, "all_results"):
                        self.all_results.extend(formatted_batch)
                except RuntimeError:
                    pass

            if skipped_batch:
                try:
                    self.signals.skipped_found.emit(skipped_batch)
                except RuntimeError:
                    pass

        # Removed no-op callback assignments (Item 7 from audit)

        # [상] H-01: 중지 시 UnboundLocalError 방지를 위해 변수 초기화 보장
        total_found = 0
        total_matches = 0
        skipped_count = 0
        search_res = {}

        try:
            if hasattr(self, Constants.PAYLOAD_FILE_LIST) and self.file_list:
                paths_only = [f[0] for f in self.file_list]
                search_res = search_files_list_fast(
                    paths_only,
                    self.search_string,
                    special_mode=self.special_mode,
                    exclude_hidden=exclude_hidden,
                    exclude_binary=exclude_binary,
                    stop_event=self.stop_event,
                    progress_callback=progress_callback,
                    results_callback=results_callback,
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
                    exclude_binary=exclude_binary,
                    stop_event=self.stop_event,
                    progress_callback=progress_callback,
                    results_callback=results_callback,
                    is_boolean=self.is_boolean,
                )
        except BaseException as e:
            error_msg = AppStrings.LOG_SCH_RUST_ENGINE_ERROR.format("SearchWorker.Batch", e)
            logger.error(error_msg, exc_info=True)
            try:
                self.signals.error.emit(error_msg)
            except RuntimeError:
                pass
            self.signals.search_finished.emit(0, 0, 0)
            return
        # [Optimization] Streaming search might return empty results in search_res
        # because results were already sent via callback. Use our accumulated stats.
        # However, for mocks or non-streaming paths, we should also check search_res.
        total_found = len(self.all_results)
        total_matches = sum(cnt for _, cnt, _ in self.all_results)

        # If all_results is empty but search_res has results (e.g. in Mocks)
        if not total_found and isinstance(search_res, dict) and Constants.PAYLOAD_RESULTS in search_res:
            res_list = search_res[Constants.PAYLOAD_RESULTS]
            for item in res_list:
                # Normalize manually if needed for stats
                if len(item) == 2:  # path, matches
                    self.all_results.append((item[0], len(item[1]), item[1]))
                else:
                    self.all_results.append(item)
            total_found = len(self.all_results)
            total_matches = sum(cnt for _, cnt, _ in self.all_results)

        # Use skipped list from search_res or our accumulation if we had one
        skipped_list = search_res.get(Constants.PAYLOAD_SKIPPED, []) if isinstance(search_res, dict) else []
        skipped_count = len(skipped_list) + len(getattr(self, "all_skipped", []))

        # [상] H-03: Rust 경로에서도 상세 skipped 항목 발행
        if skipped_list:
            self.signals.skipped_found.emit(skipped_list)

        if not self.is_running.is_set():
            logger.info(AppStrings.LOG_WKR_STOPPED)
        else:
            self.signals.progress_updated.emit(100, 100)

        self.signals.search_finished.emit(total_found, total_matches, skipped_count)
        elapsed = time.time() - self.worker_start_time
        logger.info(AppStrings.LOG_WKR_DONE.format(total_found, total_matches, elapsed))
        return total_found, total_matches, skipped_count

    def _run_python_search(self, force_python: bool = False):
        if not self.file_list and self.search_paths:
            try:
                scanner = FileScanner(
                    self.search_paths,
                    self.extensions,
                    filename_filter=self.filename_filter,
                    stop_check_callback=lambda: (
                        (not self.is_running.is_set()) or (self.stop_event is not None and self.stop_event.is_set())
                    ),
                    exclude_hidden=self.exclude_hidden,
                )
                self.file_list = scanner.scan()
            except BaseException as e:
                logger.error(AppStrings.LOG_WKR_BATCH_ERROR.format(e), exc_info=True)
                try:
                    self.signals.error.emit(AppStrings.ERR_CRITICAL_SYSTEM.format(e))
                except RuntimeError:
                    logger.debug(AppStrings.LOG_WORKER_ERROR_SIGNAL_FAIL)
                self.signals.search_finished.emit(0, 0, 0)
                return
        if not self.file_list:
            logger.warning(AppStrings.LOG_SCH_NO_FILES)
            self.signals.search_finished.emit(0, 0, 0)
            return
        logger.info(AppStrings.LOG_WKR_RUNNING.format(len(self.file_list)))
        found_count = 0
        total_matches = 0
        found_count, total_matches, skipped_count = self._run_batch_search(
            self.file_list, is_excel_fallback=False, force_python=force_python
        )
        if not self.is_running.is_set():
            logger.info(AppStrings.LOG_WKR_STOPPED)
        elapsed = time.time() - self.worker_start_time
        logger.info(AppStrings.LOG_WKR_DONE.format(found_count, total_matches, elapsed))
        self.signals.search_finished.emit(found_count, total_matches, skipped_count)

    @staticmethod
    def _cancel_pending_futures(pending_futures):
        for future in list(pending_futures):
            try:
                future.cancel()
            except Exception as e:
                logger.debug(AppStrings.LOG_PERF_FUTURE_CANCEL_ERROR.format(e))
                # 작업 취소 실패는 무시 가능하나 로깅 수준을 유지함

    def _run_batch_search(self, files, is_excel_fallback=False, force_python=False):
        total = len(files)
        if total == 0:
            return (0, 0, 0)

        # [M-05] Python 프로세스 풀 검색이 필요할 때만 매니저를 초기화하여 부하 최소화
        if not isinstance(self.stop_event, multiprocessing.managers.BaseProxy):
            manager = get_global_manager()
            if manager:
                old_event = self.stop_event
                self.stop_event = manager.Event()
                if old_event and hasattr(old_event, "is_set") and old_event.is_set():
                    self.stop_event.set()
                logger.debug(AppStrings.LOG_PERF_EVENT_UPGRADE)

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
                    force_python,
                ): b
                for b in batches
            }
            pending_futures = set(future_to_batch.keys())
            while pending_futures:
                if not self.is_running.is_set():
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
            if self.is_running.is_set():
                error_msg = AppStrings.ERR_BATCH_SEARCH_FAIL.format(e)
                logger.error(error_msg)
                # [Issue #4] 상위 예외 시 사용자에게 오류 알림 명시적 전달
                try:
                    self.signals.error.emit(error_msg)
                except RuntimeError:
                    pass
                # 예외 재발생(raise)을 제거하여 상위 run()의 finally까지 정상 도달하게 함
                # 대신 (0, 0, 0)을 반환하여 결과가 없음을 알림
        finally:
            self._executor = None
        return found_count, total_matches, skipped_count

    def stop(self):
        self.is_running.clear()
        if self.stop_event is not None:
            self.stop_event.set()
        logger.debug(AppStrings.LOG_WKR_STOP_SIGNAL)
        if hasattr(self, "_executor") and self._executor:
            try:
                logger.info(AppStrings.LOG_EXECUTOR_FORCE_STOP)
                self._executor.shutdown(wait=False, cancel_futures=True)
            except Exception as e:
                logger.debug(AppStrings.LOG_EXECUTOR_SHUTDOWN_ERROR.format(e))
            finally:
                self._executor = None
