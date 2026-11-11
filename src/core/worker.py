import threading
import time
from collections import deque
from typing import Any, List, Optional

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from sf_utils.app_strings import AppStrings
from sf_utils.config_manager import ConfigManager
from sf_utils.constants import Constants
from sf_utils.logger import logger


def _get_adv_setting(key, default, settings_snapshot=None):
    if settings_snapshot is not None:
        return settings_snapshot.get(key, default)
    return ConfigManager().get_advanced_settings().get(key, default)


class WorkerSignals(QObject):
    progress_updated = Signal(int, int)
    results_found = Signal(list)
    skipped_found = Signal(list)
    search_finished = Signal(int, int, int)
    total_match_limit_reached = Signal(int)
    error = Signal(str)
    finished = Signal()


class SearchWorker(QRunnable):
    def __init__(self, params: dict):
        super().__init__()
        self.signals = WorkerSignals()
        self.setAutoDelete(True)
        self.file_list: List[Any] = params.get(Constants.PAYLOAD_FILE_LIST, [])
        self.search_string = params.get(Constants.PAYLOAD_SEARCH_STRING, "")
        self.special_mode = params.get(Constants.PAYLOAD_SPECIAL_MODE)
        self.search_paths = params.get(Constants.PAYLOAD_SEARCH_PATHS, [])
        self.extensions: List[str] = params.get(Constants.PAYLOAD_EXTENSIONS, [])
        self.filename_filter = params.get(Constants.PAYLOAD_FILENAME_FILTER)
        self.exclude_hidden = params.get(Constants.PAYLOAD_EXCLUDE_HIDDEN, True)
        self.exclude_binary = params.get(Constants.PAYLOAD_EXCLUDE_BINARY, True)
        self.stop_event = threading.Event()
        if self.special_mode and self.special_mode.startswith(Constants.MODE_EXCEL):
            from core.search_engine import EXCEL_EXTS
            self.extensions = [ext.lstrip(".") for ext in EXCEL_EXTS]
            logger.debug(AppStrings.LOG_WKR_EXCEL_SCAN)
        self.is_running = threading.Event()
        self.is_running.set()
        self.config_manager: ConfigManager = ConfigManager()
        # Freeze search-affecting advanced settings for this search. Changes made
        # in Settings are picked up when the next SearchWorker is created.
        self.search_settings_snapshot = self.config_manager.get_advanced_settings()
        self._total_matches_accumulated = 0
        self._total_limit_alert_emitted = False
        self._last_mem_check: Optional[float] = None
        self._memory_alert_emitted = False
        self.all_skipped: List[Any] = []

    def _safe_emit(self, signal, *args):
        """전송 실패만 기록하며, Qt 수신 함수에서 발생하는 예외까지 포착하지는 않습니다."""
        try:
            signal.emit(*args)
        except Exception as error:
            message = str(error).lower()
            if isinstance(error, RuntimeError) and (
                "signal source has been deleted" in message
                or ("internal c++ object" in message and "already deleted" in message)
            ):
                # 종료 중 Qt 송신 객체가 삭제된 경우에는 알릴 대상이 없으므로 무시합니다.
                return
            try:
                logger.warning(AppStrings.LOG_WKR_SIGNAL_EMIT_FAIL.format(repr(signal), error), exc_info=True)
            except Exception:
                # 종료 중 로그 수신 객체도 삭제됐다면 진단 실패를 워커에 다시 전파하지 않습니다.
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
        """Return True only for a system-wide memory-pressure stop reason."""
        return any(
            str(reason).strip() == AppStrings.ERROR_MEMORY_CRITICAL
            for _, reason in skip_list
        )
    def _check_safety_limits(self, new_matches_count: int) -> bool:
        """Account for accepted matches and enforce the memory guard."""
        self._total_matches_accumulated += new_matches_count
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

    @staticmethod
    def _trim_match_rows(matches, allowed_count: int):
        """Keep at most ``allowed_count`` visible rows plus their notice rows."""
        if allowed_count <= 0:
            return []

        from core.search_engine import _visible_match_count

        if _visible_match_count(matches) <= allowed_count:
            return list(matches)

        retained = []
        visible_remaining = allowed_count
        for match in matches:
            is_notice = (
                isinstance(match, (tuple, list))
                and match
                and str(match[0]) in {"-1", "-2"}
            )
            if is_notice:
                retained.append(match)
            elif visible_remaining > 0:
                retained.append(match)
                visible_remaining -= 1
        return retained

    def _fit_results_to_total_limit(self, result_batch):
        """Return the exact prefix that fits within the configured global limit."""
        maximum = _get_adv_setting(
            Constants.CONFIG_KEY_MAX_TOTAL_MATCHES,
            Constants.DEFAULT_MAX_TOTAL_MATCHES,
            self.search_settings_snapshot,
        )
        remaining = max(0, maximum - self._total_matches_accumulated)
        accepted = []
        accepted_count = 0
        limit_reached = False

        for path, declared_count, matches in result_batch:
            count = max(0, int(declared_count))
            if count <= remaining:
                accepted.append((path, count, matches))
                accepted_count += count
                remaining -= count
                continue

            limit_reached = True
            if remaining <= 0:
                break

            from core.search_engine import _visible_match_count

            visible_count = _visible_match_count(matches)
            if visible_count == 1 and count > 1:
                trimmed_matches = [
                    (1, AppStrings.MSG_BINARY_MATCH.format(remaining), None, None)
                ]
            else:
                trimmed_matches = self._trim_match_rows(matches, remaining)
            accepted.append((path, remaining, trimmed_matches))
            accepted_count += remaining
            remaining = 0
            break

        if accepted_count < sum(max(0, int(item[1])) for item in result_batch):
            limit_reached = True
        elif accepted_count > 0 and remaining == 0:
            limit_reached = True
        return accepted, accepted_count, limit_reached

    def _stop_for_total_match_limit(self):
        maximum = _get_adv_setting(
            Constants.CONFIG_KEY_MAX_TOTAL_MATCHES,
            Constants.DEFAULT_MAX_TOTAL_MATCHES,
            self.search_settings_snapshot,
        )
        err_msg = AppStrings.ERROR_LIMIT_REACHED.format(maximum)
        logger.warning(err_msg)
        self.stop_event.set()
        self.is_running.clear()
        if not self._total_limit_alert_emitted:
            self._total_limit_alert_emitted = True
            self._safe_emit(self.signals.total_match_limit_reached, maximum)
        self._safe_emit(self.signals.error, err_msg)

    @Slot()
    def run(self):
        from core.search_query import validate_search_query
        try:
            validate_search_query(self.search_string)
        except ValueError as error:
            self.is_running.clear()
            self._safe_emit(self.signals.error, str(error))
            self._safe_emit(self.signals.finished)
            return
        ext_info = ",".join(self.extensions) if self.extensions else "*"
        mode_info = self.special_mode if self.special_mode else Constants.MODE_NORMAL
        logger.info(AppStrings.LOG_WKR_STARTED.format(self.search_string, ext_info, mode_info))
        if not hasattr(self, "worker_start_time"):
            self.worker_start_time = time.time()
        # Legacy diagnostics/tests access all_results. Keep only a bounded
        # recent window; the complete result stream is delivered by signals.
        self.all_results: deque[Any] = deque(maxlen=Constants.WORKER_RESULT_RETENTION)
        self.all_skipped = []
        self.skipped_sheets_list: List[Any] = []  # [(file_path, sheet_name), ...] 시트 스킵 목록
        try:
            if self.search_paths or self.file_list:
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
                # 동일 카운트는 3초마다 하트비트로 갱신
                if now - self._last_progress_emit_time < 3.0:
                    return
            else:
                if now - self._last_progress_emit_time < 0.1:
                    return

            actual_total = max(total_files, count, 1)

            self._safe_emit(self.signals.progress_updated, count, actual_total)
            self._last_progress_count = count
            self._last_progress_emit_time = now

        def process_results_batch(batch):
            # 검색 중지 직후에도 Rust 디스패처가 이미 발견한 마지막 배치를 전달할 수 있습니다.
            # 중지 플래그만으로 이 배치를 버리면 사용자에게 부분 검색 결과가 유실됩니다.
            # logger.debug(f"[Worker] results_callback received batch of {len(batch)}")
            from core.search_engine import (
                _extract_marker_skip_reason,
                _extract_partial_skip_reason,
                _normalize_rust_matches,
                _visible_match_count,
            )
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
                partial_reason = _extract_partial_skip_reason(matches)
                if partial_reason:
                    skipped_batch.append((path, partial_reason))
                    if hasattr(self, "all_skipped"):
                        self.all_skipped.append((path, partial_reason))
                match_tuples, marker_binary_count, sheet_skips = _normalize_rust_matches(matches, self.special_mode)
                # 시트 스킵 정보를 별도 목록에 수집 (파일 스킵과 구분)
                if sheet_skips and hasattr(self, "skipped_sheets_list"):
                    for sheet_name, detail in sheet_skips:
                        self.skipped_sheets_list.append((path, sheet_name, detail))
                if marker_binary_count > 0:
                    formatted_batch.append((path, marker_binary_count, [(1, AppStrings.MSG_BINARY_MATCH.format(marker_binary_count), None, None)]))
                    current_batch_matches += marker_binary_count
                elif match_tuples:
                    visible_count = _visible_match_count(match_tuples)
                    formatted_batch.append((path, visible_count, match_tuples))
                    current_batch_matches += visible_count
            
            if not formatted_batch and not skipped_batch:
                return
            if skipped_batch and self._is_memory_skip(skipped_batch):
                self._stop_for_memory_pressure()
            limit_reached = False
            if formatted_batch:
                formatted_batch, current_batch_matches, limit_reached = self._fit_results_to_total_limit(
                    formatted_batch
                )
            if formatted_batch and not self._check_safety_limits(current_batch_matches):
                return
            if formatted_batch:
                self._rust_found_files += len(formatted_batch)
                self._rust_total_matches += current_batch_matches
                self.all_results.extend(formatted_batch)
                self._safe_emit(self.signals.results_found, formatted_batch)
            if limit_reached:
                self._stop_for_total_match_limit()
            if skipped_batch:
                self._safe_emit(self.signals.skipped_found, skipped_batch)

        def results_callback(batch):
            from core.search_engine import use_search_settings_snapshot

            with use_search_settings_snapshot(self.search_settings_snapshot):
                process_results_batch(batch)

        try:
            if hasattr(self, Constants.PAYLOAD_FILE_LIST) and self.file_list:
                paths_only = [f[0] for f in self.file_list]
                from core.search_engine import use_search_settings_snapshot

                with use_search_settings_snapshot(self.search_settings_snapshot):
                    search_res = search_files_list_fast(
                        paths_only,
                        self.search_string,
                        special_mode=self.special_mode,
                        exclude_hidden=self.exclude_hidden,
                        exclude_binary=self.exclude_binary,
                        stop_event=self.stop_event,
                        progress_callback=progress_callback,
                        results_callback=results_callback,
                        )
            else:
                logger.info(AppStrings.LOG_WKR_RUST_ACT.format(len(self.search_paths)))
                from core.search_engine import use_search_settings_snapshot

                with use_search_settings_snapshot(self.search_settings_snapshot):
                    search_res = search_directory_fast(
                        self.search_paths,
                        self.search_string,
                        self.extensions,
                        filename_filter=self.filename_filter,
                        special_mode=self.special_mode,
                        exclude_hidden=self.exclude_hidden,
                        exclude_binary=self.exclude_binary,
                        stop_event=self.stop_event,
                        progress_callback=progress_callback,
                        results_callback=results_callback,
                        )
        except Exception as e:
            error_msg = AppStrings.LOG_SCH_RUST_ENGINE_ERROR.format("SearchWorker.Batch", e)
            logger.error(error_msg, exc_info=True)
            self._safe_emit(self.signals.error, error_msg)
            # Native callbacks may have delivered results before the failure.
            self._safe_emit(
                self.signals.search_finished,
                self._rust_found_files,
                self._rust_total_matches,
                len(self.all_skipped),
            )
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
        self._safe_emit(self.signals.search_finished, total_found, total_matches, skipped_count)
        elapsed = time.time() - self.worker_start_time
        logger.info(AppStrings.LOG_WKR_DONE.format(total_found, total_matches, elapsed))

    def stop(self):
        self.is_running.clear()
        if self.stop_event is not None:
            self.stop_event.set()
