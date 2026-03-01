import multiprocessing
import os
import sys

import qdarktheme
from PySide6.QtWidgets import QApplication

from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants
from sf_utils.logger import logger
from sf_utils.single_instance import SingleInstanceController
from ui.main_window import MainWindow


def main():
    logger.info(AppStrings.LOG_SYS_APP_STARTED_DECO)
    app = QApplication(sys.argv)
    app.setApplicationName(Constants.APP_NAME)
    app.setApplicationVersion(Constants.APP_VERSION)

    # Windows 환경에서 현대적인 기본 폰트 설정 (DirectWrite 레거시 폰트 경고 방지)
    if os.name == "nt":
        from PySide6.QtGui import QFont

        app.setFont(QFont("Segoe UI", 9))
    instance_key = f"{Constants.APP_NAME}_SingleInstance_Lock"
    controller = SingleInstanceController(instance_key)
    if controller.check_and_start():
        # 이미 실행 중이면 종료
        return

    class QThreadWarningDetector:
        """QThreadWarningDetector 클래스."""

        def __init__(self):
            from PySide6.QtCore import QTimer
            from PySide6.QtWidgets import QMessageBox

            self.original_stderr = sys.stderr
            self.warning_detected = False
            self.QMessageBox = QMessageBox
            self.QTimer = QTimer

        def write(self, text):
            if self.original_stderr:
                try:
                    self.original_stderr.write(text)
                    self.original_stderr.flush()
                except Exception as e:
                    # stderr가 이미 닫혔거나 fileno가 없는 특수 스트림(pytest, frozen EXE 등)인 경우 대응
                    try:
                        logger.debug(AppStrings.LOG_SYS_STDERR_FLUSH_FAIL.format(e))
                    except Exception:
                        pass
            if "QThread: Destroyed while thread" in text and not self.warning_detected:
                self.warning_detected = True
                import traceback

                stack_trace = "".join(traceback.format_stack())
                logger.warning(AppStrings.LOG_SYS_QT_THREAD_WARNING.format(text, stack_trace))
                # 경고 발생 시 메인 스레드에서 팝업을 표시하도록 스케줄링
                try:

                    def show_popup():
                        from PySide6.QtWidgets import QMessageBox

                        msg = QMessageBox()
                        msg.setIcon(QMessageBox.Icon.Warning)
                        msg.setWindowTitle(AppStrings.TITLE_QT_WARNING)
                        msg.setText(AppStrings.MSG_QT_THREAD_DESTROYED)
                        msg.setInformativeText(AppStrings.MSG_QT_RESOURCE_LEAK)
                        msg.setDetailedText(f"{text}\n\nStack Trace:\n{stack_trace}")
                        msg.exec()

                    self.QTimer.singleShot(0, show_popup)
                except Exception as e:
                    self.original_stderr.write(f"Failed to schedule warning popup: {e}\n")
            elif "DirectWrite: CreateFontFaceFromHDC() failed" in text:
                # Windows DirectWrite 폰트 렌더링 경고 필터링 (로그 노이즈 제거)
                return

        def flush(self):
            if self.original_stderr:
                self.original_stderr.flush()

    sys.stderr = QThreadWarningDetector()
    app.setStyleSheet(qdarktheme.load_stylesheet())
    window = MainWindow()
    # 중복 실행 요청 시 기존 창 활성화 연결
    controller.instance_requested.connect(window.show_normal_and_activate)
    # 앱 시작 시점에 로그 정리 수행 (종료 시뿐만 아니라 시작 시에도 공간 확보)
    try:
        from core.system_manager import SystemManager
        from sf_utils.config_manager import ConfigManager

        config_mgr = ConfigManager()
        # Use ConfigManager.defaults as SSOT for log_retention defaults.
        _log_ret_default = config_mgr.defaults.get(Constants.CONFIG_KEY_LOG_RETENTION, {})
        retention = config_mgr.get(Constants.CONFIG_KEY_LOG_RETENTION, _log_ret_default)
        app_data = os.getenv("APPDATA")
        if app_data:
            log_dir = os.path.join(app_data, "StringFinder")
            SystemManager().cleanup_logs(log_dir, retention)
    except Exception as e:
        logger.debug(AppStrings.LOG_SYS_STARTUP_LOG_CLEANUP_FAIL.format(e))
    window.show()

    def cleanup_on_exit():
        from core.system_manager import SystemManager
        from core.worker import GlobalExecutor, shutdown_global_manager
        from sf_utils.config_manager import ConfigManager
        from sf_utils.logger import logger

        try:
            logger.info(AppStrings.LOG_SYS_WKR_SHUTDOWN)
            GlobalExecutor.shutdown(wait=True, cancel_futures=True)
            logger.info(AppStrings.LOG_SYS_WKR_SHUTDOWN_DONE)
        except Exception as e:
            logger.error(AppStrings.LOG_SYS_WKR_SHUTDOWN_FAIL.format(e))
        # [성능] 전역 멀티프로세싱 매니저 종료
        try:
            shutdown_global_manager()
        except Exception as e:
            logger.debug(AppStrings.LOG_SYS_CLOSE_CLEANUP_FAIL.format(e))
        try:
            config_mgr = ConfigManager()
            config_mgr.stop()
            # SSOT: use ConfigManager.defaults as fallback
            _log_ret_default = config_mgr.defaults.get(Constants.CONFIG_KEY_LOG_RETENTION, {})
            retention = config_mgr.get(Constants.CONFIG_KEY_LOG_RETENTION, _log_ret_default)
            app_data = os.getenv("APPDATA")
            if app_data:
                log_dir = os.path.join(app_data, "StringFinder")
                sys_mgr = SystemManager()
                sys_mgr.cleanup_logs(log_dir, retention)
        except Exception as e:
            print(AppStrings.LOG_SYS_CLEANUP_ERROR.format(e))
        for handler in list(logger.handlers):
            try:
                handler.flush()
                handler.close()
                logger.removeHandler(handler)
            except Exception as e:
                logger.debug(AppStrings.LOG_SYS_HANDLER_CLEANUP_FAIL.format(e))

    app.aboutToQuit.connect(cleanup_on_exit)
    sys.exit(app.exec())


def global_exception_handler(exctype, value, tb):
    """
    프로그램에서 처리되지 않은 예외를 포착하여 로그를 기록하고,
    크래시 덤프 생성 및 사용자 알림을 수행합니다.
    """
    if issubclass(exctype, KeyboardInterrupt):
        sys.__excepthook__(exctype, value, tb)
        return
    import datetime
    import traceback

    # 1. 로거를 통한 기록
    try:
        logger.critical(AppStrings.LOG_SYS_UNCAUGHT_EXCEPTION, exc_info=(exctype, value, tb))
    except Exception as e:
        print(f"Logging fail in handler: {e}", file=sys.stderr)
    # 2. 크래시 덤프 파일 작성 (로깅 실패 대비)
    error_msg = "".join(traceback.format_exception(exctype, value, tb))
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    full_msg = f"[{timestamp}] CRITICAL ERROR:\n{error_msg}"
    print(full_msg, file=sys.stderr)
    try:
        # CWD 대신 권한이 보장된 APPDATA 경로에 덤프 생성
        app_data = os.getenv("APPDATA")
        if app_data:
            dump_dir = os.path.join(app_data, "StringFinder")
            os.makedirs(dump_dir, exist_ok=True)
            dump_path = os.path.join(dump_dir, "crash_dump.txt")
        else:
            dump_path = os.path.join(os.getcwd(), "crash_dump.txt")

        with open(dump_path, "a", encoding="utf-8") as f:
            f.write(f"\n{'=' * 50}\n{full_msg}\n{'=' * 50}\n")
    except Exception as e:
        print(f"Failed to write crash dump: {e}")
    # 3. 사용자에게 팝업 알림 (치명적 오류)
    try:
        import ctypes

        MB_ICONERROR = 0x10
        # 윈도우 타이틀과 내용에 한글 적용
        ctypes.windll.user32.MessageBoxW(
            0,
            AppStrings.MSG_CRITICAL_ERROR_POPUP.format(value),
            AppStrings.TITLE_CRITICAL_ERROR,
            MB_ICONERROR,
        )
    except Exception as e:
        logger.debug(AppStrings.LOG_SYS_CRITICAL_POPUP_FAIL.format(e))
    sys.__excepthook__(exctype, value, tb)


# 전역 예외 처리기 등록 (한 번만 수행)
sys.excepthook = global_exception_handler


if __name__ == "__main__":
    multiprocessing.freeze_support()
    try:
        main()
    except Exception as e:
        global_exception_handler(type(e), e, e.__traceback__)
        sys.exit(1)
