from PySide6.QtCore import QObject, Signal, QRunnable, Slot
from sf_utils.logger import logger
from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants
import os
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from core.search_engine import search_in_files_batch, FileScanner, EXCEL_EXTS
from sf_utils.config_manager import ConfigManager
from typing import List, Optional, Any
import threading
import time

# [성능 최적화] 전역 Manager 인스턴스를 공유하여 워커 생성 오버헤드 제거
_global_manager = None
_manager_lock = threading.Lock()


def get_global_manager():
    """전역 멀티프로세싱 매니저를 반환합니다. (지연 초기화)"""
    global _global_manager
    with _manager_lock:
        if _global_manager is None:
            try:
                # 윈도우 환경에서 안정적인 생성을 위해 시도
                _global_manager = multiprocessing.Manager()
                logger.debug(AppStrings.LOG_PERF_MANAGER_INIT)
            except Exception as e:
                logger.error(AppStrings.LOG_PERF_MANAGER_FAIL.format(e))
                return None
    return _global_manager


def shutdown_global_manager():
    """앱 종료 시 매니저 프로세스를 안전하게 종료합니다."""
    global _global_manager
    with _manager_lock:
        if _global_manager is not None:
            try:
                _global_manager.shutdown()
                _global_manager = None
                logger.debug(AppStrings.LOG_PERF_MANAGER_SHUTDOWN)
            except Exception:
                pass


class GlobalExecutor:
    _instance = None
    _executor = None
    _lock = threading.Lock()

    @classmethod
    def get_executor(cls):
        with cls._lock:
            # [v4.31.0 Fix] 이미 종료된 executor가 반환되는 것을 방지
            if cls._executor is not None:
                # 비공개 속성이지만 대부분의 파이썬 3.7+ 환경에서 작동하는 체크 방식
                # 혹은 간단한 submit 시도를 통해 유효성 검증
                if (
                    getattr(cls._executor, "_shutdown_thread", False)
                    or getattr(cls._executor, "_broken", False)
                    or getattr(cls._executor, "_closing", False)
                ):
                    cls._executor = None

            if cls._executor is None:
                max_workers = multiprocessing.cpu_count()
                max_workers = max(1, min(max_workers, 8))
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

        # [v4.29.3 Fix] 전역 active_children 강제 종료 루프 제거.
        # 이 앱의 Executor가 관리하는 프로세스 외의 다른 라이브러리/시스템 프로세스를 건드리지 않도록 합니다.
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

        self.file_list: List[Any] = params.get("file_list", [])
        self._executor: Optional[ProcessPoolExecutor] = None
        self.search_string = params.get("search_string", "")
        self.special_mode = params.get("special_mode")
        self.search_paths = params.get("search_paths", [])
        self.extensions: List[str] = params.get("extensions", [])
        self.filename_filter = params.get("filename_filter")
        self._last_progress_time: Optional[float] = None

        # 중지 이벤트를 전역 Manager로 공유하여 생성 오버헤드 제거
        manager = get_global_manager()
        self.stop_event = manager.Event() if manager else None

        if self.special_mode and self.special_mode.startswith(Constants.MODE_EXCEL):
            from core.search_engine import EXCEL_EXTS

            self.extensions = [ext.lstrip(".") for ext in EXCEL_EXTS]
            logger.info(AppStrings.LOG_WKR_EXCEL_SCAN)

        self.is_running: bool = True
        self.config_manager: ConfigManager = ConfigManager()
        self.cache_enabled: bool = params.get("cache_enabled", self.config_manager.get_cache_enabled())
        try:
            from core.search_cache import HybridSearchCache
        except ImportError:
            pass

        self.cache: Optional[HybridSearchCache] = None
        self.cache_config: dict = {
            "dir": self.config_manager.get_cache_dir(),
            "max": self.config_manager.get_cache_max_results(),
            "persist": self.config_manager.get_cache_persist(),
        }

    @Slot()
    def run(self):
        from core.search_engine import HAS_RUST_ENGINE

        logger.info(AppStrings.LOG_WKR_STARTED.format(self.search_string))

        if self.cache_enabled and self.cache is None:
            try:
                from core.search_cache import HybridSearchCache

                self.cache = HybridSearchCache(
                    self.cache_config["dir"], self.cache_config["max"], self.cache_config["persist"]
                )
                logger.debug(AppStrings.LOG_CACHE_INIT_COMPLETE)
            except Exception as e:
                logger.warning(AppStrings.LOG_CACHE_INIT_FAIL.format(e))
                self.cache_enabled = False

        cache_key = None
        if self.cache_enabled and self.cache:
            try:
                cache_key = self.cache._get_cache_key(
                    self.search_string,
                    self.search_paths or [],
                    self.extensions or [],
                    self.special_mode,
                    self.filename_filter,
                )
                logger.debug(AppStrings.LOG_CACHE_KEY.format(cache_key[:16]))
            except Exception as e:
                logger.warning(AppStrings.LOG_CACHE_KEY_FAIL.format(e))

        self.all_results: List[Any] = []
        self.all_skipped: List[Any] = []

        if cache_key and self.cache:
            try:
                # [v4.29.5] get_with_meta로 단일 조회하여 중복 호출 방지 및 메타데이터 확보
                cache_entry = self.cache.result_cache.get_with_meta(cache_key)
                logger.debug(AppStrings.LOG_CACHE_KEY_GEN.format(cache_key, cache_entry is not None))

                if cache_entry is not None:
                    # [v4.32.2 Fix] 캐시 데이터 구조: (results, skipped) 튜플 or results 리스트 (하위호환)
                    cached_data, paths_meta_to_check = cache_entry

                    if isinstance(cached_data, tuple) and len(cached_data) == 2:
                        cached_results, cached_skipped = cached_data
                    else:
                        cached_results = cached_data
                        cached_skipped = []

                    results_len = len(cached_results)
                    logger.debug(AppStrings.LOG_CACHE_FOUND.format(results_len))

                    should_use_cache = True
                    files_to_check = []

                    if self.file_list:
                        for item in self.file_list:
                            if isinstance(item, tuple) and len(item) >= 1:
                                files_to_check.append(item[0])
                            elif isinstance(item, str):
                                files_to_check.append(item)
                    elif cached_results:
                        # [v4.32.0 Fix] 결과가 있는 경우에도 신규 파일 추가 감지를 위해
                        # 아래에서 paths_meta_to_check 검증을 생략하지 않도록 합니다.
                        for res in cached_results:
                            if isinstance(res, (list, tuple)) and len(res) > 0:
                                files_to_check.append(res[0])

                    # 1단계: 기존 결과 파일들의 변경 여부 확인
                    if files_to_check:
                        if self.cache._any_file_changed(files_to_check, self.search_string):
                            should_use_cache = False
                            logger.debug(AppStrings.LOG_CACHE_FILE_CHANGED)

                    # 2단계: 신규 파일 추가/삭제 여부 확인 (디렉토리 시그너처 상시 검증)
                    if should_use_cache and paths_meta_to_check:
                        current_meta = self.cache._get_paths_metadata(self.search_paths or [])
                        if current_meta != paths_meta_to_check:
                            should_use_cache = False
                            logger.debug(AppStrings.LOG_CACHE_SCAN_DIR_CHANGE)

                    if not self.file_list and not cached_results and not paths_meta_to_check:
                        # 결과도 없고 메타데이터도 없는 초기 빈 캐시
                        should_use_cache = False

                    if should_use_cache:
                        logger.info(AppStrings.LOG_CACHE_HIT.format(len(cached_results)))

                        CHUNK_SIZE = 500
                        for i in range(0, len(cached_results), CHUNK_SIZE):
                            chunk = cached_results[i : i + CHUNK_SIZE]
                            self.signals.results_found.emit(chunk)

                        if cached_skipped:
                            self.signals.skipped_found.emit(cached_skipped)

                        self.signals.search_finished.emit(len(cached_results), len(cached_skipped))
                        return
                    else:
                        logger.debug(AppStrings.LOG_CACHE_FILE_CHANGED)
                else:
                    logger.debug(AppStrings.LOG_CACHE_MISS)
            except Exception as e:
                logger.warning(AppStrings.LOG_CACHE_CHECK_FAIL.format(e))

        try:
            if HAS_RUST_ENGINE and not self.special_mode and self.search_paths:
                self._run_rust_search()
            else:
                self._run_python_search()

            if self.cache_enabled and self.cache and cache_key:
                # [개선] 결과가 0건(Empty)인 경우에도 캐시하여 반복 스캔 비용 절감
                try:
                    logger.debug(AppStrings.LOG_CACHE_SYNC_START.format(len(self.all_results)))

                    # [v4.29.5] 결과 저장 시 검색 경로 메타데이터를 함께 보관 (0건 결과 무효화용)
                    paths_meta = self.cache._get_paths_metadata(self.search_paths)
                    self.cache.result_cache.put(cache_key, (self.all_results, self.all_skipped), meta=paths_meta)

                    logger.debug(AppStrings.LOG_CACHE_LRU_UPDATED)

                    file_cache_count = 0
                    for result in self.all_results:
                        try:
                            if isinstance(result, tuple) and len(result) >= 1:
                                file_path = result[0]
                            elif isinstance(result, dict):
                                file_path = result.get("file_path")
                            else:
                                continue

                            if file_path:
                                import os

                                stat = os.stat(file_path)
                                # [v4.33.2 Fix] 캐시 키 정규화 및 스키마 업데이트
                                # search_cache 정책에 맞춰 쿼리를 소문자로 정규화
                                normalized_query = self.search_string.casefold() if self.search_string else ""
                                file_cache_key = (file_path, normalized_query)

                                # 5개 요소 튜플 저장: (mtime, size, ctime, results, skipped)
                                # Windows: ctime은 생성 시간. Unix: 메타데이터 변경 시간. 둘 다 무결성에 유용함.
                                self.cache.file_cache[file_cache_key] = (
                                    stat.st_mtime,
                                    stat.st_size,
                                    stat.st_ctime,
                                    [result],
                                    [],  # Skipped list for this file (empty for now as we don't track per-file skipped in this loop effectively yet or it is already filtered)
                                )
                                file_cache_count += 1
                        except Exception as e:
                            logger.debug(AppStrings.LOG_CACHE_FILE_FAIL.format(e))

                    logger.debug(AppStrings.LOG_CACHE_FILE_ITEMS.format(file_cache_count))

                    if self.cache.persist:
                        logger.debug(AppStrings.LOG_CACHE_STORAGE_DIR.format(self.config_manager.get_cache_dir()))
                        self.cache.save_to_disk()
                        logger.debug(AppStrings.LOG_CACHE_STORAGE_DONE_MSG)

                    logger.info(AppStrings.LOG_CACHE_SYNC_REPORT.format(len(self.all_results)))
                except RuntimeError:
                    logger.debug(AppStrings.LOG_CACHE_RUNTIME_ERROR)
                except Exception as e:
                    logger.error(AppStrings.LOG_CACHE_SYNC_FAIL.format(e), exc_info=True)
            elif self.cache_enabled and self.cache:
                try:
                    if not cache_key:
                        logger.warning(AppStrings.LOG_CACHE_NO_KEY)
                    elif not self.all_results:
                        logger.debug(AppStrings.LOG_CACHE_NO_RESULTS)
                except RuntimeError:
                    logger.debug(AppStrings.LOG_CACHE_RUNTIME_ERROR_SIMPLE)

        except Exception as e:
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

    def _run_rust_search(self):
        from core.search_engine import search_directory_fast

        logger.info(AppStrings.LOG_WKR_RUST_ACT.format(len(self.search_paths)))
        self.signals.progress_updated.emit(0, 100)
        total_found = 0

        for path in self.search_paths:
            if not self.is_running:
                break

            search_res = search_directory_fast([path], self.search_string, self.extensions)
            results_list: List[Any] = search_res.get("results", []) or []

            if hasattr(self, "all_results"):
                self.all_results.extend(results_list)

            skipped_list = search_res.get("skipped", []) or []
            if hasattr(self, "all_skipped"):
                self.all_skipped.extend(skipped_list)

            CHUNK_SIZE = 500
            for i in range(0, len(results_list), CHUNK_SIZE):
                if not self.is_running:
                    break
                chunk = results_list[i : i + CHUNK_SIZE]
                self.signals.results_found.emit(chunk)
                total_found += len(chunk)

                import time

                time.sleep(Constants.YIELD_SLEEP_TIME)

        excel_selected: List[str] = [
            e for e in self.extensions if (e if e.startswith(".") else "." + e).lower() in EXCEL_EXTS
        ]
        total_matches = 0
        skipped_count = 0

        if excel_selected and self.is_running:
            logger.info(AppStrings.LOG_WKR_EXCEL_SCAN)
            scanner = FileScanner(
                self.search_paths,
                excel_selected,
                filename_filter=self.filename_filter,
                stop_check_callback=lambda: not self.is_running,
            )
            excel_files = scanner.scan()

            if excel_files and self.is_running:
                logger.info(AppStrings.LOG_WKR_RUNNING.format(len(excel_files)))
                # [v4.31.8 Fix] Excel fallback 결과에서 found_count와 skipped_count를 모두 받아 합산합니다.
                f_cnt, t_cnt, s_cnt = self._run_batch_search(excel_files, is_excel_fallback=True)
                total_found += f_cnt
                total_matches += t_cnt
                skipped_count += s_cnt

        if not self.is_running:
            logger.info(AppStrings.LOG_WKR_STOPPED)

        self.signals.progress_updated.emit(100, 100)
        logger.info(AppStrings.LOG_WKR_DONE.format(total_found, total_matches))
        # [v4.31.8 Fix] 실제 집계된 total_found와 skipped_count를 시그널로 전달합니다.
        self.signals.search_finished.emit(total_found, skipped_count)

    def _run_python_search(self):
        if not self.file_list:
            self.signals.search_finished.emit(0, 0)
            return

        logger.info(AppStrings.LOG_WKR_RUNNING.format(len(self.file_list)))
        found_count = 0
        total_matches = 0

        found_count, total_matches, skipped_count = self._run_batch_search(self.file_list, is_excel_fallback=False)

        if not self.is_running:
            logger.info(AppStrings.LOG_WKR_STOPPED)

        logger.info(AppStrings.LOG_WKR_DONE.format(found_count, total_matches))
        self.signals.search_finished.emit(found_count, skipped_count)

    def _run_batch_search(self, files, is_excel_fallback=False):
        total = len(files)
        if total == 0:
            return 0 if is_excel_fallback else (0, 0, 0)

        batch_size = Constants.BATCH_SIZE_LARGE if total > 10000 else Constants.BATCH_SIZE_NORMAL
        batches = [files[i : i + batch_size] for i in range(0, total, batch_size)]

        # [v4.29.3 Fix] 매번 Executor를 생성하지 않고 전역 풀을 재사용합니다.
        self._executor = GlobalExecutor.get_executor()
        executor = self._executor

        future_to_batch = {}
        found_count = 0
        total_matches = 0
        skipped_count = 0
        completed = 0
        last_logged_percent = -1

        try:
            future_to_batch = {
                executor.submit(search_in_files_batch, b, self.search_string, self.special_mode, self.stop_event): b
                for b in batches
            }
            pending_futures = set(future_to_batch.keys())

            while pending_futures:
                if not self.is_running:
                    logger.info(AppStrings.LOG_EXECUTOR_STOPPING)
                    # [v4.31.0 Fix] 전역 풀 상태 일관성을 위해 GlobalExecutor 클래스 메서드 사용
                    GlobalExecutor.shutdown(wait=False, cancel_futures=True)
                    self._executor = None
                    break

                # [v4.31.4 Fix] 워커 Hang 타임아웃 보호 로직 (누적 시간 기반)
                # 매 루프마다 start_time을 초기화하지 않고, '마지막 진행 시간'을 기준으로 측정합니다.
                if not hasattr(self, "_last_progress_time") or self._last_progress_time is None:
                    self._last_progress_time = time.time()

                done, not_done = wait(pending_futures, timeout=1.0, return_when=FIRST_COMPLETED)

                if not done:
                    elapsed = time.time() - self._last_progress_time
                    # Constants.TIMEOUT_WORKER_HANG (기본 600초) 초과 시 강제 조치
                    if elapsed > Constants.TIMEOUT_WORKER_HANG:
                        logger.critical(AppStrings.LOG_WKR_HANG_DETECTED.format(elapsed))
                        # 실행기 강제 재생성 및 현재 작업 중단
                        GlobalExecutor.shutdown(wait=False, cancel_futures=True)
                        self.signals.error.emit(AppStrings.ERROR_WKR_HANG_RECOVERY)
                        break
                    continue

                # 하나 이상의 배치가 완료된 경우 마지막 진행 시간을 갱신합니다.
                self._last_progress_time = time.time()

                for future in done:
                    pending_futures.remove(future)
                    try:
                        batch_res = future.result()
                        if batch_res:
                            if batch_res.get("results"):
                                res_list = batch_res["results"]
                                found_count += len(res_list)
                                self.signals.results_found.emit(res_list)
                                total_matches += sum(cnt for _, cnt, _ in res_list)

                                if hasattr(self, "all_results"):
                                    self.all_results.extend(res_list)

                            if batch_res.get("skipped"):
                                skip_list = batch_res["skipped"]
                                self.signals.skipped_found.emit(skip_list)
                                skipped_count += len(skip_list)

                                if hasattr(self, "all_skipped"):
                                    self.all_skipped.extend(skip_list)

                    except Exception as e:
                        # [v4.32.2 Fix] Future 예외를 무시하지 않고 사용자에게 알림 (안정성 강화)
                        # 배치 전체가 실패한 경우이므로, 해당 배치 묶음을 'Skipped(Error)'로 처리
                        logger.error(AppStrings.LOG_WKR_BATCH_RETRY.format(e))

                        # 예외 발생 시점에는 어떤 파일들이 배치에 있었는지 정확히 알기 어려우나,
                        # future_to_batch 맵을 이용해 실패한 배치를 추적할 수 있음.
                        failed_batch = future_to_batch.get(future, [])
                        if failed_batch:
                            # 배치 내 첫 번째 파일을 대표로 에러 메시지 전송 (UI 스팸 방지)
                            first_file = failed_batch[0][0]  # (path, size) tuple
                            error_msg = f"Batch Error: {str(e)}"
                            self.signals.skipped_found.emit([(first_file, error_msg)])
                            skipped_count += 1

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
            # [v4.29.3 Fix] 전역 풀 재사용을 위해 여기서 shutdown하지 않습니다.
            # 워커 참조만 해제합니다.
            self._executor = None

        # [v4.31.8 Fix] 모든 모드에서 found_count, total_matches, skipped_count를 일관되게 반환합니다.
        return found_count, total_matches, skipped_count

    def stop(self):
        self.is_running = False
        if self.stop_event is not None:
            self.stop_event.set()
        logger.info(AppStrings.LOG_WKR_STOP_SIGNAL)

        if hasattr(self, "_executor") and self._executor:
            try:
                logger.info(AppStrings.LOG_EXECUTOR_FORCE_STOP)
                # [v4.31.0 Fix] 직접 shutdown 대신 전역 관리자를 통해 포인터까지 정리
                GlobalExecutor.shutdown(wait=False, cancel_futures=True)
                self._executor = None
            except Exception as e:
                logger.debug(AppStrings.LOG_EXECUTOR_SHUTDOWN_ERROR.format(e))
            finally:
                self._executor = None


class ScanWorker(QRunnable):
    def __init__(
        self,
        selected_folders,
        selected_exts,
        filename_filter,
        search_string=None,
        disable_smart_scan=False,
        special_mode=None,
    ):
        super().__init__()
        self.signals = WorkerSignals()
        self.setAutoDelete(True)

        self.selected_folders: list = selected_folders
        self.selected_exts: list = selected_exts
        self.filename_filter = filename_filter
        self.search_string: str = search_string
        self.disable_smart_scan: bool = disable_smart_scan
        self.special_mode = special_mode
        self.is_running: bool = True

    @Slot()
    def run(self):
        self.signals.scan_started.emit()
        try:
            from core.search_engine import HAS_RUST_ENGINE, find_files_with_keyword_fast

            use_smart_scan = (
                HAS_RUST_ENGINE and self.search_string and self.selected_folders and not self.disable_smart_scan
            )

            if use_smart_scan:
                logger.info(AppStrings.LOG_SCH_SMART_SCAN_STARTED.format(self.search_string))
                found_files = []

                for folder in self.selected_folders:
                    if not self.is_running:
                        break
                    folder_files = find_files_with_keyword_fast(
                        [folder], self.search_string, self.selected_exts, special_mode=self.special_mode
                    )
                    found_files.extend(folder_files)

                if self.filename_filter and self.is_running:
                    import fnmatch

                    filtered_files = []
                    filters = [self.filename_filter] if isinstance(self.filename_filter, str) else self.filename_filter

                    for f_info in found_files:
                        path_str = f_info[0]
                        filename = os.path.basename(path_str)
                        is_matched = False
                        for f_pattern in filters:
                            pattern = f"*{f_pattern}*" if "*" not in f_pattern else f_pattern
                            if fnmatch.fnmatch(filename.lower(), pattern.lower()):
                                is_matched = True
                                break
                        if is_matched:
                            filtered_files.append(f_info)
                    found_files = filtered_files

                if self.is_running:
                    self.signals.scan_finished.emit(found_files)
                return

            scanner = FileScanner(
                self.selected_folders,
                self.selected_exts,
                self.filename_filter,
                stop_check_callback=lambda: not self.is_running,
            )
            file_list = scanner.scan()

            if self.is_running:
                self.signals.scan_finished.emit(file_list)

        except (IOError, OSError) as e:
            logger.error(AppStrings.ERROR_IO_GENERIC.format(e))
            self.signals.error.emit(str(e))
        except Exception as e:
            logger.error(AppStrings.LOG_WKR_BATCH_ERROR.format(e), exc_info=True)
            self.signals.error.emit(str(e))
        finally:
            self.signals.finished.emit()

    def stop(self):
        self.is_running = False
        logger.info(AppStrings.LOG_WKR_STOP_SIGNAL)
