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
        self.hotkey_registered = False  # 단축키 등록 상태 추적

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
                    pass  # 이미 삭제됨
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
            logger.error(AppStrings.LOG_SYS_REGISTRY_ERROR.format(str(e)))
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
        except Exception as e:
            logger.debug(f"시작 프로그램 확인 중 오류 (무시됨): {e}")
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
            # 이전에 등록된 동일한 단축키가 있다면 제거하여 충돌 방지
            if self.current_hotkey:
                try:
                    keyboard.remove_hotkey(self.current_hotkey)
                except Exception as e:
                    logger.debug(f"기존 단축키 제거 실패 (무시됨): {e}")

            self.current_hotkey = hotkey_str
            # low-level hook을 통해 단축키 감지 루틴 연결
            keyboard.add_hotkey(hotkey_str, self._on_hotkey)
            self.hotkey_registered = True  # 등록 성공
            return True
        except Exception as e:
            self.hotkey_registered = False  # 등록 실패
            self.current_hotkey = None  # 실패 시 초기화
            logger.error(AppStrings.LOG_SYS_HOTKEY_ERROR.format(str(e)))
            return False

    def unregister_hotkey(self):
        """등록된 전역 단축키를 해제하고 자원을 반납합니다."""
        if self.current_hotkey:
            try:
                keyboard.remove_hotkey(self.current_hotkey)
            except Exception as e:
                logger.debug(f"단축키 해제 실패 (무시됨): {e}")
            self.current_hotkey = None
            self.hotkey_registered = False

    def is_hotkey_active(self):
        """단축키 활성 상태 확인

        Returns:
            bool: 단축키가 성공적으로 등록되어 활성 상태인지 여부
        """
        return self.hotkey_registered

    def _on_hotkey(self):
        """단축키 입력 시 시그널 발생"""
        self.hotkey_pressed.emit()

    # --- 로그 관리 ---
    def cleanup_logs(self, log_dir, retention_config):
        """
        오래된 로그 파일을 정리합니다.

        Args:
            log_dir (str): 로그 파일이 저장된 디렉토리 경로
            retention_config (dict): 로그 보관 설정 (enabled, max_files, max_days)
        """
        import glob
        import time

        try:
            if not os.path.exists(log_dir):
                return

            log_pattern = os.path.join(log_dir, "stringfinder_*.log")
            all_log_files = glob.glob(log_pattern)

            # 파일이 없으면 종료
            if not all_log_files:
                return

            # [Fix] retention이 비활성화된 경우 오래된 로그를 삭제해야 함
            # 기존: 10개 유지 → 수정: 현재 세션 로그만 유지하고 나머지 삭제
            if not retention_config.get("enabled", False):
                # 보존 비활성화: 현재 세션 로그만 유지, 나머지 모두 삭제
                max_files = 1  # 현재 로그만 유지
                max_days = 0  # 즉시 삭제
            else:
                max_files = retention_config.get("max_files", 5)
                max_days = retention_config.get("max_days", 7)

            # 최신순 정렬 (수정 시간 기준)
            log_files = sorted(all_log_files, key=os.path.getmtime, reverse=True)

            cutoff_time = time.time() - (max_days * 86400)

            # 현재 실행 중인 로그 파일 식별 (삭제 방지)
            current_log_file = None
            import logging

            root_logger = logging.getLogger("StringFinder")
            for h in root_logger.handlers:
                if isinstance(h, logging.FileHandler):
                    current_log_file = os.path.abspath(h.baseFilename)
                    break

            deleted_count = 0
            for i, log_file in enumerate(log_files):
                try:
                    # 현재 세션 로그는 절대 삭제 안 함
                    if current_log_file and os.path.abspath(log_file) == current_log_file:
                        continue

                    # 1. 파일 개수 초과 확인
                    # 2. 보관 기간 초과 확인
                    if i >= max_files or os.path.getmtime(log_file) < cutoff_time:
                        os.remove(log_file)
                        deleted_count += 1
                except (OSError, FileNotFoundError) as e:
                    logger.debug(f"Log cleanup skip for {log_file}: {e}")

            if deleted_count > 0:
                logger.info(AppStrings.LOG_SYS_LOG_CLEANUP_DONE.format(deleted_count))

        except Exception as e:
            logger.error(AppStrings.LOG_RES_LOG_CLEANUP_FAIL.format(e))
