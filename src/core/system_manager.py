import os
import sys
import winreg
import keyboard
from PySide6.QtCore import QObject, Signal
from utils.logger import logger
from utils.app_strings import AppStrings


class SystemManager(QObject):
    """
    운영체제 수준의 설정을 관리하는 클래스입니다.
    - 윈도우 시작 프로그램 등록/확인 (Registry)
    - 전역 단축키 등록 및 입력 감지 (keyboard library)
    """

    hotkey_pressed = Signal()

    def __init__(self, app_name="StringFinder"):
        """
        시스템 관리자를 초기화합니다.

        Args:
            app_name (str): 레지스트리 등에 사용될 애플리케이션 식별자
        """
        super().__init__()
        self.app_name = app_name
        self.exe_path = os.path.abspath(sys.argv[0])
        self.current_hotkey = None

    # --- 시작 프로그램 관리 (Windows 전용) ---
    def set_run_at_startup(self, enable=True):
        """
        윈도우 부팅 시 프로그램이 자동으로 시작되도록 등록하거나 해제합니다.
        윈도우 레지스트리의 'Run' 키를 조작합니다.

        Args:
            enable (bool): 등록 여부

        Returns:
            bool: 성공 여부
        """
        if os.name != "nt":  # 윈도우가 아니면 리턴
            return False

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
            if enable:
                # 현재 실행 중인 파일 경로(exe)를 등록
                winreg.SetValueEx(key, self.app_name, 0, winreg.REG_SZ, f'"{self.exe_path}"')
            else:
                # 등록된 값 삭제
                try:
                    winreg.DeleteValue(key, self.app_name)
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
            return True
        except PermissionError as e:
            # 권한 부족 시 명확한 메시지 로깅
            logger.error(f"레지스트리 접근 권한이 부족합니다. 관리자 권한으로 실행해 주세요: {e}")
            return False
        except FileNotFoundError as e:
            # 레지스트리 키 미존재
            logger.warning(f"레지스트리 키를 찾을 수 없습니다: {e}")
            return False
        except (OSError, Exception) as e:
            logger.error(AppStrings.LOG_REGISTRY_ERROR.format(str(e)))
            return False

    def is_run_at_startup(self):
        """시작 프로그램 등록 여부 확인"""
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ)
            winreg.QueryValueEx(key, self.app_name)
            winreg.CloseKey(key)
            return True
        except FileNotFoundError:
            return False
        except Exception:
            return False

    # --- 전역 단축키 관리 ---
    def register_hotkey(self, hotkey_str):
        """
        시스템 전역 단축키를 등록합니다. 다른 앱보다 우선적으로 입력을 가로앱니다.

        Args:
            hotkey_str (str): 등록할 단축키 문자열 (예: 'alt+shift+f')

        Returns:
            bool: 성공 여부
        """
        if not hotkey_str:
            return False

        try:
            # 이전에 등록된 단축키가 있다면 모두 해제하여 충돌 방지
            if self.current_hotkey:
                keyboard.unhook_all()

            self.current_hotkey = hotkey_str
            # low-level hook을 통해 단축키 감지 루틴 연결
            keyboard.add_hotkey(hotkey_str, self._on_hotkey)
            logger.info(AppStrings.LOG_HOTKEY_REGISTERED.format(hotkey_str))
            return True
        except Exception as e:
            logger.error(AppStrings.LOG_HOTKEY_ERROR.format(str(e)))
            return False

    def unregister_hotkey(self):
        """등록된 전역 단축키를 해제하고 자원을 반납합니다."""
        if self.current_hotkey:
            keyboard.unhook_all()
            self.current_hotkey = None

    def _on_hotkey(self):
        """단축키 입력 시 시그널 발생"""
        self.hotkey_pressed.emit()
