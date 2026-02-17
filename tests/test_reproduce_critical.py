import unittest
import os
import shutil
import tempfile
from unittest.mock import patch

# sys.path 설정
import sys

sys.path.append(os.path.abspath("src"))

from utils.config_manager import ConfigManager
from utils.app_strings import AppStrings


class TestCriticalIssues(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        # ConfigManager Singleton 해제 (테스트 격리를 위해)
        if hasattr(ConfigManager, "_instance"):
            del ConfigManager._instance

        # Mock Config Path
        self.patcher = patch("utils.config_manager.os.getenv", return_value=self.test_dir)
        self.patcher.start()

        self.config = ConfigManager()
        # 강제로 경로 재설정 (mock이 __init__ 호출 시점에 적용되도록)
        self.config.config_dir = os.path.join(self.test_dir, "StringFinder")
        self.config.config_path = os.path.join(self.config.config_dir, "config.json")
        self.config.sessions_dir = os.path.join(self.config.config_dir, "sessions")
        os.makedirs(self.config.sessions_dir, exist_ok=True)

    def tearDown(self):
        self.patcher.stop()
        if hasattr(self.config, "_save_timer") and self.config._save_timer:
            self.config._save_timer.cancel()
        shutil.rmtree(self.test_dir)

    def test_config_manager_singleton(self):
        """[High] ConfigManager가 싱글톤으로 동작하지 않아 종료 시 타이머 제어 실패"""
        cfg1 = ConfigManager()
        cfg2 = ConfigManager()
        self.assertIs(cfg1, cfg2, "ConfigManager must be a Singleton")

    def test_save_immediately_missing_dir(self):
        """[High] 설정 디렉토리가 없을 때 save_immediately 실패 확인"""
        shutil.rmtree(self.config.config_dir)
        try:
            self.config.save_immediately()
        except FileNotFoundError:
            self.fail("save_immediately raised FileNotFoundError when directory is missing")

    def test_session_sanitization_consistency(self):
        """[High] 세션 로드/삭제 시에도 파일명 소독 적용 여부"""
        unsafe_name = "../../../unsafe_session"
        safe_name = "unsafe_session"

        # 1. 저장 (이미 소독 로직 있음)
        self.config.save_session(unsafe_name, {"data": 1})
        expected_path = os.path.join(self.config.sessions_dir, f"{safe_name}.json")
        self.assertTrue(os.path.exists(expected_path))

        # 2. 로드 (소독 적용 확인)
        loaded = self.config.load_session(unsafe_name)
        self.assertIsNotNone(loaded, "Failed to load session with unsafe name")

        # 3. 삭제 (소독 적용 확인)
        self.config.delete_session(unsafe_name)
        self.assertFalse(os.path.exists(expected_path), "Failed to delete session with unsafe name")

    def test_app_strings_completeness(self):
        """[High] AppStrings.APP_NAME 누락 확인"""
        self.assertTrue(hasattr(AppStrings, "APP_NAME"), "AppStrings.APP_NAME is missing")


if __name__ == "__main__":
    unittest.main()
