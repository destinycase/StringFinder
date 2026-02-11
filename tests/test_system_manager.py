from unittest.mock import patch
from core.system_manager import SystemManager


def test_startup_manager_register():
    """시작 프로그램 등록 로직 테스트 (Mock)"""
    with patch("winreg.OpenKey"):
        with patch("winreg.SetValueEx") as mock_set:
            with patch("winreg.CloseKey"):
                sm = SystemManager(app_name="TestApp")
                sm.exe_path = "C:\\test.exe"

                # 등록 시도
                result = sm.set_run_at_startup(True)

                assert result is True
                mock_set.assert_called_once()


def test_startup_manager_unregister():
    """시작 프로그램 해제 로직 테스트 (Mock)"""
    with patch("winreg.OpenKey") as mock_open:
        with patch("winreg.DeleteValue") as mock_del:
            with patch("winreg.CloseKey"):
                sm = SystemManager(app_name="TestApp")

                # 해제 시도
                result = sm.set_run_at_startup(False)

                assert result is True
                # mock_open.return_value가 key로 전달됨
                mock_del.assert_called_once_with(mock_open.return_value, "TestApp")


def test_is_run_at_startup():
    """시작 프로그램 등록 여부 확인 테스트 (Mock)"""
    with patch("winreg.OpenKey"):
        with patch("winreg.QueryValueEx") as mock_query:
            with patch("winreg.CloseKey"):
                sm = SystemManager(app_name="TestApp")

                # 1. 등록되어 있는 경우
                mock_query.return_value = ("path", 1)
                assert sm.is_run_at_startup() is True

                # 2. 등록되어 있지 않은 경우 (FileNotFoundError)
                mock_query.side_effect = FileNotFoundError()
                assert sm.is_run_at_startup() is False


def test_hotkey_manager_registration():
    """전역 단축키 등록/해제 테스트 (Mock)"""
    with patch("keyboard.add_hotkey") as mock_add:
        with patch("keyboard.unhook_all") as mock_unhook:
            sm = SystemManager()

            # 등록
            sm.register_hotkey("alt+shift+k")
            assert sm.current_hotkey == "alt+shift+k"
            mock_add.assert_called_once()

            # 해제
            sm.unregister_hotkey()
            assert sm.current_hotkey is None
            mock_unhook.assert_called_once()
