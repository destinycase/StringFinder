"""
[test_system_manager.py]

이 테스트는 윈도우 시스템 연동 기능(시작 프로그램, 핫키 등)을 담당하는 `SystemManager`를 검증합니다.

- 테스트 목적:
  1. 레지스트리 조작 및 키보드 훅(Hook) 등 운영체제 수준의 상호작용 정확성 확인.

- 주요 검증 사항:
  1. 윈도우 레지스트리(Winreg)를 통한 시작 프로그램 등록 및 해제 동작.
  2. 전역 핫키(Hotkey)의 안전한 등록 및 등록 취소 시퀀스.
"""

from unittest.mock import patch

from core.system_manager import SystemManager


def test_startup_manager_register():
    """시작 프로그램 등록 로직을 모킹으로 검증한다."""
    with patch("winreg.OpenKey"):
        with patch("winreg.SetValueEx") as mock_set:
            with patch("winreg.CloseKey"):
                sm = SystemManager(app_name="TestApp")
                sm.exe_path = "C:\\test.exe"

                # 등록 호출
                result = sm.set_run_at_startup(True)

                assert result is True
                mock_set.assert_called_once()


def test_startup_manager_unregister():
    """시작 프로그램 해제 로직을 모킹으로 검증한다."""
    with patch("winreg.OpenKey") as mock_open:
        with patch("winreg.DeleteValue") as mock_del:
            with patch("winreg.CloseKey"):
                sm = SystemManager(app_name="TestApp")

                # 해제 호출
                result = sm.set_run_at_startup(False)

                assert result is True
                mock_del.assert_called_once_with(mock_open.return_value, "TestApp")


def test_is_run_at_startup():
    """시작 프로그램 등록 여부 확인 로직을 모킹으로 검증한다."""
    with patch("winreg.OpenKey"):
        with patch("winreg.QueryValueEx") as mock_query:
            with patch("winreg.CloseKey"):
                sm = SystemManager(app_name="TestApp")

                # 1. 등록된 경우
                mock_query.return_value = ("path", 1)
                assert sm.is_run_at_startup() is True

                mock_query.side_effect = FileNotFoundError()
                assert sm.is_run_at_startup() is False


def test_hotkey_manager_registration():
    """전역 핫키 등록/해제 동작을 모킹으로 검증한다."""
    with patch("keyboard.add_hotkey") as mock_add:
        with patch("keyboard.remove_hotkey") as mock_remove:
            sm = SystemManager()

            # 등록
            sm.register_hotkey("alt+shift+k")
            assert sm.current_hotkey == "alt+shift+k"
            mock_add.assert_called_once()

            # 해제
            sm.unregister_hotkey()
            assert sm.current_hotkey is None
            mock_remove.assert_called_once_with("alt+shift+k")
