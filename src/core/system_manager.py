import os
import sys
import winreg

import keyboard
from PySide6.QtCore import QObject, Signal

from sf_utils.app_strings import AppStrings
from sf_utils.logger import logger


class SystemManager(QObject):
    """SystemManager 클래스."""

    hotkey_pressed = Signal()

    def __init__(self, app_name="StringFinder"):
        """__init__ 함수."""
        super().__init__()
        self.app_name = app_name
        self.exe_path = os.path.abspath(sys.argv[0])
        self.current_hotkey = None
        self.hotkey_registered = False

    def set_run_at_startup(self, enable=True):
        """set_run_at_startup 함수."""
        if os.name != "nt":
            return False
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
            if enable:
                winreg.SetValueEx(key, self.app_name, 0, winreg.REG_SZ, f'"{self.exe_path}"')
            else:
                try:
                    winreg.DeleteValue(key, self.app_name)
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
            return True
        except PermissionError as e:
            logger.error(AppStrings.LOG_SYS_REGISTRY_ERROR.format(f"Permission denied: {e}"))
            return False
        except FileNotFoundError as e:
            logger.warning(AppStrings.LOG_SYS_REGISTRY_ERROR.format(f"Key not found: {e}"))
            return False
        except (OSError, Exception) as e:
            logger.error(AppStrings.LOG_SYS_REGISTRY_ERROR.format(str(e)))
            return False

    def is_run_at_startup(self):
        """시작 프로그램 등록 여부 확인"""
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        key = None
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ)
            winreg.QueryValueEx(key, self.app_name)
            return True
        except FileNotFoundError:
            return False
        except Exception as e:
            logger.debug(AppStrings.LOG_SYS_DATA_DIR_CHECK_FAIL.format(e))
            return False
        finally:
            # 레지스트리 키는 예외 발생 여부와 무관하게 항상 닫아야 함.
            if key is not None:
                winreg.CloseKey(key)

    def register_hotkey(self, hotkey_str):
        """register_hotkey 함수."""
        if not hotkey_str:
            return False
        try:
            if self.current_hotkey:
                try:
                    keyboard.remove_hotkey(self.current_hotkey)
                except Exception as e:
                    logger.debug(AppStrings.LOG_SYS_HOTKEY_REMOVE_FAIL.format(e))
            self.current_hotkey = hotkey_str
            keyboard.add_hotkey(hotkey_str, self._on_hotkey)
            self.hotkey_registered = True
            return True
        except Exception as e:
            self.hotkey_registered = False
            self.current_hotkey = None
            logger.error(AppStrings.LOG_SYS_HOTKEY_ERROR.format(str(e)))
            return False

    def unregister_hotkey(self):
        """등록된 전역 단축키를 해제하고 자원을 반납합니다."""
        if self.current_hotkey:
            try:
                keyboard.remove_hotkey(self.current_hotkey)
                # unhook_all() 은 타 프로그램 훅까지 제거하는 부작용이 있음.
                # unhook_all_hotkeys() 로 StringFinder 가 등록한 hotkey 훅만 해제.
                keyboard.unhook_all_hotkeys()
            except Exception as e:
                logger.debug(AppStrings.LOG_SYS_HOTKEY_REMOVE_FAIL.format(e))
            self.current_hotkey = None
            self.hotkey_registered = False

    @staticmethod
    def force_cleanup():
        """모든 전역 키보드 후킹을 강제 해제합니다. (테스트/긴급용)"""
        try:
            keyboard.unhook_all()
        except Exception as e:
            logger.debug(AppStrings.LOG_SYS_LOG_CLEANUP_FAILURE.format(e))

    def is_hotkey_active(self):
        """is_hotkey_active 함수."""
        return self.hotkey_registered

    def _on_hotkey(self):
        """단축키 입력 시 시그널 발생"""
        self.hotkey_pressed.emit()

    def cleanup_logs(self, log_dir, retention_config):
        """cleanup_logs 함수."""
        import glob
        import time

        try:
            if not os.path.exists(log_dir):
                return
            log_pattern = os.path.join(log_dir, "stringfinder_*.log")
            all_log_files = glob.glob(log_pattern)
            if not all_log_files:
                return
            if not retention_config.get("enabled", False):
                max_files = 1
                max_days = 0
            else:
                max_files = retention_config.get("max_files", 5)
                max_days = retention_config.get("max_days", 7)
            # [일관성] 정렬 엔진과 삭제 기준을 모두 수정일(mtime)로 통일하여 보관 정책의 예측성을 높입니다.
            log_files = sorted(all_log_files, key=os.path.getmtime, reverse=True)
            cutoff_time = time.time() - (max_days * 86400)
            current_log_file = None
            import logging

            root_logger = logging.getLogger("StringFinder")
            for h in root_logger.handlers:
                if isinstance(h, logging.FileHandler):
                    current_log_file = os.path.abspath(h.baseFilename)
                    break
            deleted_count = 0
            kept_count = 0
            for log_file in log_files:
                try:
                    if current_log_file and os.path.abspath(log_file) == current_log_file:
                        continue
                    # i >= max_files 대신 실제 보관 처리된(kept_count) 파일 수를 기준으로 삭제 판정
                    # 이전 방식(i)은 현재 로그 파일을 건너뛰어도 인덱스가 증가하여 실제보다 1개 적게 보관함
                    if kept_count >= max_files or os.path.getmtime(log_file) < cutoff_time:
                        os.remove(log_file)
                        deleted_count += 1
                    else:
                        kept_count += 1
                except (OSError, FileNotFoundError) as e:
                    logger.debug(AppStrings.LOG_SYS_CLEANUP_SKIP.format(log_file, e))
            if deleted_count > 0:
                logger.info(AppStrings.LOG_SYS_LOG_CLEANUP_DONE.format(deleted_count))
        except Exception as e:
            logger.error(AppStrings.LOG_RES_LOG_CLEANUP_FAIL.format(e))
