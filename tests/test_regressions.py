"""
[test_regressions.py]

이 테스트는 프로젝트 과거 버전에서 발견되었던 주요 결함 및 사용자 보고 이슈들의 재발을 방지하기 위한 리그레션 테스트를 통합 관리합니다.

- 테스트 목적:
  1. 싱글톤 패턴 파괴, 설정 파일 유실 등 치명적인 결함의 재발 방지.
  2. 대소문자 무감각 검색 무결성 등 과거 수정된 핵심 로직의 안정성 지속 검증.

- 주요 검증 사항:
  1. `ConfigManager`의 싱글톤 인스턴스 유지 무결성.
  2. 설정 디렉토리 부재 상황에서의 자동 생성 및 안전한 저장.
  3. 세션 이름 내 예약어 및 경로 공격 방어 확인.
  4. 혼합 대소문자 검색의 정확한 매칭 유지 확인.
"""

import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(os.path.abspath("src"))

from core.system_manager import SystemManager
from sf_utils.app_strings import AppStrings
from sf_utils.config_manager import ConfigManager


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
        """test_singleton_integrity 함수."""
        cfg1 = ConfigManager()
        cfg2 = ConfigManager()
        self.assertIs(cfg1, cfg2)

    def test_app_strings_completeness(self):
        """test_app_strings_completeness 함수."""
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
        """test_session_name_security 함수."""
        unsafe_name = "../../../hacker"
        safe_name = "hacker"
        self.config.save_session(unsafe_name, {"test": 1})
        expected = os.path.join(self.config.sessions_dir, f"{safe_name}.json")
        self.assertTrue(os.path.exists(expected))

    def test_system_manager_safe_init(self):
        """test_system_manager_safe_init 함수."""
        sm = SystemManager()
        try:
            sm.is_run_at_startup()
        except Exception as e:
            self.fail(f"SystemManager encountered error: {e}")

    def test_config_copy_isolation(self):
        """test_config_copy_isolation 함수."""
        original_defaults = self.config.defaults
        self.config.clear_all_data()
        self.assertNotEqual(id(self.config.config), id(original_defaults))

    def test_case_insensitivity_regression(self):
        """[상] 대소문자 무감각 검색 무결성 결함 리그레션 테스트"""
        from core.search_engine import search_in_file

        # 1. 파일=소문자, 검색어=대문자 케이스
        test_file_lower = os.path.join(self.test_dir, "case_test_lower.txt")
        with open(test_file_lower, "w", encoding="utf-8") as f:
            f.write("hello world")

        self.assertIsNotNone(search_in_file(test_file_lower, "HELLO"))

        # 2. 파일=대문자, 검색어=소문자 케이스
        test_file_upper = os.path.join(self.test_dir, "case_test_upper.txt")
        with open(test_file_upper, "w", encoding="utf-8") as f:
            f.write("HELLO WORLD")

        self.assertIsNotNone(search_in_file(test_file_upper, "hello"))

        # 3. 혼합 대소문자 케이스
        test_file_mixed = os.path.join(self.test_dir, "case_test_mixed.txt")
        with open(test_file_mixed, "w", encoding="utf-8") as f:
            f.write("hElLo WoRlD")

        res = search_in_file(test_file_mixed, "hello")
        self.assertIsNotNone(res)
        self.assertGreater(res[1], 0)  # type: ignore


if __name__ == "__main__":
    unittest.main()
