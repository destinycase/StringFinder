import unittest
import os
import shutil
import tempfile
from unittest.mock import patch
import sys

# Ensure src is in path
sys.path.append(os.path.abspath("src"))

from sf_utils.config_manager import ConfigManager
from sf_utils.app_strings import AppStrings
from core.system_manager import SystemManager


class TestRegressions(unittest.TestCase):
    """과거 발견되었던 주요 버그 및 이슈 재발 방지 테스트 통합"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        if hasattr(ConfigManager, "_instance"):
            del ConfigManager._instance

        self.patcher = patch("sf_utils.config_manager.os.getenv", return_value=self.test_dir)
        self.patcher.start()

        self.config = ConfigManager()
        self.config.config_dir = os.path.join(self.test_dir, "StringFinder")
        os.makedirs(self.config.config_dir, exist_ok=True)
        self.config.sessions_dir = os.path.join(self.config.config_dir, "sessions")
        os.makedirs(self.config.sessions_dir, exist_ok=True)

    def tearDown(self):
        self.patcher.stop()
        if hasattr(self.config, "_save_timer") and self.config._save_timer:
            self.config._save_timer.cancel()
        shutil.rmtree(self.test_dir)

    def test_singleton_integrity(self):
        """ConfigManager 싱글톤 보장 확인"""
        cfg1 = ConfigManager()
        cfg2 = ConfigManager()
        self.assertIs(cfg1, cfg2)

    def test_app_strings_completeness(self):
        """필수 UI 상수 존재 여부 확인"""
        self.assertTrue(hasattr(AppStrings, "APP_TITLE"))
        self.assertTrue(hasattr(AppStrings, "DOCK_SEARCH_TITLE"))

    def test_config_save_no_dir(self):
        """디렉토리가 없는 상태에서 저장 시 크래시 방지"""
        shutil.rmtree(self.config.config_dir)
        try:
            self.config.save_immediately()
        except Exception as e:
            self.fail(f"save_immediately failed when dir missing: {e}")

    def test_session_name_security(self):
        """세션 파일명 경로 유동(Path Traversal) 공격 방어 확인"""
        unsafe_name = "../../../hacker"
        safe_name = "hacker"
        self.config.save_session(unsafe_name, {"test": 1})
        expected = os.path.join(self.config.sessions_dir, f"{safe_name}.json")
        self.assertTrue(os.path.exists(expected))

    def test_system_manager_safe_init(self):
        """SystemManager 인스턴스화 및 기본 호출 안정성"""
        sm = SystemManager()
        # Windows API 호출 시 실패하지 않는지 여부
        try:
            sm.is_run_at_startup()
        except Exception as e:
            self.fail(f"SystemManager encountered error: {e}")

    def test_config_copy_isolation(self):
        """clear_all_data 등이 원본 참조를 깨고 독립적인 복사본을 사용하는지 확인"""
        original_defaults = self.config.defaults
        self.config.clear_all_data()
        self.assertNotEqual(id(self.config.config), id(original_defaults))

    def test_quick_search_case_insensitivity(self):
        """[상] 대소문자 무감각 검색 무결성 결함 리그레션 테스트 (양방향 보강)"""
        from core.search_engine import _quick_search_bytes, search_in_file

        # 1. 파일=소문자, 검색어=대문자 케이스
        test_file_lower = os.path.join(self.test_dir, "case_test_lower.txt")
        with open(test_file_lower, "w", encoding="utf-8") as f:
            f.write("hello world")

        self.assertTrue(_quick_search_bytes(test_file_lower, "HELLO"))
        # 상위 레벨 함수에서도 확인 (None이 아니어야 함)
        self.assertIsNotNone(search_in_file(test_file_lower, "HELLO"))

        # 2. 파일=대문자, 검색어=소문자 케이스 (Phase 2에서 지적된 맹점)
        test_file_upper = os.path.join(self.test_dir, "case_test_upper.txt")
        with open(test_file_upper, "w", encoding="utf-8") as f:
            f.write("HELLO WORLD")

        self.assertTrue(_quick_search_bytes(test_file_upper, "hello"))
        self.assertIsNotNone(search_in_file(test_file_upper, "hello"))

        # 3. 혼합 대소문자 케이스 (Phase 3에서 지적된 맹점: hElLo vs hello)
        test_file_mixed = os.path.join(self.test_dir, "case_test_mixed.txt")
        with open(test_file_mixed, "w", encoding="utf-8") as f:
            f.write("hElLo WoRlD")

        # _quick_search_bytes는 바이트 기반이라 hElLo를 놓칠 수 있음 (False를 반환할 수 있음)
        # 하지만 search_in_file은 이를 인지하고 본 검색으로 넘어가야 하므로 0보다 큰 결과가 나와야 함
        res = search_in_file(test_file_mixed, "hello")
        assert res is not None
        self.assertGreater(res[1], 0)  # type: ignore

        self.assertFalse(_quick_search_bytes(test_file_lower, "MISSING"))


if __name__ == "__main__":
    unittest.main()
