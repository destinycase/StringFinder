from PySide6.QtCore import QObject, Signal, QRunnable, Slot
from utils.logger import logger
from utils.app_strings import AppStrings
from utils.constants import Constants
import os
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from core.search_engine import search_in_files_batch, FileScanner, EXCEL_EXTS

# 캐시 임포트
from utils.config_manager import ConfigManager

# 배치 검색 타임아웃 (초) - 대용량 파일 검색 시 조절 가능
# 기존 Worker 코드의 복잡성을 제거하기 위해 QRunnable 아키텍처로 리팩토링되었습니다.


class GlobalExecutor:
    """
    애플리케이션 전역에서 사용되는 ProcessPoolExecutor 관리 클래스입니다.
    싱글톤 패턴을 사용하여 프로세스 풀의 생성과 종료를 일원화합니다.
    """

    _instance = None
    _executor = None

    @classmethod
    def get_executor(cls):
        """현재 활성화된 executor를 반환하거나 새로 생성합니다."""
        if cls._executor is None:
            max_workers = multiprocessing.cpu_count()
            # 안전하게 코어 수의 합리적인 수준으로 제한
            max_workers = max(1, min(max_workers, 8))
            cls._executor = ProcessPoolExecutor(max_workers=max_workers)
        return cls._executor

    @classmethod
    def shutdown(cls, wait=False, cancel_futures=True):
        """executor를 강제 종료합니다."""
        if cls._executor:
            try:
                cls._executor.shutdown(wait=wait, cancel_futures=cancel_futures)
            except Exception as e:
                logger.error(f"Executor shutdown error: {e}")
            finally:
                cls._executor = None


class WorkerSignals(QObject):
    """
    QRunnable은 시그널을 직접 가질 수 없으므로,
    시그널 통신을 위한 별도의 QObject 클래스를 정의합니다.
    """

    # 진행 상황 알림 시그널 (현재 처리된 파일 수, 전체 대상 파일 수)
    progress_updated = Signal(int, int)
    # 검색 결과 발견 시그널 (배치 전송: [(파일 경로, 매칭 수, 상세 매칭 리스트), ...])
    results_found = Signal(list)
    # 스킵된 파일 발견 시그널 (배치 전송: [(파일 경로, 스킵 사유), ...])
    skipped_found = Signal(list)
    # 검색/스캔 작업 완료 시그널
    # SearchWorker: (매칭된 총 파일 수, 스킵된 총 파일 수)
    # ScanWorker: (파일 목록 리스트) - 타입 통합을 위해 object로 전달하거나 별도 시그널 사용 가능하지만,
    # 여기서는 각 워커가 필요한 시그널을 골라 씁니다.
    search_finished = Signal(int, int)
    scan_finished = Signal(list)

    # 공통 에러 시그널
    error = Signal(str)
    # 워커 종료 최종 알림 (리소스 정리용)
    finished = Signal()
    # 스캔 시작 알림
    scan_started = Signal()


class SearchWorker(QRunnable):
    """
    실제 파일 검색 작업을 수행하는 백그라운드 워커 클래스입니다.
    QRunnable을 상속받아 QThreadPool에서 실행됩니다.
    """

    def __init__(self, params: dict):
        super().__init__()
        # 시그널 컨테이너
        self.signals = WorkerSignals()
        self.setAutoDelete(True)  # 작업 완료 후 자동 삭제

        self.file_list = params.get("file_list", [])
        self.search_string = params.get("search_string", "")
        self.special_mode = params.get("special_mode")
        self.search_paths = params.get("search_paths")
        self.extensions = params.get("extensions")
        self.filename_filter = params.get("filename_filter")

        # [Optimization] Excel 모드일 경우 확장자 강제 설정
        if self.special_mode and self.special_mode.startswith(Constants.MODE_EXCEL):
            from core.search_engine import EXCEL_EXTS

            self.extensions = [ext.lstrip(".") for ext in EXCEL_EXTS]
            logger.info(AppStrings.LOG_WKR_EXCEL_SCAN)

        # 워커 실행 상태 플래그
        self.is_running = True

        # [Cache] 캐시 설정 저장 (초기화는 run 메서드에서 수행)
        self.config_manager = ConfigManager()
        self.cache_enabled = self.config_manager.get_cache_enabled()
        self.cache = None
        self.cache_config = {
            "dir": self.config_manager.get_cache_dir(),
            "max": self.config_manager.get_cache_max_results(),
            "persist": self.config_manager.get_cache_persist(),
        }

    @Slot()
    def run(self):
        """
        백그라운드 비동기 검색을 수행합니다.
        ProcessPoolExecutor의 수명 관리는 GlobalExecutor에 위임하여 좀비 스레드를 방지합니다.
        """
        from core.search_engine import HAS_RUST_ENGINE

        logger.info(AppStrings.LOG_WKR_STARTED.format(self.search_string))

        # [Cache] 캐시 지연 초기화 (백그라운드 스레드)
        if self.cache_enabled and self.cache is None:
            try:
                from core.search_cache import HybridSearchCache

                self.cache = HybridSearchCache(
                    self.cache_config["dir"], self.cache_config["max"], self.cache_config["persist"]
                )
                logger.debug("[Cache] 검색 캐시 초기화 완료 (백그라운드)")
            except Exception as e:
                logger.warning(f"[Cache] 캐시 초기화 실패: {e}")
                self.cache_enabled = False

        # [Cache] 캐시 키 생성 (캐시 활성화 시)
        cache_key = None
        if self.cache_enabled and self.cache:
            try:
                cache_key = self.cache._get_cache_key(
                    self.search_string, self.search_paths or [], self.extensions or []
                )
                logger.debug(f"[Cache] 캐시 키 생성: {cache_key[:16]}...")
            except Exception as e:
                logger.warning(f"[Cache] 캐시 키 생성 실패: {e}")

        # [Cache] 캐시 확인 (캐시 키가 있을 때만)
        if cache_key and self.cache:
            try:
                cached_results = self.cache.result_cache.get(cache_key)

                if cached_results is not None:
                    logger.debug(f"[Cache] 캐시 발견 - {len(cached_results)}개 결과")

                    # 파일 변경 확인 (file_list가 있을 때만)
                    # file_list는 tuple 형식 (file_path, match_count, content)일 수 있으므로 파일 경로만 추출
                    files_to_check = []
                    if self.file_list:
                        for item in self.file_list:
                            if isinstance(item, tuple) and len(item) >= 1:
                                files_to_check.append(item[0])  # 첫 번째 요소가 file_path
                            elif isinstance(item, str):
                                files_to_check.append(item)

                    # Rust 모드에서는 파일 변경 확인 스킵 (파일 목록이 없음)
                    should_use_cache = True
                    if files_to_check:
                        should_use_cache = not self.cache._any_file_changed(files_to_check, self.search_string)

                    if should_use_cache:
                        logger.info(f"[Cache] 캐시 히트 - {len(cached_results)}개 결과 반환")

                        # 결과 방출
                        CHUNK_SIZE = 500
                        for i in range(0, len(cached_results), CHUNK_SIZE):
                            chunk = cached_results[i : i + CHUNK_SIZE]
                            self.signals.results_found.emit(chunk)

                        self.signals.search_finished.emit(len(cached_results), 0)
                        self.signals.finished.emit()
                        return
                    else:
                        logger.debug("[Cache] 파일 변경 감지 - 재검색 수행")
                else:
                    logger.debug("[Cache] 캐시 미스 - 새로운 검색 수행")
            except Exception as e:
                logger.warning(f"[Cache] 캐시 확인 실패: {e}")

        # 캐시 미스 또는 비활성화 - 일반 검색
        self.all_results = []  # 결과 수집용

        try:
            # [Rust Engine Parallel Mode]
            if HAS_RUST_ENGINE and not self.special_mode and self.search_paths:
                self._run_rust_search()
            else:
                self._run_python_search()

            # [Cache] 검색 완료 후 결과 저장
            if self.cache_enabled and self.cache and cache_key and self.all_results:
                try:
                    logger.debug(f"[Cache] 결과 저장 시작: {len(self.all_results)}개")
                    self.cache.result_cache.put(cache_key, self.all_results)
                    logger.debug("[Cache] LRU 캐시 저장 완료")

                    # 파일별 캐시 업데이트
                    file_cache_count = 0
                    for result in self.all_results:
                        # 결과 형식: (file_path, match_count, content) 튜플
                        try:
                            if isinstance(result, tuple) and len(result) >= 1:
                                file_path = result[0]  # 첫 번째 요소가 file_path
                            elif isinstance(result, dict):
                                file_path = result.get("file_path")
                            else:
                                continue

                            if file_path:
                                import os

                                stat = os.stat(file_path)
                                file_cache_key = (file_path, self.search_string)
                                self.cache.file_cache[file_cache_key] = (
                                    stat.st_mtime,
                                    stat.st_size,
                                    [result],  # 해당 파일의 결과만 저장
                                )
                                file_cache_count += 1
                        except Exception as e:
                            logger.debug(f"[Cache] 파일 캐시 실패: {e}")

                    logger.debug(f"[Cache] 파일별 캐시 {file_cache_count}개 저장 완료")

                    # 디스크 저장
                    if self.cache.persist:
                        logger.debug(f"[Cache] 디스크 저장 시작: {self.config_manager.get_cache_dir()}")
                        self.cache.save_to_disk()
                        logger.debug("[Cache] 디스크 저장 완료")

                    logger.info(f"[Cache] {len(self.all_results)}개 결과 캐싱 완료")
                except RuntimeError:
                    # 애플리케이션 종료 중 시그널 소스가 삭제된 경우 무시
                    logger.debug("[Cache] 애플리케이션 종료 중 - 캐시 저장 스킵")
                except Exception as e:
                    logger.error(f"[Cache] 결과 저장 실패: {e}", exc_info=True)
            elif self.cache_enabled and self.cache:
                try:
                    if not cache_key:
                        logger.warning("[Cache] 캐시 키가 없어 저장 불가")
                    elif not self.all_results:
                        logger.debug("[Cache] 결과가 없어 저장 안 함")
                except RuntimeError:
                    # 애플리케이션 종료 중 시그널 소스가 삭제된 경우 무시
                    logger.debug("[Cache] 애플리케이션 종료 중 - 캐시 확인 스킵")

        except Exception as e:
            logger.critical(AppStrings.LOG_WKR_BATCH_ERROR.format(e), exc_info=True)
            # 시그널 소스가 아직 존재하는 경우에만 emit
            try:
                self.signals.error.emit(AppStrings.ERR_CRITICAL_SYSTEM.format(e))
            except RuntimeError:
                logger.debug("[Worker] 애플리케이션 종료 중 - 에러 시그널 전송 스킵")
        finally:
            # 시그널 소스가 아직 존재하는 경우에만 emit
            try:
                self.signals.finished.emit()
            except RuntimeError:
                logger.debug("[Worker] 애플리케이션 종료 중 - 완료 시그널 전송 스킵")

    def _run_rust_search(self):
        """Rust 엔진을 이용한 고속 검색"""
        from core.search_engine import search_directory_fast

        logger.info(AppStrings.LOG_WKR_RUST_ACT.format(len(self.search_paths)))
        self.signals.progress_updated.emit(0, 100)
        total_found = 0

        # 1. Rust 검색 수행
        for path in self.search_paths:
            if not self.is_running:
                break

            search_res = search_directory_fast([path], self.search_string, self.extensions)
            results_list = search_res.get("results", [])

            # [Cache] 결과 수집
            if hasattr(self, "all_results"):
                self.all_results.extend(results_list)

            # 결과 방출 (대량 결과 대응: 500개씩 청크)
            CHUNK_SIZE = 500
            for i in range(0, len(results_list), CHUNK_SIZE):
                if not self.is_running:
                    break
                chunk = results_list[i : i + CHUNK_SIZE]
                self.signals.results_found.emit(chunk)
                total_found += len(chunk)

                # [GIL Yield]
                import time

                time.sleep(Constants.YIELD_SLEEP_TIME)

        # 2. Excel 보완 검색 (Fallback)
        excel_selected = [e for e in self.extensions if (e if e.startswith(".") else "." + e).lower() in EXCEL_EXTS]

        total_matches = (
            0  # Rust 엔진은 매치 수 합계를 정확히 알기 어려우므로(구현에 따라 다름) 일단 0 또는 Python 검색에서 합산
        )

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
                # 엑셀 검색은 Python 엔진 사용 (배치 처리)
                total_matches += self._run_batch_search(excel_files, is_excel_fallback=True)

        if not self.is_running:
            logger.info(AppStrings.LOG_WKR_STOPPED)

        self.signals.progress_updated.emit(100, 100)
        logger.info(AppStrings.LOG_WKR_DONE.format(total_found, total_matches))
        self.signals.search_finished.emit(total_found, 0)

    def _run_python_search(self):
        """Python ProcessPoolExecutor를 이용한 검색"""
        if not self.file_list:
            self.signals.search_finished.emit(0, 0)
            return

        logger.info(AppStrings.LOG_WKR_RUNNING.format(len(self.file_list)))
        found_count = 0
        total_matches = 0

        # 배치 처리 실행
        found_count, total_matches, skipped_count = self._run_batch_search(self.file_list, is_excel_fallback=False)

        if not self.is_running:
            logger.info(AppStrings.LOG_WKR_STOPPED)

        logger.info(AppStrings.LOG_WKR_DONE.format(found_count, total_matches))
        self.signals.search_finished.emit(found_count, skipped_count)

    def _run_batch_search(self, files, is_excel_fallback=False):
        """
        주어진 파일 리스트에 대해 배치 검색을 수행하고 결과를 반환합니다.
        returns: (found_count, total_matches[, skipped_count])
        """
        total = len(files)
        if total == 0:
            return 0 if is_excel_fallback else (0, 0, 0)

        batch_size = Constants.BATCH_SIZE_LARGE if total > 10000 else Constants.BATCH_SIZE_NORMAL
        batches = [files[i : i + batch_size] for i in range(0, total, batch_size)]

        # Global Executor 사용
        executor = GlobalExecutor.get_executor()

        future_to_batch = {}
        found_count = 0
        total_matches = 0
        skipped_count = 0
        completed = 0
        last_logged_percent = -1

        try:
            future_to_batch = {
                executor.submit(search_in_files_batch, b, self.search_string, self.special_mode): b for b in batches
            }
            pending_futures = set(future_to_batch.keys())

            while pending_futures:
                if not self.is_running:
                    # 사용자 중단 시 Global Executor는 유지하되 현재 퓨처만 취소 시도
                    # (GlobalExecutor.shutdown을 부르면 다른 작업에 영향 줄 수 있으므로 여기서는 cancel만)
                    for f in pending_futures:
                        f.cancel()
                    break

                done, not_done = wait(pending_futures, timeout=0.5, return_when=FIRST_COMPLETED)

                if not done:
                    continue

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

                                # [Cache] 결과 수집
                                if hasattr(self, "all_results"):
                                    self.all_results.extend(res_list)

                            if batch_res.get("skipped"):
                                skip_list = batch_res["skipped"]
                                self.signals.skipped_found.emit(skip_list)
                                skipped_count += len(skip_list)

                    except Exception as e:
                        logger.error(AppStrings.LOG_WKR_BATCH_RETRY.format(e))

                    # 진행률 업데이트
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
            logger.error(AppStrings.ERR_BATCH_SEARCH_FAIL.format(e))
            raise e

        if is_excel_fallback:
            return total_matches
        return found_count, total_matches, skipped_count

    def stop(self):
        """검색 중단 요청"""
        self.is_running = False
        logger.info(AppStrings.LOG_WKR_STOP_SIGNAL)


class ScanWorker(QRunnable):
    """
    파일 목록을 스캔하는 백그라운드 워커 클래스 (QRunnable)
    """

    def __init__(
        self,
        selected_folders,
        selected_exts,
        filename_filter,
        search_string=None,
        disable_smart_scan=False,
    ):
        super().__init__()
        self.signals = WorkerSignals()
        self.setAutoDelete(True)

        self.selected_folders = selected_folders
        self.selected_exts = selected_exts
        self.filename_filter = filename_filter
        self.search_string = search_string
        self.disable_smart_scan = disable_smart_scan
        self.is_running = True

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
                    folder_files = find_files_with_keyword_fast([folder], self.search_string, self.selected_exts)
                    found_files.extend(folder_files)

                # 파일명 필터 2차 적용
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

            # Legacy Scan
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
