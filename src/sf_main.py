import sys
import os
import multiprocessing
import qdarktheme
from PySide6.QtWidgets import QApplication
from ui.main_window import MainWindow
from sf_utils.logger import logger
from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants
from sf_utils.single_instance import SingleInstanceController


def main():
    logger.info(AppStrings.LOG_SYS_APP_STARTED_DECO)
    app = QApplication(sys.argv)
    app.setApplicationName(Constants.APP_NAME)
    app.setApplicationVersion(Constants.APP_VERSION)

    # [v4.30.0] 단일 인스턴스 체크
    instance_key = f"{Constants.APP_NAME}_SingleInstance_Lock"
    controller = SingleInstanceController(instance_key)
    if controller.check_and_start():
        # 이미 실행 중이면 종료
        return

    class QThreadWarningDetector:
        """stderr를 후킹하여 QThread 경고를 감지하고 팝업으로 표시합니다."""

        def __init__(self):
            from PySide6.QtWidgets import QMessageBox
            from PySide6.QtCore import QTimer

            self.original_stderr = sys.stderr
            self.warning_detected = False
            self.QMessageBox = QMessageBox
            self.QTimer = QTimer

        def write(self, text):
            if self.original_stderr:
                self.original_stderr.write(text)
                try:
                    self.original_stderr.flush()
                except Exception:
                    pass
            # QThread가 삭제되기 전에 종료되지 않은 경우 발생하는 경고를 감지합니다.
            if "QThread: Destroyed while thread" in text and not self.warning_detected:
                self.warning_detected = True
                import traceback

                stack_trace = "".join(traceback.format_stack())
                logger.warning(AppStrings.LOG_SYS_QT_THREAD_WARNING.format(text, stack_trace))

                # [개선] 경고 발생 시 메인 스레드에서 팝업을 표시하도록 스케줄링
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

        def flush(self):
            if self.original_stderr:
                self.original_stderr.flush()

    sys.stderr = QThreadWarningDetector()

    app.setStyleSheet(qdarktheme.load_stylesheet())

    window = MainWindow()
    # 중복 실행 요청 시 기존 창 활성화 연결
    controller.instance_requested.connect(window.show_normal_and_activate)
    window.show()

    def cleanup_on_exit():
        from sf_utils.logger import logger
        from sf_utils.config_manager import ConfigManager
        from core.system_manager import SystemManager
        from core.worker import GlobalExecutor, shutdown_global_manager

        try:
            logger.info(AppStrings.LOG_SYS_WKR_SHUTDOWN)
            GlobalExecutor.shutdown(wait=True, cancel_futures=True)
            logger.info(AppStrings.LOG_SYS_WKR_SHUTDOWN_DONE)
        except Exception as e:
            logger.error(AppStrings.LOG_SYS_WKR_SHUTDOWN_FAIL.format(e))

        # [성능] 전역 멀티프로세싱 매니저 종료
        try:
            shutdown_global_manager()
        except Exception:
            pass

        try:
            config_mgr = ConfigManager()
            config_mgr.stop()

            retention = config_mgr.get("log_retention", {"enabled": True, "max_files": 5, "max_days": 7})

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
            except Exception:
                pass

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

    import traceback
    import datetime

    # 1. 로거를 통한 기록
    try:
        logger.critical(AppStrings.LOG_SYS_UNCAUGHT_EXCEPTION, exc_info=(exctype, value, tb))
    except Exception:
        pass

    # 2. 크래시 덤프 파일 작성 (로깅 실패 대비)
    error_msg = "".join(traceback.format_exception(exctype, value, tb))
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    full_msg = f"[{timestamp}] CRITICAL ERROR:\n{error_msg}"

    # stderr 출력
    print(full_msg, file=sys.stderr)

    try:
        dump_path = os.path.join(os.getcwd(), "crash_dump.txt")
        with open(dump_path, "a", encoding="utf-8") as f:
            f.write(f"\n{'=' * 50}\n{full_msg}\n{'=' * 50}\n")
    except Exception:
        pass

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
    except Exception:
        pass

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
