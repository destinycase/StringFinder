import json
import os
import copy
import threading
import time
from typing import Dict, Any
from sf_utils.logger import logger
from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants


class ConfigManager:
    """
    애플리케이션의 설정 및 검색 기록(History)을 중앙에서 관리하는 싱글톤(Singleton) 클래스입니다.
    여러 곳에서 동시에 접근 시 데이터 일관성을 보장하며, 파일 기반으로 영구적으로 저장합니다.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if not hasattr(cls, "_instance") or cls._instance is None:
                cls._instance = super(ConfigManager, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        """사용자 로컬 AppData 내에 설정 저장소를 확보하고 기본값들을 준비합니다."""
        if getattr(self, "_initialized", False):
            return

        app_data = os.getenv("APPDATA")
        if not app_data:
            app_data = os.path.join(os.path.expanduser("~"), ".stringfinder")

        self.config_dir = os.path.join(app_data, "StringFinder")
        try:
            os.makedirs(self.config_dir, exist_ok=True)
        except Exception:
            import tempfile

            self.config_dir = os.path.join(tempfile.gettempdir(), "StringFinder")
            os.makedirs(self.config_dir, exist_ok=True)

        self.config_path = os.path.join(self.config_dir, "config.json")

        self.CURRENT_CONFIG_VERSION = 1

        self.defaults: Dict[str, Any] = {
            "config_version": self.CURRENT_CONFIG_VERSION,
            "filters": {"folders": [], "extensions": ["xml", "json", "xlsx", "xlsm"], "filenames": []},
            "history": [],
            "filename_history": [],
            "geometry": None,
            "windowState": None,
            "main_splitter_state": None,
            "result_splitter_state": None,
            "filter_splitter_state": None,
            "theme": "Dark",
            "global_hotkey": "alt+shift+space",
            "run_at_startup": False,
            "close_to_tray": False,
            "case_insensitive": False,
            "result_column_widths": [60, 400, 100, 60],
            "match_column_widths": [60, 400, 400],
            "log_retention": {
                "enabled": True,
                "max_files": 10,
                "max_days": 3,
            },
            "search_cache": {
                "enabled": False,
                "max_results": 100,
                "persist": True,
            },
            "dock_layout_state": None,
            "lock_dock_layout": False,
            "tabs": [],
        }
        self.config: Dict[str, Any] = self._load()
        self.sessions_dir: str = os.path.join(self.config_dir, "sessions")
        os.makedirs(self.sessions_dir, exist_ok=True)
        self._save_lock = threading.Lock()
        self._save_timer = None
        self._save_debounce_time = 0.5
        # config_manager.config는 위에서 이미 로드되었지만, 명확성을 위해 필요시 재할당
        if not hasattr(self, "config"):
            self.config = self._load()
        self._initialized = True

    def get_column_widths(self, table_name):
        """테이블 이름별로 저장된 컬럼 너비 리스트를 반환합니다."""
        if table_name == Constants.VIEW_RESULT:
            return self.get("result_column_widths", [60, 400, 100, 60])
        elif table_name == Constants.VIEW_MATCH:
            return self.get("match_column_widths", [60, 400, 400])
        return None

    def save_column_widths(self, table_name, widths):
        """테이블 이름별로 컬럼 너비 리스트를 설정 파일에 저장합니다."""
        if table_name == Constants.VIEW_RESULT:
            self.set("result_column_widths", widths)
        elif table_name == Constants.VIEW_MATCH:
            self.set("match_column_widths", widths)

    def get_case_insensitive(self):
        """대소문자 구분 여부 옵션의 현재 상태를 반환합니다."""
        return self.config.get("case_insensitive", False)

    def set_case_insensitive(self, value):
        """대소문자 구분 여부 옵션을 변경하고 파일로 즉시 저장합니다."""
        self.config["case_insensitive"] = value
        self.save()

    def _load(self):
        """로컬 JSON 파일을 읽어 설정을 복원하며, 파일이 없거나 손상된 경우 기본값을 사용합니다."""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                    if not isinstance(data, dict):
                        logger.warning(AppStrings.LOG_CFG_INVALID_VER)
                        return copy.deepcopy(self.defaults)

                    loaded_version = data.get("config_version", 0)

                    if loaded_version < self.CURRENT_CONFIG_VERSION:
                        logger.info(AppStrings.LOG_CFG_MIGRATION.format(loaded_version, self.CURRENT_CONFIG_VERSION))
                        data = self._migrate_config(data, loaded_version)
                    elif loaded_version > self.CURRENT_CONFIG_VERSION:
                        logger.warning(
                            AppStrings.LOG_CFG_MIGRATE_WARN.format(loaded_version, self.CURRENT_CONFIG_VERSION)
                        )

                    merged = copy.deepcopy(self.defaults)
                    for k, v in data.items():
                        if k in merged and v is not None:
                            if isinstance(merged[k], list) and not isinstance(v, list):
                                continue
                            if isinstance(merged[k], dict) and not isinstance(v, dict):
                                continue
                            merged[k] = v
                    return merged
            except json.JSONDecodeError as e:
                logger.warning(AppStrings.LOG_CFG_LOAD_FAIL.format(e))
            except (IOError, OSError) as e:
                logger.error(AppStrings.LOG_CFG_READ_ERROR.format(e))
        return copy.deepcopy(self.defaults)

    def _migrate_config(self, config: dict, from_version: int) -> dict:
        """
        설정 파일을 이전 버전에서 현재 버전으로 마이그레이션합니다.

        Args:
            config: 로드된 설정 딕셔너리
            from_version: 설정 파일의 버전 (0 = 레거시)

        Returns:
            마이그레이션된 설정 딕셔너리
        """
        if from_version < 1:
            config["config_version"] = 1

        return config

    def save(self):
        """현재 메모리 설정값들을 로컬 JSON 파일로 예약 저장합니다 (Debounce).
        짧은 시간 내에 여러 번 호출되어도 지연 시간 뒤에 한 번만 저장됩니다.
        """
        self.cancel_start()

        self._save_timer = threading.Timer(self._save_debounce_time, self.save_immediately)
        self._save_timer.name = "ConfigSaveTimer"
        self._save_timer.start()

    def cancel_start(self):
        """실행 대기 중인 저장 타이머를 안전하게 취소합니다."""
        with self._save_lock:
            if self._save_timer is not None:
                self._save_timer.cancel()
                self._save_timer = None

    def stop(self):
        """매니저 종료 시 호출. 대기 중인 저장을 즉시 수행하거나 취소하고 리소스를 정리합니다."""
        self.cancel_start()
        self.save_immediately()

    def save_immediately(self):
        """지연 없이 즉시 설정을 파일로 기록하여 영구 저장합니다.
        Windows 환경에서는 파일 잠금(Lock) 및 액세스 거부 오류를 방지하기 위해 정교한 원자적 쓰기 로직을 사용합니다.
        """
        temp_path = self.config_path + ".tmp"
        old_path = self.config_path + ".old"

        with self._save_lock:
            if self._save_timer is not None:
                self._save_timer.cancel()
                self._save_timer = None

            for attempt in range(5):
                try:
                    config_dir = os.path.dirname(self.config_path)
                    if not os.path.exists(config_dir):
                        os.makedirs(config_dir, exist_ok=True)

                    with open(temp_path, "w", encoding="utf-8") as f:
                        json.dump(self.config, f, indent=4, ensure_ascii=False)

                    if not os.path.exists(self.config_path):
                        os.rename(temp_path, self.config_path)
                    else:
                        try:
                            os.replace(temp_path, self.config_path)
                        except (PermissionError, OSError):
                            if os.path.exists(old_path):
                                os.remove(old_path)

                            os.rename(self.config_path, old_path)
                            os.rename(temp_path, self.config_path)
                            try:
                                os.remove(old_path)
                            except OSError:
                                pass

                    return

                except (IOError, OSError) as e:
                    if attempt < 4:
                        logger.debug(AppStrings.LOG_CFG_SAVE_RETRY.format(attempt + 1, e))
                        time.sleep(0.1 * (attempt + 1))
                    else:
                        logger.error(AppStrings.LOG_CFG_SAVE_FAIL.format(e))
                except Exception as e:
                    logger.error(AppStrings.LOG_CFG_SAVE_FAIL.format(e), exc_info=True)
                    break
                finally:
                    if os.path.exists(temp_path):
                        try:
                            os.remove(temp_path)
                        except OSError:
                            pass

    def get(self, key, default=None):
        """설정값을 가져옵니다. 키가 없으면 기본값을 반환합니다."""
        return self.config.get(key, default)

    def set(self, key, value):
        """설정값을 변경하고 파일로 즉시 저장합니다."""
        self.config[key] = value
        self.save()

    def add_history(self, search_text):
        """새로운 검색어를 기록 상단에 추가하며, 중복된 경우 맨 앞으로 이동시킵니다. 최대 유지 개수는 20개입니다."""
        if not search_text:
            return

        history = self.config.get("history")
        if not isinstance(history, list):
            history = []
            self.config["history"] = history

        if search_text in history:
            history.remove(search_text)

        history.insert(0, search_text)
        self.config["history"] = history[:20]
        self.save()

    def remove_history_item(self, text):
        history = self.config.get("history", [])
        if isinstance(history, list) and text in history:
            history.remove(text)
            self.save()

    def clear_history(self):
        self.config["history"] = []
        self.save()

    def get_history(self):
        history = self.config.get("history", [])
        return history if isinstance(history, list) else []

    def add_filename_history(self, filename):
        """파일명 필터 기록을 상단에 추가하며, 중복된 경우 맨 앞으로 이동시킵니다."""
        if not filename:
            return

        history = self.config.get("filename_history")
        if not isinstance(history, list):
            history = []
            self.config["filename_history"] = history

        if filename in history:
            history.remove(filename)

        history.insert(0, filename)
        self.config["filename_history"] = history[:20]
        self.save()

    def remove_filename_history_item(self, text):
        history = self.config.get("filename_history", [])
        if isinstance(history, list) and text in history:
            history.remove(text)
            self.save()

    def clear_filename_history(self):
        self.config["filename_history"] = []
        self.save()

    def get_filename_history(self):
        history = self.config.get("filename_history", [])
        return history if isinstance(history, list) else []

    def update_filters(self, folders, extensions, filenames=None):
        if not isinstance(self.config.get("filters"), dict):
            self.config["filters"] = self.defaults["filters"]

        self.config["filters"]["folders"] = folders
        self.config["filters"]["extensions"] = extensions
        if filenames is not None:
            self.config["filters"]["filenames"] = filenames
        self.save()

    def get_filters(self):
        return self.config["filters"]

    def set_window_state(self, geometry, state):
        """메인 윈도우의 위치와 크기 정보를 직렬화하여 저장합니다."""
        self.config["geometry"] = geometry.toHex().data().decode()
        self.config["windowState"] = state.toHex().data().decode()
        self.save()

    def get_window_state(self):
        return self.config.get("geometry"), self.config.get("windowState")

    def set_splitter_states(self, main_state, result_state, filter_state=None):
        if main_state:
            self.config["main_splitter_state"] = main_state.toHex().data().decode()
        if result_state:
            self.config["result_splitter_state"] = result_state.toHex().data().decode()
        if filter_state:
            self.config["filter_splitter_state"] = filter_state.toHex().data().decode()
        self.save()

    def set_dock_state(self, state):
        """도킹 레이아웃 상태를 저장합니다."""
        if state:
            self.config["dock_layout_state"] = state.toHex().data().decode()
            self.save()

    def get_dock_state(self):
        """저장된 도킹 레이아웃 상태를 반환합니다."""
        return self.config.get("dock_layout_state")

    def get_lock_dock_layout(self):
        """레이아웃 고정 여부를 반환합니다."""
        return self.config.get("lock_dock_layout", False)

    def set_lock_dock_layout(self, lock):
        """레이아웃 고정 여부를 설정합니다."""
        self.config["lock_dock_layout"] = lock
        self.save()

    def get_tab_order(self):
        """저장된 탭 순서를 반환합니다."""
        return self.config.get("tabs", [])

    def set_tab_order(self, tabs):
        """탭 영역 순서를 저장합니다."""
        self.config["tabs"] = tabs
        self.save()

    def _sanitize_session_name(self, name: str) -> str:
        """세션 파일명으로 사용할 수 없는 문자를 제거합니다.

        Args:
            name: 원본 세션 이름

        Returns:
            정규화된 세션 이름
        """
        from sf_utils.file_helper import sanitize_filename

        base_name = os.path.basename(name)
        sanitized = sanitize_filename(base_name)
        return sanitized if sanitized else "default"

    def save_session(self, name, data):
        """개별 탭의 세션 데이터를 파일로 저장합니다."""
        safe_name = self._sanitize_session_name(name)

        file_path = os.path.join(self.sessions_dir, f"{safe_name}.json")
        try:
            if not os.path.exists(self.sessions_dir):
                os.makedirs(self.sessions_dir, exist_ok=True)

            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            return True
        except Exception as e:
            logger.error(AppStrings.LOG_SES_SAVE_FAIL.format(name, e))
            return False

    def load_session(self, name):
        """저장된 탭 세션 데이터를 불러옵니다."""
        safe_name = self._sanitize_session_name(name)
        file_path = os.path.join(self.sessions_dir, f"{safe_name}.json")
        if not os.path.exists(file_path):
            return None
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(AppStrings.LOG_SES_LOAD_FAIL.format(name, e))
            return None

    def delete_session(self, name):
        """탭 세션 파일을 삭제합니다."""
        safe_name = self._sanitize_session_name(name)
        file_path = os.path.join(self.sessions_dir, f"{safe_name}.json")
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception as e:
                logger.error(AppStrings.LOG_SES_DELETE_FAIL.format(name, e))

    def get_all_session_names(self):
        """저장된 모든 세션 파일의 이름(확장자 제외)을 리스트로 반환합니다."""
        if not os.path.exists(self.sessions_dir):
            return []

        sessions = []
        try:
            for filename in os.listdir(self.sessions_dir):
                if filename.lower().endswith(".json"):
                    sessions.append(os.path.splitext(filename)[0])
        except Exception as e:
            logger.error(AppStrings.LOG_SES_LIST_FAIL.format(e))

        return sessions

    def get_splitter_states(self):
        return (
            self.config.get("main_splitter_state"),
            self.config.get("result_splitter_state"),
            self.config.get("filter_splitter_state"),
        )

    def get_theme(self):
        return self.config.get("theme", "Dark")

    def set_theme(self, theme):
        self.config["theme"] = theme
        self.save()

    def clear_all_logs(self):
        """저장된 모든 로그 파일을 삭제합니다. (현재 사용 중인 파일 제외)"""
        if not os.path.exists(self.config_dir):
            return

        import glob

        log_pattern = os.path.join(self.config_dir, "*.log")
        files = glob.glob(log_pattern)
        deleted_count = 0

        for f in files:
            try:
                os.remove(f)
                deleted_count += 1
            except (PermissionError, OSError):
                pass

        logger.info(AppStrings.LOG_CFG_LOG_CLEANUP_DONE_MSG.format(deleted_count))

    def clear_all_data(self):
        """모든 데이터(설정, 로그, 캐시, 히스토리 등)를 초기화합니다."""
        self.clear_all_logs()

        try:
            import shutil

            cache_dir = self.get_cache_dir()
            if os.path.exists(cache_dir):
                shutil.rmtree(cache_dir, ignore_errors=True)
                logger.info("[설정] 캐시 폴더 삭제 완료")
        except Exception as e:
            logger.error(f"[설정] 캐시 폴더 삭제 실패: {e}")

        try:
            import shutil

            if os.path.exists(self.sessions_dir):
                shutil.rmtree(self.sessions_dir, ignore_errors=True)
                os.makedirs(self.sessions_dir, exist_ok=True)
        except Exception as e:
            logger.error(f"[설정] 세션 폴더 삭제 실패: {e}")

        if os.path.exists(self.config_path):
            try:
                os.remove(self.config_path)
            except OSError as e:
                logger.error(AppStrings.LOG_CFG_DELETE_FAIL_MSG.format(e))

        self.config = copy.deepcopy(self.defaults)
        self.save()
        logger.info("[설정] 모든 데이터 초기화 완료")

    def get_global_hotkey(self):
        return self.config.get("global_hotkey", "alt+shift+space")

    def set_global_hotkey(self, hotkey):
        self.config["global_hotkey"] = hotkey
        self.save()

    def get_run_at_startup(self):
        return self.config.get("run_at_startup", False)

    def set_run_at_startup(self, run):
        self.config["run_at_startup"] = run
        self.save()

    def get_close_to_tray(self):
        """닫기 버튼 클릭 시 트레이로 가기 여부 반환 (기본값 False: 프로그램 종료)"""
        return self.config.get("close_to_tray", False)

    def set_close_to_tray(self, value):
        self.config["close_to_tray"] = value
        self.save()

    def get_cache_enabled(self) -> bool:
        """검색 캐시 활성화 여부 반환"""
        return bool(self.config.get("search_cache", {}).get("enabled", True))

    def set_cache_enabled(self, enabled: bool):
        """검색 캐시 활성화 여부 설정"""
        if "search_cache" not in self.config:
            self.config["search_cache"] = self.defaults["search_cache"].copy()
        self.config["search_cache"]["enabled"] = enabled
        self.save()

    def get_cache_max_results(self) -> int:
        """캐시 최대 크기 반환"""
        return int(self.config.get("search_cache", {}).get("max_results", 100))

    def get_cache_persist(self) -> bool:
        """캐시 디스크 저장 여부 반환"""
        return bool(self.config.get("search_cache", {}).get("persist", True))

    def get_cache_dir(self) -> str:
        """캐시 저장 디렉토리 경로 반환"""
        cache_dir = os.path.join(self.config_dir, "cache")
        os.makedirs(cache_dir, exist_ok=True)
        return cache_dir
