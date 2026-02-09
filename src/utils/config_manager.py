import json
import os
from utils.logger import logger


class ConfigManager:
    """
    히스토리 및 설정을 관리하는 클래스
    """

    def __init__(self):
        # AppData/StringFinder 경로 설정
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
        }
        self.config = self._load()

    def get_case_insensitive(self):
        return self.config.get("case_insensitive", False)

    def set_case_insensitive(self, value):
        self.config["case_insensitive"] = value
        self.save()

    def _load(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    # 기본값과 병합 (새 필드 추가 대응)
                    merged = self.defaults.copy()
                    merged.update(data)
                    return merged
            except json.JSONDecodeError as e:
                logger.warning(f"Config file corrupted, using defaults: {e}")
            except (IOError, OSError) as e:
                logger.error(f"Error reading config file: {e}")
        return self.defaults.copy()

    def save(self):
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
        except (IOError, OSError) as e:
            logger.error(f"Config save error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error saving config: {e}", exc_info=True)

    def add_history(self, search_text):
        if search_text and search_text not in self.config["history"]:
            self.config["history"].insert(0, search_text)
            self.config["history"] = self.config["history"][:20]  # 최대 20개
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
        """저장된 모든 설정 및 히스토리 삭제"""
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
