import json
import os
import copy
import threading
import time
from utils.logger import logger
from utils.app_strings import AppStrings
from utils.constants import Constants


class ConfigManager:
    """
    애플리케이션의 설정 및 검색 기록(History)을 중앙에서 관리하는 싱글톤(Singleton) 클래스입니다.
    여러 곳에서 동시 접근 시 데이터 일관성을 보장하며, 파일 기반으로 영구적으로 저장합니다.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if not hasattr(cls, "_instance") or cls._instance is None:
                cls._instance = super(ConfigManager, cls).__new__(cls)
                # [Fix] 초기화 여부 플래그를 __new__ 단계에서 확실히 관리
                # __init__은 인스턴스 생성 시마다 호출되므로, 한 번만 초기화되도록 보장 필요
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        """사용자 로컬 AppData 내에 설정 저장소를 확보하고 기본값들을 준비합니다."""
        if getattr(self, "_initialized", False):
            return

        # Windows 기준 AppData/StringFinder 경로를 사용합니다.
        app_data = os.getenv("APPDATA")
        if not app_data:
            # APPDATA 환경변수가 없는 경우 홈 디렉토리 하위로 폴백
            app_data = os.path.join(os.path.expanduser("~"), ".stringfinder")

        self.config_dir = os.path.join(app_data, "StringFinder")
        try:
            os.makedirs(self.config_dir, exist_ok=True)
        except Exception:
            # 폴더 생성 실패 시 임시 폴더로 최종 폴백
            import tempfile

            self.config_dir = os.path.join(tempfile.gettempdir(), "StringFinder")
            os.makedirs(self.config_dir, exist_ok=True)

        self.config_path = os.path.join(self.config_dir, "config.json")

        # [Feature] 설정 파일 버전 관리 (향후 스키마 변경 시 마이그레이션 지원)
        self.CURRENT_CONFIG_VERSION = 1

        self.defaults = {
            "config_version": self.CURRENT_CONFIG_VERSION,  # [NEW] 버전 필드 추가
            "filters": {"folders": [], "extensions": ["xml", "json", "xlsx", "xlsm", "log", "txt"], "filenames": []},
            "history": [],
            "filename_history": [],
            "geometry": None,
            "windowState": None,
            "main_splitter_state": None,
            "result_splitter_state": None,
            "filter_splitter_state": None,
            "theme": "Dark",
            "global_hotkey": "alt+shift+space",
            "enable_profiler": False,
            "run_at_startup": False,
            "close_to_tray": False,
            "case_insensitive": False,
            "result_column_widths": [60, 400, 100, 60],
            "match_column_widths": [60, 400, 400],
            "log_retention": {
                "enabled": False,  # 기본값: 로그 삭제 (기존 동작 유지)
                "max_files": 5,  # 최대 보관 파일 수
                "max_days": 7,  # 최대 보관 일수
            },
            "dock_layout_state": None,
            "lock_dock_layout": False,
            "tabs": [],  # [tab_name, ...] 순서 유지용
        }
        self.sessions_dir = os.path.join(self.config_dir, "sessions")
        os.makedirs(self.sessions_dir, exist_ok=True)
        self._save_lock = threading.Lock()  # 동시 저장 방지를 위한 락
        self._save_timer = None  # 지연 저장을 위한 타이머
        self._save_debounce_time = 0.5  # 0.5초 디바운스
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
        # [Fix] set() 내부에서 이미 save()를 호출하므로 중복 save() 제거
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

                    # [Fix] 로드된 데이터가 dict가 아닌 경우 방어
                    if not isinstance(data, dict):
                        logger.warning("설정 파일 형식이 올바르지 않습니다. 기본값을 사용합니다.")
                        return copy.deepcopy(self.defaults)

                    # [Feature] 버전 확인 및 마이그레이션
                    loaded_version = data.get("config_version", 0)  # 버전 없으면 0 (레거시)

                    if loaded_version < self.CURRENT_CONFIG_VERSION:
                        logger.info(f"설정 파일 마이그레이션: v{loaded_version} -> v{self.CURRENT_CONFIG_VERSION}")
                        data = self._migrate_config(data, loaded_version)
                    elif loaded_version > self.CURRENT_CONFIG_VERSION:
                        logger.warning(
                            f"설정 파일 버전이 현재 앱 버전보다 높습니다 (파일: v{loaded_version}, 앱: v{self.CURRENT_CONFIG_VERSION}). "
                            "일부 설정이 손실될 수 있습니다."
                        )

                    # 기존 설정 파일에 없는 새로운 필드는 기본값에서 보충하여 병합합니다.
                    merged = copy.deepcopy(self.defaults)
                    for k, v in data.items():
                        # 최상위 키들에 대해 타입 일관성 체크 (필요시)
                        if k in merged and v is not None:
                            # 기본값이 리스트인데 데이터가 리스트가 아니면 스킵 등
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
        # v0 (레거시) -> v1 마이그레이션
        if from_version < 1:
            logger.info("레거시 설정 파일을 v1로 마이그레이션합니다.")
            # v1에서는 config_version 필드가 추가됨
            config["config_version"] = 1
            # 향후 필드 변경 사항이 있다면 여기서 처리
            # 예: config["new_field"] = default_value

        # 향후 v1 -> v2 마이그레이션이 필요하면 여기에 추가
        # if from_version < 2:
        #     config = self._migrate_v1_to_v2(config)

        return config

    def save(self):
        """현재 인메모리 설정값들을 로컬 JSON 파일로 예약 저장합니다 (Debounce).
        짧은 시간 내에 여러 번 호출되어도 지연 시간 후에 한 번만 저장됩니다.
        """
        self.cancel_start()  # 기존 타이머 취소

        # 데몬 스레드가 아닌 일반 스레드로 실행하여 종료 시점 정리를 보장받거나,
        # 메인 스레드 종료 시 cancel_start()를 호출해야 함.
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
        # 종료 전 최종 변경 사항이 있다면 즉시 저장 (옵션 -> 강제 적용)
        self.save_immediately()

    def save_immediately(self):
        """지연 없이 즉시 설정을 파일로 직렬화하여 영구 저장합니다.
        Windows 환경에서의 파일 잠금(Lock) 및 액세스 거부 오류를 방지하기 위해 정교한 원자적 쓰기 로직을 포함합니다.
        """
        temp_path = self.config_path + ".tmp"
        old_path = self.config_path + ".old"

        with self._save_lock:
            # 타이머 정리
            if self._save_timer is not None:
                self._save_timer.cancel()
                self._save_timer = None

            for attempt in range(5):  # 최대 5회 재시도
                try:
                    # [Fix] 저장 전 디렉토리가 존재하는지 확인하고 없으면 생성
                    config_dir = os.path.dirname(self.config_path)
                    if not os.path.exists(config_dir):
                        os.makedirs(config_dir, exist_ok=True)

                    # 1. 임시 파일에 쓰기
                    with open(temp_path, "w", encoding="utf-8") as f:
                        json.dump(self.config, f, indent=4, ensure_ascii=False)

                    # 2. 원자적 교체 시도
                    if not os.path.exists(self.config_path):
                        os.rename(temp_path, self.config_path)
                    else:
                        try:
                            # 표준 교체 시도
                            os.replace(temp_path, self.config_path)
                        except (PermissionError, OSError):
                            # Windows에서 WinError 5 (액세스 거부) 발생 시 폴백
                            # 기존 파일을 .old로 바꾸고 새 파일을 밀어 넣은 뒤 이전 파일 삭제
                            if os.path.exists(old_path):
                                os.remove(old_path)

                            os.rename(self.config_path, old_path)
                            os.rename(temp_path, self.config_path)
                            try:
                                os.remove(old_path)
                            except OSError:
                                pass  # 백업 파일 삭제 실패는 치명적이지 않음

                    return  # 성공 시 종료

                except (IOError, OSError) as e:
                    if attempt < 4:
                        logger.debug(AppStrings.LOG_CFG_SAVE_RETRY.format(attempt + 1, e))
                        time.sleep(0.1 * (attempt + 1))  # 지수 백오프
                    else:
                        logger.error(AppStrings.LOG_CFG_SAVE_FAIL.format(e))
                except Exception as e:
                    logger.error(AppStrings.LOG_CFG_SAVE_FAIL.format(e), exc_info=True)
                    break
                finally:
                    # 임시 파일이 여전히 남아있다면 정리
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

        # [Fix] KeyError 및 타입 에러 방어: 리스트가 아니면 초기화
        history = self.config.get("history")
        if not isinstance(history, list):
            history = []
            self.config["history"] = history

        # 중복된 항목이 있으면 제거하여 나중에 맨 앞에 오도록 합니다.
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

        # [Fix] KeyError 및 타입 에러 방어
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
        # [Fix] "filters" 키가 없거나 dict가 아닐 경우 방어
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
        """탭 전역 순서를 저장합니다."""
        self.config["tabs"] = tabs
        self.save()

    def _sanitize_session_name(self, name: str) -> str:
        """세션 파일명으로 사용할 수 없는 문자를 제거합니다.
        
        Args:
            name: 원본 세션 이름
            
        Returns:
            정규화된 세션 이름
        """
        from utils.file_helper import sanitize_filename
        
        # [Fix] 경로 탐색 공격 방지: basename 추출 후 정규화
        # ../../../unsafe_session → unsafe_session
        base_name = os.path.basename(name)
        # Windows 금지 문자 필터링 (?, <, >, |, *, ", :, /, \)
        sanitized = sanitize_filename(base_name)
        return sanitized if sanitized else "default"

    def save_session(self, name, data):
        """개별 탭의 세션 데이터를 파일로 저장합니다."""
        safe_name = self._sanitize_session_name(name)

        file_path = os.path.join(self.sessions_dir, f"{safe_name}.json")
        try:
            # 저장 전 디렉토리 확인 및 생성
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
        import json

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
                    # 파일명에서 확장자 제거하여 세션 이름으로 사용
                    sessions.append(os.path.splitext(filename)[0])
        except Exception as e:
            logger.error(f"세션 목록 조회 실패: {e}")

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

    def clear_all_data(self):
        """데이터베이스 파일(JSON)을 삭제하고 전체 초기화 상태로 되돌립니다."""
        if os.path.exists(self.config_path):
            try:
                os.remove(self.config_path)
            except OSError as e:
                logger.error(f"설정 파일 삭제 실패: {e}")
        self.config = copy.deepcopy(self.defaults)
        self.save()

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
