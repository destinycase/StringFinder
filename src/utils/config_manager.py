import json
import os
from utils.logger import logger


class ConfigManager:
    """
    히스토리 및 설정을 관리하는 클래스
    """

    def __init__(self):
        """사용자 로컬 AppData 내에 설정 저장소를 확보하고 기본값들을 준비합니다."""
        # Windows 기준 AppData/StringFinder 경로를 사용합니다.
        app_data = os.getenv("APPDATA")
        self.config_dir = os.path.join(app_data, "StringFinder")
        if not os.path.exists(self.config_dir):
            os.makedirs(self.config_dir)

        self.config_path = os.path.join(self.config_dir, "config.json")
        self.defaults = {
            "filters": {"folders": [], "extensions": ["xml", "json", "xlsx", "xlsm", "log", "txt"]},
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
        }
        self.config = self._load()

    def get_column_widths(self, table_name):
        """저장된 테이블 컬럼 너비를 반환합니다."""
        if table_name == "result":
            return self.config.get("result_column_widths", [100, 400, 200])
        elif table_name == "match":
            return self.config.get("match_column_widths", [60, 400, 400])
        return None

    def set_column_widths(self, table_name, widths):
        """테이블 컬럼 너비를 저장합니다."""
        if table_name == "result":
            self.config["result_column_widths"] = widths
        elif table_name == "match":
            self.config["match_column_widths"] = widths
        self.save()

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
                    # 기존 설정 파일에 없는 새로운 필드는 기본값에서 보충하여 병합합니다.
                    merged = self.defaults.copy()
                    merged.update(data)
                    return merged
            except json.JSONDecodeError as e:
                logger.warning(f"Config file corrupted, using defaults: {e}")
            except (IOError, OSError) as e:
                logger.error(f"Error reading config file: {e}")
        return self.defaults.copy()

    def save(self):
        """현재 인메모리 설정값들을 로컬 JSON 파일로 직렬화하여 영구 저장합니다."""
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
        except (IOError, OSError) as e:
            logger.error(f"Config save error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error saving config: {e}", exc_info=True)

    def add_history(self, search_text):
        """새로운 검색어를 기록 상단에 추가하며 최대 유지 개수(20개)를 넘지 않도록 관리합니다."""
        if search_text and search_text not in self.config["history"]:
            self.config["history"].insert(0, search_text)
            self.config["history"] = self.config["history"][:20]
            self.save()

    def remove_history_item(self, text):
        if text in self.config["history"]:
            self.config["history"].remove(text)
            self.save()

    def clear_history(self):
        self.config["history"] = []
        self.save()

    def get_history(self):
        return self.config.get("history", [])

    def add_filename_history(self, filename):
        if filename and filename not in self.config["filename_history"]:
            self.config["filename_history"].insert(0, filename)
            self.config["filename_history"] = self.config["filename_history"][:20]
            self.save()

    def remove_filename_history_item(self, text):
        if text in self.config["filename_history"]:
            self.config["filename_history"].remove(text)
            self.save()

    def clear_filename_history(self):
        self.config["filename_history"] = []
        self.save()

    def get_filename_history(self):
        return self.config.get("filename_history", [])

    def update_filters(self, folders, extensions):
        self.config["filters"]["folders"] = folders
        self.config["filters"]["extensions"] = extensions
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
            os.remove(self.config_path)
        self.config = self.defaults.copy()
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
