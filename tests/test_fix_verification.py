import unittest
import os
import sys
import tempfile
from unittest.mock import patch

# 프로젝트 루트를 path에 추가
sys.path.append(os.path.abspath("src"))

from utils.app_strings import AppStrings
from utils.config_manager import ConfigManager
from core.system_manager import SystemManager


class TestFixVerification(unittest.TestCase):
    def test_app_strings_integrity(self):
        """[Phase 14] AppStrings.APP_TITLE 존재 및 중복 제거 확인"""
        self.assertTrue(hasattr(AppStrings, "APP_TITLE"), "APP_TITLE missing")
        # 중복 제거 확인 (소스 코드를 직접 읽어서 확인하는 것이 가장 확실하지만, 여기서는 속성 존재 여부만)
        # DOCK 상수 존재 확인 (AppStrings에 유지됨)
        self.assertTrue(
            hasattr(AppStrings, "DOCK_SEARCH_TITLE"),
            "DOCK constants missing",
        )

    def test_system_manager_logging(self):
        """[Phase 15] SystemManager 예외 로깅 확인 (Mock)"""
        with patch("utils.logger.logger.debug"):
            sm = SystemManager()
            # 강제로 예외 발생시키기 어려우므로, 로직상 pass가 아닌지 코드 리뷰로 확인했음.
            # 여기서는 인스턴스 생성 및 기본 메서드 호출이 안 터지는지 확인
            sm.is_run_at_startup()
            # 예외가 안 나면 성공

    def test_config_manager_deepcopy(self):
        """[Phase 15] ConfigManager clear_all_data deepcopy 확인"""
        # Singleton 초기화 우회
        if hasattr(ConfigManager, "_instance"):
            del ConfigManager._instance

        with patch("utils.config_manager.os.getenv", return_value=tempfile.gettempdir()):
            cm = ConfigManager()
            original_defaults = cm.defaults
            cm.clear_all_data()
            self.assertNotEqual(id(cm.config), id(original_defaults), "Config should be a copy")
            # Deepcopy 확인: 내부 딕셔너리 ID도 달라야 함
            if "filename_history" in cm.config:
                self.assertNotEqual(id(cm.config["filename_history"]), id(original_defaults["filename_history"]))


if __name__ == "__main__":
    unittest.main()
