import copy
import hashlib
import json
import os
import tempfile
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, cast

from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants
from sf_utils.logger import logger


class ConfigManager:
    """애플리케이션 설정을 관리하는 싱글턴 클래스."""

    _instance = None
    _lock = threading.Lock()
    _SESSION_NAME_META_KEY = "__sf_session_name"

    def __new__(cls, *_, **kwargs):
        with cls._lock:
            # _instance는 클래스 속성입니다. hasattr 가드를 통해
            # 테스트 환경 등에서 _instance가 삭제될 경우에도 안정성을 확보합니다.
            if not hasattr(cls, "_instance") or cls._instance is None:
                cls._instance = super(ConfigManager, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        """설정 경로, 기본값, 저장 타이머를 초기화한다."""
        if getattr(self, "_initialized", False):
            return
        # "모든 데이터 삭제" 직후 참입니다. 이후 새 세션을 저장하기 전까지 종료할 때 열린 탭을
        # 다시 저장하지 않게 하는 데 씁니다(검수 U-9).
        self.data_cleared = False
        # 초기화 중 예외가 발생하면 차후 재시도가 가능하도록 인스턴스를 초기화합니다.
        try:
            self._init_body()
        except Exception:
            type(self)._instance = None
            raise

    def _init_body(self):
        """실제 초기화를 담당하는 내부 로직입니다."""
        app_data = os.getenv(Constants.ENV_APPDATA)
        if not app_data:
            app_data = os.path.join(os.path.expanduser("~"), Constants.APPDATA_FALLBACK_DIR)
        self.config_dir = os.path.join(app_data, Constants.APP_NAME)
        self.uses_temporary_storage = False
        try:
            os.makedirs(self.config_dir, exist_ok=True)
        except Exception as e:
            self.config_dir = os.path.join(tempfile.gettempdir(), Constants.APPDATA_TEMP_DIR)
            os.makedirs(self.config_dir, exist_ok=True)
            self.uses_temporary_storage = True
            # AppData 접근이 실패하면 임시 폴더로 전환한다.
            logger.warning(AppStrings.LOG_CFG_APPDATA_FALLBACK.format(e, self.config_dir))
        self.config_path = os.path.join(self.config_dir, Constants.CONFIG_FILENAME)
        self.CURRENT_CONFIG_VERSION = Constants.CONFIG_SCHEMA_VERSION
        self._defaults: Dict[str, Any] = {
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
            Constants.CONFIG_KEY_LANGUAGE: Constants.DEFAULT_LANGUAGE,
            Constants.CONFIG_KEY_RESULT_COLUMN_WIDTHS: [60, 400, 100, 60],
            Constants.CONFIG_KEY_MATCH_COLUMN_WIDTHS: [60, 400, 400],
            Constants.CONFIG_KEY_CONTEXT_BEFORE_LINES: Constants.DEFAULT_CONTEXT_PREVIEW_LINES,
            Constants.CONFIG_KEY_COMPACT_RESULT_ROWS: True,
            Constants.CONFIG_KEY_FILTER_LAYOUT: Constants.DEFAULT_FILTER_LAYOUT,
            Constants.CONFIG_KEY_EXCLUDE_HIDDEN: True,
            Constants.CONFIG_KEY_CONTEXT_AFTER_LINES: Constants.DEFAULT_CONTEXT_PREVIEW_LINES,
            Constants.CONFIG_KEY_EXTERNAL_EDITOR: {
                Constants.CONFIG_KEY_EDITOR_TYPE: Constants.DEFAULT_EXTERNAL_EDITOR,
                Constants.CONFIG_KEY_EDITOR_CUSTOM_PATH: "",
                Constants.CONFIG_KEY_EDITOR_CUSTOM_ARGS: "{file}:{line}",
            },
            Constants.CONFIG_KEY_LOG_RETENTION: {
                Constants.CONFIG_KEY_LOG_RETENTION_ENABLED: True,
                Constants.CONFIG_KEY_LOG_RETENTION_MAX_FILES: 10,
                Constants.CONFIG_KEY_LOG_RETENTION_MAX_DAYS: 3,
            },
            Constants.CONFIG_KEY_DOCK_LAYOUT_STATE: None,
            Constants.CONFIG_KEY_LOCK_DOCK_LAYOUT: False,
            Constants.CONFIG_KEY_EXCLUDE_BINARY: True,
            Constants.CONFIG_KEY_TABS: [],
            Constants.CONFIG_KEY_ADVANCED: {
                key: spec["default"]
                for key, spec in Constants.ADVANCED_SETTING_SPECS.items()
            },
            Constants.CONFIG_KEY_SETTING_DEFAULT_VERSIONS: dict(Constants.SETTING_DEFAULT_VERSIONS),
        }
        self._config_lock = threading.RLock()
        self._save_lock = threading.Lock()
        self._config: Dict[str, Any] = self._load()
        self.sessions_dir: str = os.path.join(self.config_dir, Constants.SESSIONS_DIRNAME)
        os.makedirs(self.sessions_dir, exist_ok=True)
        # 교착 상태를 방지하기 위해 반드시 _config_lock보다 _save_lock을 먼저 획득해야 합니다.
        self._save_timer = None
        self._save_debounce_time = 0.5
        self._initialized = True

    @property
    def config(self) -> Dict[str, Any]:
        """Return a detached configuration snapshot for compatibility callers."""
        return self.get_config_snapshot()

    @property
    def defaults(self) -> Dict[str, Any]:
        """Return a detached defaults snapshot for compatibility callers."""
        with self._config_lock:
            return copy.deepcopy(self._defaults)

    def get_config_snapshot(self) -> Dict[str, Any]:
        """Return configuration data without exposing internal mutable state."""
        with self._config_lock:
            return copy.deepcopy(self._config)

    def get_defaults(self) -> Dict[str, Any]:
        """Return default configuration data without exposing internal mutable state."""
        with self._config_lock:
            return copy.deepcopy(self._defaults)

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

    def _load(self):
        """설정 파일을 로드하고 기본값과 병합해 반환한다."""
        load_path = self.config_path
        if not os.path.exists(load_path):
            # Recover files stranded by the former rename-based save fallback.
            backup_path = self.config_path + Constants.BACKUP_FILE_SUFFIX
            if os.path.exists(backup_path):
                load_path = backup_path
        if os.path.exists(load_path):
            try:
                with open(load_path, "r", encoding=Constants.ENC_UTF8) as f:
                    data = json.load(f)
                    if not isinstance(data, dict):
                        logger.warning(AppStrings.LOG_CFG_INVALID_VER)
                        return copy.deepcopy(self._defaults)
                    loaded_version = data.get(Constants.CONFIG_KEY_VERSION, 0)
                    if type(loaded_version) is not int or loaded_version < 0:
                        logger.warning("Invalid configuration version; applying current defaults")
                        loaded_version = 0
                    if loaded_version < self.CURRENT_CONFIG_VERSION:
                        logger.info(AppStrings.LOG_CFG_MIGRATION.format(loaded_version, self.CURRENT_CONFIG_VERSION))
                        data = self._migrate_config(data, loaded_version)
                    elif loaded_version > self.CURRENT_CONFIG_VERSION:
                        logger.warning(
                            AppStrings.LOG_CFG_MIGRATE_WARN.format(loaded_version, self.CURRENT_CONFIG_VERSION)
                        )
                    merged = copy.deepcopy(self._defaults)
                    for k, v in data.items():
                        if k in merged and v is not None:
                            if isinstance(merged[k], list) and not isinstance(v, list):
                                continue
                            if isinstance(merged[k], dict) and not isinstance(v, dict):
                                continue
                            merged[k] = v
                    # Validate scalar settings before UI code consumes them.
                    # A malformed JSON value must not prevent application start.
                    language = merged.get(Constants.CONFIG_KEY_LANGUAGE)
                    if not isinstance(language, str) or language not in {"ko", "en"}:
                        merged[Constants.CONFIG_KEY_LANGUAGE] = self._defaults[
                            Constants.CONFIG_KEY_LANGUAGE
                        ]
                    theme = merged.get(Constants.CONFIG_KEY_THEME)
                    if not isinstance(theme, str) or theme.lower() not in {"dark", "light", "auto"}:
                        merged[Constants.CONFIG_KEY_THEME] = self._defaults.get(
                            Constants.CONFIG_KEY_THEME, Constants.DEFAULT_THEME
                        )
                    if merged.get(Constants.CONFIG_KEY_FILTER_LAYOUT) not in (
                        Constants.FILTER_LAYOUT_TABS,
                        Constants.FILTER_LAYOUT_STACKED,
                    ):
                        merged[Constants.CONFIG_KEY_FILTER_LAYOUT] = Constants.DEFAULT_FILTER_LAYOUT
                    # 예전 키의 값(대부분 당시 기본값)은 쓰지 않고 지웁니다.
                    merged.pop(Constants.LEGACY_CONFIG_KEY_FILTER_LAYOUT, None)
                    for key in (
                        Constants.CONFIG_KEY_COMPACT_RESULT_ROWS,
                        Constants.CONFIG_KEY_EXCLUDE_HIDDEN,
                        Constants.CONFIG_KEY_EXCLUDE_BINARY,
                        Constants.CONFIG_KEY_LOCK_DOCK_LAYOUT,
                    ):
                        if not isinstance(merged.get(key), bool):
                            merged[key] = self._defaults[key]
                    for key in (
                        Constants.CONFIG_KEY_CONTEXT_BEFORE_LINES,
                        Constants.CONFIG_KEY_CONTEXT_AFTER_LINES,
                    ):
                        merged[key] = self._normalize_bounded_int(
                            merged.get(key),
                            self._defaults[key],
                            0,
                            Constants.MAX_CONTEXT_PREVIEW_LINES,
                        )
                    for key in (
                        Constants.CONFIG_KEY_RESULT_COLUMN_WIDTHS,
                        Constants.CONFIG_KEY_MATCH_COLUMN_WIDTHS,
                    ):
                        default_widths = self._defaults[key]
                        widths = merged.get(key)
                        if (
                            not isinstance(widths, list)
                            or len(widths) != len(default_widths)
                            or any(type(width) is not int or not 0 <= width <= 10_000 for width in widths)
                        ):
                            merged[key] = copy.deepcopy(default_widths)
                    for key in (
                        Constants.CONFIG_KEY_GEOMETRY,
                        Constants.CONFIG_KEY_WINDOW_STATE,
                        Constants.CONFIG_KEY_MAIN_SPLITTER_STATE,
                        Constants.CONFIG_KEY_RESULT_SPLITTER_STATE,
                        Constants.CONFIG_KEY_FILTER_SPLITTER_STATE,
                        Constants.CONFIG_KEY_DOCK_LAYOUT_STATE,
                    ):
                        value = merged.get(key)
                        if value is not None and not isinstance(value, str):
                            merged[key] = self._defaults.get(key)
                    merged[Constants.CONFIG_KEY_ADVANCED] = self._normalize_advanced_settings(
                        merged.get(Constants.CONFIG_KEY_ADVANCED),
                    )
                    merged[Constants.CONFIG_KEY_LOG_RETENTION] = self._normalize_log_retention(
                        merged.get(Constants.CONFIG_KEY_LOG_RETENTION)
                    )
                    merged = self._apply_default_version_updates(merged, data)
                    return merged
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                logger.warning(AppStrings.LOG_CFG_LOAD_FAIL.format(e))
                self._backup_unreadable_config()
            except (IOError, OSError) as e:
                logger.error(AppStrings.LOG_CFG_READ_ERROR.format(e))
            except Exception as e:
                logger.error(AppStrings.LOG_CFG_UNEXPECTED_LOAD_ERROR.format(e))
        return copy.deepcopy(self._defaults)

    def _backup_unreadable_config(self) -> None:
        """읽지 못한 설정 파일을 기본값으로 덮어쓰기 전에 옆에 보관합니다(검수 E-1).

        폴더·필터·검색 기록을 손으로 되살릴 수 있도록 `설정파일.corrupt-날짜시각`으로 복사합니다.
        """
        try:
            import shutil
            import time as _time

            backup = f"{self.config_path}.corrupt-{_time.strftime('%Y%m%d-%H%M%S')}"
            if not os.path.exists(backup):
                shutil.copy2(self.config_path, backup)
            logger.warning(AppStrings.LOG_CFG_CORRUPT_BACKUP.format(backup))
        except OSError as error:
            logger.error(AppStrings.LOG_CFG_READ_ERROR.format(error))

    def _apply_default_version_updates(self, merged: dict, loaded: dict) -> dict:
        """Reset only settings whose shipped default version advanced.

        Settings without metadata are treated as version 1. This preserves
        existing user values on first adoption while allowing future default
        changes to be explicit and deterministic.
        """
        stored_versions = loaded.get(Constants.CONFIG_KEY_SETTING_DEFAULT_VERSIONS, {})
        if not isinstance(stored_versions, dict):
            stored_versions = {}
        advanced = merged.get(Constants.CONFIG_KEY_ADVANCED)
        if not isinstance(advanced, dict):
            advanced = {}
        for key, current_version in Constants.SETTING_DEFAULT_VERSIONS.items():
            try:
                previous_version = int(stored_versions.get(key, 1))
            except (TypeError, ValueError):
                previous_version = 1
            if previous_version < current_version:
                advanced[key] = Constants.ADVANCED_SETTING_SPECS[key]["default"]
        merged[Constants.CONFIG_KEY_ADVANCED] = self._normalize_advanced_settings(advanced)
        merged[Constants.CONFIG_KEY_SETTING_DEFAULT_VERSIONS] = dict(Constants.SETTING_DEFAULT_VERSIONS)
        merged[Constants.CONFIG_KEY_VERSION] = self.CURRENT_CONFIG_VERSION
        return merged

    def _migrate_config(self, config: dict, from_version: int) -> dict:
        """구버전 설정을 현재 버전 스키마로 마이그레이션한다."""
        if from_version < 1:
            config[Constants.CONFIG_KEY_VERSION] = 1
        if from_version < 2:
            advanced = config.get(Constants.CONFIG_KEY_ADVANCED)
            if isinstance(advanced, dict):
                old_default = 80
                if advanced.get(Constants.CONFIG_KEY_MAX_JSON_DOM_SIZE) == old_default:
                    advanced[Constants.CONFIG_KEY_MAX_JSON_DOM_SIZE] = Constants.DEFAULT_MAX_JSON_DOM_SIZE_MB
            config[Constants.CONFIG_KEY_VERSION] = 2
        if from_version < 3:
            # 더 이상 어떤 UI나 검색 경로에서도 소비하지 않는 v1 레거시 키입니다.
            config.pop("case_insensitive", None)
            config[Constants.CONFIG_KEY_VERSION] = 3
        return config

    def _normalize_advanced_settings(self, settings: Any) -> dict[str, Any]:
        """Validate advanced settings, restoring invalid values to defaults."""
        source = settings if isinstance(settings, dict) else {}
        normalized: dict[str, Any] = {}
        for key, spec in Constants.ADVANCED_SETTING_SPECS.items():
            if "choices" in spec:
                value = source.get(key, spec["default"])
                normalized[key] = value if isinstance(value, str) and value in spec["choices"] else spec["default"]
                continue
            if type(spec["default"]) is bool:
                raw_value = source.get(key, spec["default"])
                normalized[key] = raw_value if type(raw_value) is bool else spec["default"]
                continue
            default = int(spec["default"])
            minimum = int(spec["minimum"])
            maximum = int(spec["maximum"])
            raw_value = source.get(key, default)
            is_valid = type(raw_value) is int and minimum <= raw_value <= maximum
            value = raw_value if is_valid else default
            if key in source and not is_valid:
                logger.warning(
                    "Invalid advanced setting %s; restoring its default value",
                    key,
                )
            normalized[key] = value
        return normalized

    @staticmethod
    def _normalize_bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
        """Accept only JSON integer values in range; restore defaults otherwise."""
        if type(value) is not int or not minimum <= value <= maximum:
            return default
        return value

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
        # 교착 상태 방지: _save_lock을 먼저 획득한 후 내부에서 _config_lock을 잡습니다.
        # 다른 메서드들과의 획득 순서를 일원화합니다.
        with self._save_lock:
            if self._save_timer is not None:
                self._save_timer.cancel()
                self._save_timer = None
            
            # 스냅샷 촬영을 위해 config_lock만 아주 짧게 획득
            with self._config_lock:
                config_snapshot = copy.deepcopy(self._config)

            for attempt in range(5):
                temp_path = None
                try:
                    config_dir = os.path.dirname(self.config_path)
                    if not os.path.exists(config_dir):
                        os.makedirs(config_dir, exist_ok=True)
                    
                    candidate = os.path.join(config_dir, f".config_{uuid.uuid4().hex}{Constants.TEMP_FILE_SUFFIX}")
                    # Exclusive creation protects other writers. Unlike the
                    # Windows tempfile helper, permission failures are bounded
                    # by our five retries rather than an internal name loop.
                    with open(candidate, "x", encoding=Constants.ENC_UTF8) as f:
                        # Register before writing so a partial serialization is
                        # also cleaned up. Close before replacing on Windows.
                        temp_path = candidate
                        json.dump(config_snapshot, f, indent=4, ensure_ascii=False)
                    if not os.path.exists(self.config_path):
                        os.rename(temp_path, self.config_path)
                    else:
                        # Retry without moving the previous settings away.
                        # Failed replacement must preserve the original path.
                        os.replace(temp_path, self.config_path)
                    return True
                except (IOError, OSError) as e:
                    # 종료 시에도 기존 설정 보존을 위해 같은 재시도 정책을 씁니다.
                    # 대기 합계는 1초이며 파일 I/O와 저장 락 대기 시간은 별도입니다.
                    if attempt < 4:
                        logger.debug(AppStrings.LOG_CFG_SAVE_RETRY.format(attempt + 1, e))
                        time.sleep(0.1 * (attempt + 1))
                    else:
                        logger.error(AppStrings.LOG_CFG_SAVE_FAIL.format(e))
                except Exception as e:
                    logger.error(AppStrings.LOG_CFG_SAVE_FAIL.format(e), exc_info=True)
                    break
                finally:
                    if temp_path is not None and os.path.exists(temp_path):
                        try:
                            os.remove(temp_path)
                        except OSError as e:
                            logger.debug(AppStrings.LOG_CFG_TEMP_REMOVE_FAIL.format(e))
            # 루프 종료 후 성공하지 못했음이 명시적임
            return False

    def get(self, key, default=None):
        """설정 값을 조회한다."""
        with self._config_lock:
            return copy.deepcopy(self._config.get(key, default))

    def set(self, key, value):
        """설정 값을 변경하고 저장을 예약한다."""
        with self._config_lock:
            self._config[key] = copy.deepcopy(value)
        self.save()

    def add_history(self, search_text):
        """검색어 히스토리를 최신순으로 추가한다."""
        if not search_text:
            return
        with self._config_lock:
            history = self._config.get(Constants.CONFIG_KEY_HISTORY)
            if not isinstance(history, list):
                history = []
                self._config[Constants.CONFIG_KEY_HISTORY] = history
            if search_text in history:
                history.remove(search_text)
            history.insert(0, search_text)
            self._config[Constants.CONFIG_KEY_HISTORY] = history[:20]
        self.save()



    def get_history(self):
        with self._config_lock:
            history = self._config.get(Constants.CONFIG_KEY_HISTORY, [])
            return copy.deepcopy(history) if isinstance(history, list) else []





    def _normalize_filter_container(self, value, fallback):
        if isinstance(value, dict):
            normalized: dict[str, Any] = {}
            for name, entry in value.items():
                if isinstance(name, str) and name.strip() and isinstance(entry, bool):
                    normalized[name] = entry
                elif name in (Constants.CONFIG_KEY_EXTENSIONS, Constants.CONFIG_KEY_FILENAMES):
                    normalized[name] = self._normalize_filter_container(entry, [])
                elif name in (Constants.PAYLOAD_SPECIAL_MODE, Constants.PAYLOAD_FILENAME_FILTER):
                    normalized[name] = entry if isinstance(entry, str) else ""
            return normalized
        if isinstance(value, (list, tuple)):
            return [entry for entry in value if isinstance(entry, str) and entry.strip()]
        return copy.deepcopy(fallback)

    def _ensure_filters_dict(self):
        # 경쟁 조건을 방지하기 위해 단일 설정 잠금 블록 내에서 처리합니다.
        default_filters = copy.deepcopy(self._defaults.get(Constants.CONFIG_KEY_FILTERS, {}))
        with self._config_lock:
            filters = self._config.get(Constants.CONFIG_KEY_FILTERS)
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
            if Constants.CONFIG_KEY_FOLDER_GROUPS in filters:
                filters[Constants.CONFIG_KEY_FOLDER_GROUPS] = self._normalize_folder_groups(
                    filters.get(Constants.CONFIG_KEY_FOLDER_GROUPS)
                )
            self._config[Constants.CONFIG_KEY_FILTERS] = filters
        return filters

    @staticmethod
    def _normalize_folder_groups(value) -> list:
        """검색 폴더 그룹을 ``[{name, collapsed, folders}]`` 형식으로만 받아들이고 잘못된 항목은 버립니다."""
        groups = []
        for group in value if isinstance(value, list) else []:
            if not isinstance(group, dict):
                continue
            name = group.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            folders = group.get("folders")
            groups.append({
                "name": name.strip(),
                "collapsed": group.get("collapsed") is True,
                "folders": [folder for folder in folders if isinstance(folder, str) and folder.strip()]
                if isinstance(folders, list) else [],
            })
        return groups


    def update_filters(self, folders, extensions, filenames=None, folder_groups=None):
        filters = self._ensure_filters_dict()
        filters[Constants.CONFIG_KEY_FOLDERS] = self._normalize_filter_container(
            folders, self._defaults[Constants.CONFIG_KEY_FILTERS][Constants.CONFIG_KEY_FOLDERS]
        )
        filters[Constants.CONFIG_KEY_EXTENSIONS] = self._normalize_filter_container(
            extensions, self._defaults[Constants.CONFIG_KEY_FILTERS][Constants.CONFIG_KEY_EXTENSIONS]
        )
        if filenames is not None:
            filters[Constants.CONFIG_KEY_FILENAMES] = self._normalize_filter_container(
            filenames, self._defaults[Constants.CONFIG_KEY_FILTERS][Constants.CONFIG_KEY_FILENAMES]
            )
        if folder_groups is not None:
            filters[Constants.CONFIG_KEY_FOLDER_GROUPS] = self._normalize_folder_groups(folder_groups)
        with self._config_lock:
            self._config[Constants.CONFIG_KEY_FILTERS] = filters
        self.save()

    def get_filters(self):
        return copy.deepcopy(self._ensure_filters_dict())

    def set_window_state(self, geometry, state):
        """메인 윈도우 지오메트리/상태를 저장한다."""
        with self._config_lock:
            self._config[Constants.CONFIG_KEY_GEOMETRY] = geometry.toHex().data().decode()
            self._config[Constants.CONFIG_KEY_WINDOW_STATE] = state.toHex().data().decode()
        self.save()

    def get_window_state(self):
        with self._config_lock:
            return self._config.get(Constants.CONFIG_KEY_GEOMETRY), self._config.get(Constants.CONFIG_KEY_WINDOW_STATE)

    def set_splitter_states(self, main_state, result_state, filter_state=None):
        with self._config_lock:
            if main_state:
                self._config[Constants.CONFIG_KEY_MAIN_SPLITTER_STATE] = main_state.toHex().data().decode()
            if result_state:
                self._config[Constants.CONFIG_KEY_RESULT_SPLITTER_STATE] = result_state.toHex().data().decode()
            if filter_state:
                self._config[Constants.CONFIG_KEY_FILTER_SPLITTER_STATE] = filter_state.toHex().data().decode()
        self.save()

    def get_dock_state(self):
        """저장된 도크 레이아웃 상태를 반환한다."""
        with self._config_lock:
            return self._config.get(Constants.CONFIG_KEY_DOCK_LAYOUT_STATE)

    def get_lock_dock_layout(self):
        """도크 레이아웃 잠금 여부를 반환한다."""
        with self._config_lock:
            return self._config.get(Constants.CONFIG_KEY_LOCK_DOCK_LAYOUT, False)

    def set_lock_dock_layout(self, lock):
        """도크 레이아웃 잠금 여부를 설정한다."""
        with self._config_lock:
            self._config[Constants.CONFIG_KEY_LOCK_DOCK_LAYOUT] = lock
        self.save()

    def get_tab_order(self):
        """저장된 탭 순서를 반환한다."""
        with self._config_lock:
            tabs = self._config.get(Constants.CONFIG_KEY_TABS, [])
            return copy.deepcopy(tabs) if isinstance(tabs, list) else []

    def set_tab_order(self, tabs):
        """탭 순서를 저장한다."""
        with self._config_lock:
            self._config[Constants.CONFIG_KEY_TABS] = list(tabs) if isinstance(tabs, list) else []
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
        """세션 파일의 탭 이름입니다(검수 S-3).

        6.4.17부터 이름을 파일 맨 앞에 쓰므로 앞부분만 읽습니다. 이름이 끝에 있는 예전 파일은
        한 번만 전체를 해석하고, 파일 시각·크기가 같으면 기억해 둔 이름을 다시 씁니다.
        """
        try:
            stat = os.stat(file_path)
        except OSError:
            return None
        cache_key = (stat.st_mtime_ns, stat.st_size)
        cache = self.__dict__.setdefault("_session_title_cache", {})
        cached = cache.get(file_path)
        if cached is not None and cached[0] == cache_key:
            return cast(Optional[str], cached[1])
        title = self._read_session_title_from_head(file_path)
        if title is None:
            title = self._read_session_title_full(file_path)
        cache[file_path] = (cache_key, title)
        return title

    def _read_session_title_from_head(self, file_path: str) -> Optional[str]:
        prefix = "{" + json.dumps(self._SESSION_NAME_META_KEY) + ":"
        try:
            with open(file_path, "r", encoding=Constants.ENC_UTF8) as f:
                head = f.read(65536)
            if not head.startswith(prefix):
                return None
            value, _end = json.JSONDecoder().raw_decode(head, len(prefix))
        except (OSError, UnicodeDecodeError, ValueError):
            return None
        return value if isinstance(value, str) and value.strip() else None

    def _read_session_title_full(self, file_path: str) -> Optional[str]:
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
        self.data_cleared = False
        file_path = self._resolve_session_file_path(name, for_write=True)
        temp_path = file_path + Constants.TEMP_FILE_SUFFIX
        try:
            if not os.path.exists(self.sessions_dir):
                os.makedirs(self.sessions_dir, exist_ok=True)
            # Search results are immutable after completion.  A shallow top-level
            # copy is enough to add metadata without duplicating every match.
            # 탭 이름을 맨 앞에 두어 목록을 만들 때 파일 앞부분만 읽게 합니다(검수 S-3).
            payload = {self._SESSION_NAME_META_KEY: str(name), **data} if isinstance(data, dict) else data

            with open(temp_path, "w", encoding=Constants.ENC_UTF8) as f:
                json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

            os.replace(temp_path, file_path)
            return True
        except Exception as e:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError as ee:
                    logger.debug(AppStrings.LOG_SES_TEMP_CLEANUP_FAIL.format(ee))
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
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.error(AppStrings.LOG_SES_LOAD_FAIL.format(name, e))
            self._remove_invalid_session_file(file_path, name)
            return None
        except Exception as e:
            logger.error(AppStrings.LOG_SES_LOAD_FAIL.format(name, e))
            return None

        if not isinstance(payload, dict):
            logger.error(AppStrings.LOG_SES_LOAD_FAIL.format(name, "invalid root object"))
            self._remove_invalid_session_file(file_path, name)
            return None
        payload.pop(self._SESSION_NAME_META_KEY, None)
        return payload

    @staticmethod
    def _remove_invalid_session_file(file_path: str, name: str) -> None:
        """Remove a session file only after its serialized data is proven invalid."""
        try:
            os.remove(file_path)
        except OSError as e:
            logger.error(AppStrings.LOG_SES_DELETE_FAIL.format(name, e))

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
                self._config.get(Constants.CONFIG_KEY_MAIN_SPLITTER_STATE),
                self._config.get(Constants.CONFIG_KEY_RESULT_SPLITTER_STATE),
                self._config.get(Constants.CONFIG_KEY_FILTER_SPLITTER_STATE),
            )

    def get_theme(self):
        with self._config_lock:
            return self._config.get(Constants.CONFIG_KEY_THEME, Constants.DEFAULT_THEME)

    def set_theme(self, theme):
        with self._config_lock:
            self._config[Constants.CONFIG_KEY_THEME] = theme
        self.save()

    def get_language(self):
        """Return the persisted UI language code."""
        from sf_utils.localization import normalize_language

        with self._config_lock:
            return normalize_language(
                self._config.get(Constants.CONFIG_KEY_LANGUAGE, Constants.DEFAULT_LANGUAGE)
            )

    def set_language(self, language):
        """Persist a supported UI language code."""
        from sf_utils.localization import normalize_language

        normalized = normalize_language(language)
        with self._config_lock:
            self._config[Constants.CONFIG_KEY_LANGUAGE] = normalized
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
            except (PermissionError, OSError) as e:
                logger.debug(AppStrings.LOG_CFG_LOG_FILE_CLEANUP_FAIL.format(f, e))
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
            self._config = copy.deepcopy(self._defaults)
        self.save()
        self.data_cleared = True
        logger.info(AppStrings.LOG_CFG_ALL_DATA_CLEARED)


    def get_exclude_binary(self) -> bool:
        """바이너리 파일 제외 옵션 값을 반환한다."""
        with self._config_lock:
            return bool(self._config.get(Constants.CONFIG_KEY_EXCLUDE_BINARY, True))

    def set_exclude_binary(self, value: bool):
        """바이너리 파일 제외 옵션 값을 설정하고 저장한다."""
        with self._config_lock:
            self._config[Constants.CONFIG_KEY_EXCLUDE_BINARY] = value
        self.save()

    def get_advanced_settings(self) -> dict[str, Any]:
        """고급 설정 딕셔너리를 반환한다. (누락된 키는 기본값으로 채운다)"""
        with self._config_lock:
            advanced = self._config.get(Constants.CONFIG_KEY_ADVANCED, {})
            return self._normalize_advanced_settings(advanced)

    def get_log_retention(self) -> dict[str, Any]:
        """Return a detached, type- and range-normalized log retention config."""
        with self._config_lock:
            return self._normalize_log_retention(
                self._config.get(Constants.CONFIG_KEY_LOG_RETENTION)
            )

    def _normalize_log_retention(self, settings: Any) -> dict[str, Any]:
        defaults = self._defaults.get(Constants.CONFIG_KEY_LOG_RETENTION, {})
        result = copy.deepcopy(defaults) if isinstance(defaults, dict) else {}
        if isinstance(settings, dict):
            result.update(copy.deepcopy(settings))

        enabled_key = Constants.CONFIG_KEY_LOG_RETENTION_ENABLED
        if not isinstance(result.get(enabled_key), bool):
            result[enabled_key] = defaults.get(enabled_key, True)
        for key, minimum, maximum in (
            (Constants.CONFIG_KEY_LOG_RETENTION_MAX_FILES, 1, 100),
            (Constants.CONFIG_KEY_LOG_RETENTION_MAX_DAYS, 1, 365),
        ):
            result[key] = self._normalize_bounded_int(
                result.get(key), defaults.get(key, minimum), minimum, maximum
            )
        return result
        
    def set_advanced_settings(self, settings_dict: dict):
        """고급 설정을 업데이트한다."""
        with self._config_lock:
            current = self._config.get(Constants.CONFIG_KEY_ADVANCED, {})
            if not isinstance(current, dict):
                current = copy.deepcopy(self._defaults.get(Constants.CONFIG_KEY_ADVANCED, {}))
            current.update(copy.deepcopy(settings_dict))
            self._config[Constants.CONFIG_KEY_ADVANCED] = self._normalize_advanced_settings(current)
        self.save()
        
    def reset_advanced_settings(self) -> dict[str, Any]:
        """고급 설정을 초기화하고 반환한다."""
        with self._config_lock:
            defaults = copy.deepcopy(self._defaults.get(Constants.CONFIG_KEY_ADVANCED, {}))
            self._config[Constants.CONFIG_KEY_ADVANCED] = defaults
        self.save()
        return defaults  # type: ignore
