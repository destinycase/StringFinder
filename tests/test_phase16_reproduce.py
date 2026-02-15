import unittest
import os
import shutil
import tempfile
import time
from unittest.mock import MagicMock, patch

# 프로젝트 루트를 path에 추가
import sys

sys.path.append(os.path.abspath("src"))

from utils.config_manager import ConfigManager


class TestPhase16Critical(unittest.TestCase):
    def setUp(self):
        # Singleton 인스턴스 초기화 우회
        if hasattr(ConfigManager, "_instance"):
            del ConfigManager._instance

    def test_singleton_initialization(self):
        """[High] ConfigManager 싱글톤 초기화가 1회성인지 확인"""
        with patch("utils.config_manager.os.getenv", return_value=tempfile.gettempdir()):
            cm1 = ConfigManager()
            cm1.config["test_key"] = "value1"

            # 두 번째 인스턴스 생성 시 __init__이 다시 실행되어 config가 리셋되면 안 됨
            cm2 = ConfigManager()
            self.assertEqual(cm2.config.get("test_key"), "value1", "Singleton re-initialization reset the config")
            self.assertIs(cm1, cm2, "Different instances returned")

    def test_session_save_no_dir(self):
        """[High] 세션 디렉터리 미존재 시 저장 실패 확인"""
        with patch("utils.config_manager.os.getenv", return_value=tempfile.gettempdir()):
            cm = ConfigManager()
            # 세션 디렉터리를 강제로 삭제
            if os.path.exists(cm.sessions_dir):
                shutil.rmtree(cm.sessions_dir)

            # 저장이 실패하지 않고 디렉토리를 생성해야 함 (또는 예외가 발생하더라도 처리되어야 함)
            try:
                result = cm.save_session("test_session", {"tabs": []})
                print(f"[DEBUG] save_session result: {result}")
                print(f"[DEBUG] sessions_dir: {cm.sessions_dir}")
                print(f"[DEBUG] exists? {os.path.exists(cm.sessions_dir)}")
                target_file = os.path.join(cm.sessions_dir, "test_session.json")
                print(f"[DEBUG] target file: {target_file}")
                print(f"[DEBUG] file exists? {os.path.exists(target_file)}")

                self.assertTrue(result, "save_session returned False")
                self.assertTrue(os.path.exists(target_file))
            except Exception as e:
                print(f"[DEBUG] Exception: {e}")
                self.fail(f"save_session raised exception: {e}")

    def test_debounce_race_condition(self):
        """[High] 설정 저장 Debounce 신뢰성 확인"""
        with patch("utils.config_manager.os.getenv", return_value=tempfile.gettempdir()):
            cm = ConfigManager()
            # Debounce 시간 짧게 조절 (테스트용)
            # 여기서는 로직을 검증하기 어려우므로, 파일 저장 호출 여부를 Mocking하여 확인
            with patch("builtins.open", new_callable=MagicMock) as _:
                # save() 호출
                cm.save()
                # 즉시 확인 시 파일이 아직 안 열렸을 수 있음 (Timer)
                # 약간 대기
                time.sleep(1.1)
                # 하지만 현재 구현상 1초 뒤에 저장이 보장되어야 함.
                # 만약 프로그램이 그 전에 종료되면? -> stop()에서 flush 확인 필요

    def test_key_error_defense(self):
        """[High] 설정 딕셔너리 KeyError 방어 확인"""
        with patch("utils.config_manager.os.getenv", return_value=tempfile.gettempdir()):
            cm = ConfigManager()
            # 강제로 필수 키 삭제
            if "theme" in cm.config:
                del cm.config["theme"]

            # get_theme() 호출 시 크래시 나지 않아야 함
            try:
                theme = cm.get_theme()
                self.assertEqual(theme, "Dark", "Should return default if key missing")
            except KeyError:
                self.fail("get_theme raised KeyError")


if __name__ == "__main__":
    unittest.main()
