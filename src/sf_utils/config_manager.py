import copy
import hashlib
import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants
from sf_utils.logger import logger


class ConfigManager:
    """애플리케이션 설정을 관리하는 싱글턴 클래스."""

    _instance = None
    _lock = threading.Lock()
    _SESSION_NAME_META_KEY = "__sf_session_name"

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            # _instance는 클래스 속성입니다. hasattr 가드를 유지하여
            # _instance가 삭제될 수 있는 테스트 환경에서의 안정성을 확보합니다.
            if not hasattr(cls, "_instance") or cls._instance is None:
                cls._instance = super(ConfigManager, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        """설정 경로, 기본값, 저장 타이머를 초기화한다."""
        if getattr(self, "_initialized", False):
            return
        # __init__에서 예외 발생 시 _instance를 초기화하여 다음 호출에서
        # 완전하게 초기화된 싱글턴을 새로 생성할 수 있게 합니다.
        try:
            self._init_body()
        except Exception:
            type(self)._instance = None
            raise

    def _init_body(self):
        """H-01 가드를 위해 실제 초기화 로직을 분리했습니다."""
        app_data = os.getenv(Constants.ENV_APPDATA)
        if not app_data:
            app_data = os.path.join(os.path.expanduser("~"), Constants.APPDATA_FALLBACK_DIR)
        self.config_dir = os.path.join(app_data, Constants.APP_NAME)
        try:
            os.makedirs(self.config_dir, exist_ok=True)
        except Exception as e:
            import tempfile

            self.config_dir = os.path.join(tempfile.gettempdir(), Constants.APPDATA_TEMP_DIR)
            os.makedirs(self.config_dir, exist_ok=True)
            # AppData 접근이 실패하면 임시 폴더로 전환한다.
            logger.warning(AppStrings.LOG_CFG_APPDATA_FALLBACK.format(e, self.config_dir))
        self.config_path = os.path.join(self.config_dir, Constants.CONFIG_FILENAME)
        self.CURRENT_CONFIG_VERSION = 1
        self.last_load_error: Optional[Exception] = None
        self.last_save_error: Optional[Exception] = None
        self.defaults: Dict[str, Any] = {
            Constants.CONFIG_KEY_VERSION: self.CURRENT_CONFIG_VERSION,
            Constants.CONFIG_KEY_FILTERS: {
                Constants.CONFIG_KEY_FOLDERS: [],
                Constants.CONFIG_KEY_EXTENSIONS: ["xml", "json", "xlsx", "xlsm"],
                Constants.CONFIG_KEY_FILENAMES: [],
            },
            Constants.CONFIG_KEY_HISTORY: [],
            Constants.CONFIG_KEY_FILENAME_HISTORY: [],
            Constants.CONFIG_KEY_GEOMETRY: None,
            Constants.CONFIG_KEY_WINDOW_STATE: None,
            Constants.CONFIG_KEY_MAIN_SPLITTER_STATE: None,
            Constants.CONFIG_KEY_RESULT_SPLITTER_STATE: None,
            Constants.CONFIG_KEY_FILTER_SPLITTER_STATE: None,
            Constants.CONFIG_KEY_THEME: Constants.DEFAULT_THEME,
            Constants.CONFIG_KEY_GLOBAL_HOTKEY: Constants.DEFAULT_GLOBAL_HOTKEY,
            Constants.CONFIG_KEY_RUN_AT_STARTUP: False,
            Constants.CONFIG_KEY_CLOSE_TO_TRAY: False,
            Constants.CONFIG_KEY_CASE_INSENSITIVE: False,
            Constants.CONFIG_KEY_RESULT_COLUMN_WIDTHS: [60, 400, 100, 60],
            Constants.CONFIG_KEY_MATCH_COLUMN_WIDTHS: [60, 400, 400],
            Constants.CONFIG_KEY_LOG_RETENTION: {
                Constants.CONFIG_KEY_LOG_RETENTION_ENABLED: True,
                Constants.CONFIG_KEY_LOG_RETENTION_MAX_FILES: 10,
                Constants.CONFIG_KEY_LOG_RETENTION_MAX_DAYS: 3,
            },
            Constants.CONFIG_KEY_DOCK_LAYOUT_STATE: None,
            Constants.CONFIG_KEY_LOCK_DOCK_LAYOUT: False,
            Constants.CONFIG_KEY_EXCLUDE_BINARY: True,
            Constants.CONFIG_KEY_TABS: [],
        }
        self._config_lock = threading.RLock()
        self._save_lock = threading.Lock()
        self.last_load_error = None  # _load() 호출 전 에러 상태 초기화
        self.config: Dict[str, Any] = self._load()
        self.sessions_dir: str = os.path.join(self.config_dir, Constants.SESSIONS_DIRNAME)
        os.makedirs(self.sessions_dir, exist_ok=True)
        # 반드시 _config_lock 전에 _save_lock을 획득해야 함.
        # 이 순서를 어기면 데드락 위험이 있음 if both are held concurrently.
        self._save_timer = None
        self._save_debounce_time = 0.5
        if not hasattr(self, "config"):
            self.config = self._load()
        self._initialized = True

    def get_column_widths(self, table_name):
        """테이블 이름에 해당하는 저장된 컬럼 너비 목록을 반환한다."""
        if table_name == Constants.VIEW_RESULT:
            return self.get(Constants.CONFIG_KEY_RESULT_COLUMN_WIDTHS, [60, 400, 100, 60])
        elif table_name == Constants.VIEW_MATCH:
            return self.get(Constants.CONFIG_KEY_MATCH_COLUMN_WIDTHS, [60, 400, 400])
        return None

    def save_column_widths(self, table_name, widths):
        """테이블 이름에 해당하는 컬럼 너비를 저장한다."""
        if table_name == Constants.VIEW_RESULT:
            self.set(Constants.CONFIG_KEY_RESULT_COLUMN_WIDTHS, widths)
        elif table_name == Constants.VIEW_MATCH:
            self.set(Constants.CONFIG_KEY_MATCH_COLUMN_WIDTHS, widths)

    def get_case_insensitive(self):
        """대소문자 구분 옵션 값을 반환한다."""
        with self._config_lock:
            return self.config.get(Constants.CONFIG_KEY_CASE_INSENSITIVE, False)

    def set_case_insensitive(self, value):
        """대소문자 구분 옵션 값을 설정하고 저장한다."""
        with self._config_lock:
            self.config[Constants.CONFIG_KEY_CASE_INSENSITIVE] = value
        self.save()

    def _load(self):
        """설정 파일을 로드하고 기본값과 병합해 반환한다."""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding=Constants.ENC_UTF8) as f:
                    data = json.load(f)
                    if not isinstance(data, dict):
                        logger.warning(AppStrings.LOG_CFG_INVALID_VER)
                        return copy.deepcopy(self.defaults)
                    loaded_version = data.get(Constants.CONFIG_KEY_VERSION, 0)
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
                self.last_load_error = e
                logger.warning(AppStrings.LOG_CFG_LOAD_FAIL.format(e))
            except (IOError, OSError) as e:
                self.last_load_error = e
                logger.error(AppStrings.LOG_CFG_READ_ERROR.format(e))
            except Exception as e:
                self.last_load_error = e
                logger.error(f"Unexpected config load error: {e}")
        return copy.deepcopy(self.defaults)

    def _migrate_config(self, config: dict, from_version: int) -> dict:
        """구버전 설정을 현재 버전 스키마로 마이그레이션한다."""
        if from_version < 1:
            config[Constants.CONFIG_KEY_VERSION] = 1
        return config

    def save(self):
        """디바운스 저장을 예약한다."""
        # 타이머 취소와 새 타이머 시작 사이의 경쟁 상태 방지를 위해 락 획득
        with self._save_lock:
            self._cancel_start_unlocked()
            self._save_timer = threading.Timer(self._save_debounce_time, self.save_immediately)
            self._save_timer.name = "ConfigSaveTimer"
            self._save_timer.start()

    def _cancel_start_unlocked(self):
        """내부용: 락이 이미 확보된 상태에서 타이머 취소."""
        if self._save_timer is not None:
            self._save_timer.cancel()
            self._save_timer = None

    def cancel_start(self):
        """예약된 저장 타이머를 취소한다."""
        with self._save_lock:
            self._cancel_start_unlocked()

    def stop(self) -> bool:
        """종료 시점에 예약 저장을 취소하고 즉시 저장하며 성공 여부를 반환한다."""
        self.cancel_start()
        return self.save_immediately()

    def save_immediately(self) -> bool:
        """설정 파일을 원자적으로 즉시 저장한다."""
        temp_path = self.config_path + Constants.TEMP_FILE_SUFFIX
        old_path = self.config_path + Constants.BACKUP_FILE_SUFFIX
        with self._save_lock:
            if self._save_timer is not None:
                self._save_timer.cancel()
                self._save_timer = None
            for attempt in range(5):
                try:
                    config_dir = os.path.dirname(self.config_path)
                    if not os.path.exists(config_dir):
                        os.makedirs(config_dir, exist_ok=True)
                    with self._config_lock:
                        config_snapshot = copy.deepcopy(self.config)
                    with open(temp_path, "w", encoding=Constants.ENC_UTF8) as f:
                        json.dump(config_snapshot, f, indent=4, ensure_ascii=False)
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
                    self.last_save_error = None  # 성공 시 에러 상태 초기화
                    return True
                except (IOError, OSError) as e:
                    self.last_save_error = e
                    if attempt < 4:
                        logger.debug(AppStrings.LOG_CFG_SAVE_RETRY.format(attempt + 1, e))
                        time.sleep(0.1 * (attempt + 1))
                    else:
                        logger.error(AppStrings.LOG_CFG_SAVE_FAIL.format(e))
                except Exception as e:
                    self.last_save_error = e
                    logger.error(AppStrings.LOG_CFG_SAVE_FAIL.format(e), exc_info=True)
                    break
                finally:
                    if os.path.exists(temp_path):
                        try:
                            os.remove(temp_path)
                        except OSError:
                            pass
            # 루프 종료 후 성공하지 못했음이 명시적임
            return False

    def get(self, key, default=None):
        """설정 값을 조회한다."""
        with self._config_lock:
            return self.config.get(key, default)

    def set(self, key, value):
        """설정 값을 변경하고 저장을 예약한다."""
        with self._config_lock:
            self.config[key] = value
        self.save()

    def add_history(self, search_text):
        """검색어 히스토리를 최신순으로 추가한다."""
        if not search_text:
            return
        with self._config_lock:
            history = self.config.get(Constants.CONFIG_KEY_HISTORY)
            if not isinstance(history, list):
                history = []
                self.config[Constants.CONFIG_KEY_HISTORY] = history
            if search_text in history:
                history.remove(search_text)
            history.insert(0, search_text)
            self.config[Constants.CONFIG_KEY_HISTORY] = history[:20]
        self.save()

    def remove_history_item(self, text):
        with self._config_lock:
            history = self.config.get(Constants.CONFIG_KEY_HISTORY, [])
            if isinstance(history, list) and text in history:
                history.remove(text)
                # 락이 해제되기 전에 저장을 예약하여 원자성 확보
                self.save()
            else:
                return

    def clear_history(self):
        with self._config_lock:
            self.config[Constants.CONFIG_KEY_HISTORY] = []
            self.save()

    def get_history(self):
        with self._config_lock:
            history = self.config.get(Constants.CONFIG_KEY_HISTORY, [])
            return history if isinstance(history, list) else []

    def add_filename_history(self, filename):
        """파일명 필터 히스토리를 최신순으로 추가한다."""
        if not filename:
            return
        with self._config_lock:
            history = self.config.get(Constants.CONFIG_KEY_FILENAME_HISTORY)
            if not isinstance(history, list):
                history = []
                self.config[Constants.CONFIG_KEY_FILENAME_HISTORY] = history
            if filename in history:
                history.remove(filename)
            history.insert(0, filename)
            self.config[Constants.CONFIG_KEY_FILENAME_HISTORY] = history[:20]
            self.save()

    def remove_filename_history_item(self, text):
        with self._config_lock:
            history = self.config.get(Constants.CONFIG_KEY_FILENAME_HISTORY, [])
            if isinstance(history, list) and text in history:
                history.remove(text)
                self.save()
            else:
                return

    def clear_filename_history(self):
        with self._config_lock:
            self.config[Constants.CONFIG_KEY_FILENAME_HISTORY] = []
            self.save()

    def get_filename_history(self):
        with self._config_lock:
            history = self.config.get(Constants.CONFIG_KEY_FILENAME_HISTORY, [])
            return history if isinstance(history, list) else []

    def _normalize_filter_container(self, value, fallback):
        if isinstance(value, dict):
            return copy.deepcopy(value)
        if isinstance(value, list):
            return copy.deepcopy(value)
        if isinstance(value, tuple):
            return list(value)
        return copy.deepcopy(fallback)

    def _ensure_filters_dict(self):
        default_filters = copy.deepcopy(self.defaults.get(Constants.CONFIG_KEY_FILTERS, {}))
        with self._config_lock:
            filters = self.config.get(Constants.CONFIG_KEY_FILTERS)
            if not isinstance(filters, dict):
                filters = default_filters
            else:
                filters = copy.deepcopy(filters)
        folders_default = default_filters.get(Constants.CONFIG_KEY_FOLDERS, [])
        extensions_default = default_filters.get(Constants.CONFIG_KEY_EXTENSIONS, [])
        filenames_default = default_filters.get(Constants.CONFIG_KEY_FILENAMES, [])
        filters[Constants.CONFIG_KEY_FOLDERS] = self._normalize_filter_container(
            filters.get(Constants.CONFIG_KEY_FOLDERS), folders_default
        )
        filters[Constants.CONFIG_KEY_EXTENSIONS] = self._normalize_filter_container(
            filters.get(Constants.CONFIG_KEY_EXTENSIONS), extensions_default
        )
        filters[Constants.CONFIG_KEY_FILENAMES] = self._normalize_filter_container(
            filters.get(Constants.CONFIG_KEY_FILENAMES), filenames_default
        )
        with self._config_lock:
            self.config[Constants.CONFIG_KEY_FILTERS] = filters
        return filters

    def update_filters(self, folders, extensions, filenames=None):
        filters = self._ensure_filters_dict()
        filters[Constants.CONFIG_KEY_FOLDERS] = self._normalize_filter_container(
            folders, self.defaults[Constants.CONFIG_KEY_FILTERS][Constants.CONFIG_KEY_FOLDERS]
        )
        filters[Constants.CONFIG_KEY_EXTENSIONS] = self._normalize_filter_container(
            extensions, self.defaults[Constants.CONFIG_KEY_FILTERS][Constants.CONFIG_KEY_EXTENSIONS]
        )
        if filenames is not None:
            filters[Constants.CONFIG_KEY_FILENAMES] = self._normalize_filter_container(
                filenames, self.defaults[Constants.CONFIG_KEY_FILTERS][Constants.CONFIG_KEY_FILENAMES]
            )
        with self._config_lock:
            self.config[Constants.CONFIG_KEY_FILTERS] = filters
        self.save()

    def get_filters(self):
        return copy.deepcopy(self._ensure_filters_dict())

    def set_window_state(self, geometry, state):
        """메인 윈도우 지오메트리/상태를 저장한다."""
        with self._config_lock:
            self.config[Constants.CONFIG_KEY_GEOMETRY] = geometry.toHex().data().decode()
            self.config[Constants.CONFIG_KEY_WINDOW_STATE] = state.toHex().data().decode()
        self.save()

    def get_window_state(self):
        with self._config_lock:
            return self.config.get(Constants.CONFIG_KEY_GEOMETRY), self.config.get(Constants.CONFIG_KEY_WINDOW_STATE)

    def set_splitter_states(self, main_state, result_state, filter_state=None):
        with self._config_lock:
            if main_state:
                self.config[Constants.CONFIG_KEY_MAIN_SPLITTER_STATE] = main_state.toHex().data().decode()
            if result_state:
                self.config[Constants.CONFIG_KEY_RESULT_SPLITTER_STATE] = result_state.toHex().data().decode()
            if filter_state:
                self.config[Constants.CONFIG_KEY_FILTER_SPLITTER_STATE] = filter_state.toHex().data().decode()
        self.save()

    def set_dock_state(self, state):
        """도크 레이아웃 상태를 저장한다."""
        if state:
            with self._config_lock:
                self.config[Constants.CONFIG_KEY_DOCK_LAYOUT_STATE] = state.toHex().data().decode()
            self.save()

    def get_dock_state(self):
        """저장된 도크 레이아웃 상태를 반환한다."""
        with self._config_lock:
            return self.config.get(Constants.CONFIG_KEY_DOCK_LAYOUT_STATE)

    def get_lock_dock_layout(self):
        """도크 레이아웃 잠금 여부를 반환한다."""
        with self._config_lock:
            return self.config.get(Constants.CONFIG_KEY_LOCK_DOCK_LAYOUT, False)

    def set_lock_dock_layout(self, lock):
        """도크 레이아웃 잠금 여부를 설정한다."""
        with self._config_lock:
            self.config[Constants.CONFIG_KEY_LOCK_DOCK_LAYOUT] = lock
        self.save()

    def get_tab_order(self):
        """저장된 탭 순서를 반환한다."""
        with self._config_lock:
            tabs = self.config.get(Constants.CONFIG_KEY_TABS, [])
            return copy.deepcopy(tabs) if isinstance(tabs, list) else []

    def set_tab_order(self, tabs):
        """탭 순서를 저장한다."""
        with self._config_lock:
            self.config[Constants.CONFIG_KEY_TABS] = list(tabs) if isinstance(tabs, list) else []
        self.save()

    def _sanitize_session_name(self, name: str) -> str:
        """세션 이름을 안전한 파일명으로 정규화한다."""
        from sf_utils.file_helper import sanitize_filename

        base_name = os.path.basename(name)
        sanitized = sanitize_filename(base_name)
        return sanitized if sanitized else "default"

    @staticmethod
    def _session_name_hash(name: str) -> str:
        digest = hashlib.sha1(str(name).encode(Constants.ENC_UTF8)).hexdigest()
        return digest[:10]

    def _session_base_path(self, safe_name: str) -> str:
        return os.path.join(self.sessions_dir, f"{safe_name}{Constants.JSON_EXTENSION}")

    def _read_session_title(self, file_path: str) -> Optional[str]:
        try:
            with open(file_path, "r", encoding=Constants.ENC_UTF8) as f:
                payload = json.load(f)
            if isinstance(payload, dict):
                session_name = payload.get(self._SESSION_NAME_META_KEY)
                if isinstance(session_name, str) and session_name.strip():
                    return session_name
                title = payload.get("title")
                if isinstance(title, str) and title.strip():
                    return title
        except Exception as e:
            logger.debug(AppStrings.LOG_CFG_SESSION_TITLE_READ_FAIL.format(e))
            return None
        return None

    def _find_existing_session_file(self, name: str) -> Optional[str]:
        safe_name = self._sanitize_session_name(name)
        base_path = self._session_base_path(safe_name)
        if not os.path.exists(self.sessions_dir):
            return None
        target_name = str(name)
        if os.path.exists(base_path):
            base_title = self._read_session_title(base_path)
            if base_title == target_name:
                return base_path
            if base_title is None and os.path.basename(target_name) == safe_name:
                return base_path
        prefix = f"{safe_name}_"
        try:
            for filename in os.listdir(self.sessions_dir):
                if not filename.lower().endswith(Constants.JSON_EXTENSION):
                    continue
                stem = os.path.splitext(filename)[0]
                if not stem.startswith(prefix):
                    continue
                candidate = os.path.join(self.sessions_dir, filename)
                if self._read_session_title(candidate) == target_name:
                    return candidate
        except Exception as e:
            logger.warning(AppStrings.LOG_CFG_SESSION_SEARCH_FAIL.format(e))
            return None
        return None

    def _resolve_session_file_path(self, name: str, for_write: bool = False) -> str:
        safe_name = self._sanitize_session_name(name)
        base_path = self._session_base_path(safe_name)
        existing = self._find_existing_session_file(name)
        if existing:
            return existing
        if not for_write or not os.path.exists(base_path):
            return base_path
        suffix = self._session_name_hash(name)
        return os.path.join(self.sessions_dir, f"{safe_name}_{suffix}{Constants.JSON_EXTENSION}")

    def save_session(self, name, data):
        """세션 데이터를 파일로 원자적으로 저장한다."""
        file_path = self._resolve_session_file_path(name, for_write=True)
        temp_path = file_path + Constants.TEMP_FILE_SUFFIX
        try:
            if not os.path.exists(self.sessions_dir):
                os.makedirs(self.sessions_dir, exist_ok=True)
            payload = copy.deepcopy(data) if isinstance(data, dict) else data
            if isinstance(payload, dict):
                payload[self._SESSION_NAME_META_KEY] = str(name)

            with open(temp_path, "w", encoding=Constants.ENC_UTF8) as f:
                json.dump(payload, f, ensure_ascii=False, indent=4)

            os.replace(temp_path, file_path)
            return True
        except Exception as e:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            logger.error(AppStrings.LOG_SES_SAVE_FAIL.format(name, e))
            return False

    def load_session(self, name):
        """세션 데이터를 파일에서 로드한다."""
        file_path = self._resolve_session_file_path(name)
        if not os.path.exists(file_path):
            return None
        try:
            with open(file_path, "r", encoding=Constants.ENC_UTF8) as f:
                payload = json.load(f)
                if isinstance(payload, dict):
                    payload.pop(self._SESSION_NAME_META_KEY, None)
                return payload
        except Exception as e:
            logger.error(AppStrings.LOG_SES_LOAD_FAIL.format(name, e))
            return None

    def delete_session(self, name):
        """세션 파일을 삭제한다."""
        file_path = self._resolve_session_file_path(name)
        if not os.path.exists(file_path):
            return
        try:
            os.remove(file_path)
        except Exception as e:
            logger.error(AppStrings.LOG_SES_DELETE_FAIL.format(name, e))

    def get_all_session_names(self):
        """저장된 모든 세션 이름 목록을 반환한다."""
        if not os.path.exists(self.sessions_dir):
            return []
        sessions: List[str] = []
        seen = set()
        try:
            for filename in os.listdir(self.sessions_dir):
                if filename.lower().endswith(Constants.JSON_EXTENSION):
                    file_path = os.path.join(self.sessions_dir, filename)
                    display_name = self._read_session_title(file_path)
                    if not display_name:
                        display_name = os.path.splitext(filename)[0]
                    if display_name not in seen:
                        seen.add(display_name)
                        sessions.append(display_name)
        except Exception as e:
            logger.error(AppStrings.LOG_SES_LIST_FAIL.format(e))
        return sessions

    def get_splitter_states(self):
        with self._config_lock:
            return (
                self.config.get(Constants.CONFIG_KEY_MAIN_SPLITTER_STATE),
                self.config.get(Constants.CONFIG_KEY_RESULT_SPLITTER_STATE),
                self.config.get(Constants.CONFIG_KEY_FILTER_SPLITTER_STATE),
            )

    def get_theme(self):
        with self._config_lock:
            return self.config.get(Constants.CONFIG_KEY_THEME, Constants.DEFAULT_THEME)

    def set_theme(self, theme):
        with self._config_lock:
            self.config[Constants.CONFIG_KEY_THEME] = theme
        self.save()

    def clear_all_logs(self):
        """설정 폴더의 로그 파일을 모두 삭제한다."""
        if not os.path.exists(self.config_dir):
            return
        import glob

        log_pattern = os.path.join(self.config_dir, Constants.LOG_FILE_GLOB)
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
        """설정/세션/로그를 초기화한다."""
        self.clear_all_logs()
        try:
            import shutil

            if os.path.exists(self.sessions_dir):
                shutil.rmtree(self.sessions_dir, ignore_errors=True)
                os.makedirs(self.sessions_dir, exist_ok=True)
        except Exception as e:
            logger.error(AppStrings.LOG_CFG_SESSION_DIR_CLEAR_FAIL.format(e))
        if os.path.exists(self.config_path):
            try:
                os.remove(self.config_path)
            except OSError as e:
                logger.error(AppStrings.LOG_CFG_DELETE_FAIL_MSG.format(e))
        with self._config_lock:
            self.config = copy.deepcopy(self.defaults)
        self.save()
        logger.info(AppStrings.LOG_CFG_ALL_DATA_CLEARED)

    def get_global_hotkey(self):
        with self._config_lock:
            return self.config.get(Constants.CONFIG_KEY_GLOBAL_HOTKEY, Constants.DEFAULT_GLOBAL_HOTKEY)

    def set_global_hotkey(self, hotkey):
        with self._config_lock:
            self.config[Constants.CONFIG_KEY_GLOBAL_HOTKEY] = hotkey
        self.save()

    def get_run_at_startup(self):
        with self._config_lock:
            return self.config.get(Constants.CONFIG_KEY_RUN_AT_STARTUP, False)

    def set_run_at_startup(self, run):
        with self._config_lock:
            self.config[Constants.CONFIG_KEY_RUN_AT_STARTUP] = run
        self.save()

    def get_close_to_tray(self):
        """트레이로 닫기 옵션 값을 반환한다."""
        with self._config_lock:
            return self.config.get(Constants.CONFIG_KEY_CLOSE_TO_TRAY, False)

    def set_close_to_tray(self, value):
        with self._config_lock:
            self.config[Constants.CONFIG_KEY_CLOSE_TO_TRAY] = value
        self.save()
