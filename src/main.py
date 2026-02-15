import sys
import os
import multiprocessing
import qdarktheme
from PySide6.QtWidgets import QApplication
from ui.main_window import MainWindow
from utils.logger import logger
from utils.app_strings import AppStrings
from utils.constants import Constants


def main():
    logger.info(AppStrings.LOG_SYS_APP_STARTED_DECO)
    app = QApplication(sys.argv)
    app.setApplicationName(Constants.APP_NAME)
    app.setApplicationVersion(Constants.APP_VERSION)

    # [Debug] QThread 경고 감지 및 팝업 표시
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
            self.original_stderr.write(text)
            self.original_stderr.flush()  # 즉시 출력
            # QThread 경고 감지
            if "QThread: Destroyed while thread" in text and not self.warning_detected:
                self.warning_detected = True
                # 스택 트레이스 수집
                import traceback

                stack_trace = "".join(traceback.format_stack())

                # [Fix] 즉시 팝업 표시 (QTimer 없이)
                # 프로그램 종료 시에도 팝업이 표시되도록 동기적으로 실행
                try:
                    self.show_warning_popup(text, stack_trace)
                except Exception as e:
                    self.original_stderr.write(f"Failed to show QThread warning popup: {e}\n")

        def show_warning_popup(self, warning_text, stack_trace):
            """QThread 경고 팝업을 표시합니다."""
            msg = self.QMessageBox()
            msg.setIcon(self.QMessageBox.Warning)
            msg.setWindowTitle(AppStrings.DEBUG_QTHREAD_WARNING_TITLE)
            msg.setText(AppStrings.DEBUG_QTHREAD_WARNING_TEXT)
            msg.setInformativeText(AppStrings.DEBUG_QTHREAD_WARNING_INFO)
            msg.setDetailedText(AppStrings.DEBUG_QTHREAD_WARNING_DETAILS.format(warning_text, stack_trace))
            msg.exec()
            self.warning_detected = False  # 다음 경고를 위해 리셋

        def flush(self):
            self.original_stderr.flush()

    # stderr 후킹 설치
    sys.stderr = QThreadWarningDetector()

    # 시스템 테마에 맞춰 고대비 또는 다크 테마를 강제 적용합니다.
    app.setStyleSheet(qdarktheme.load_stylesheet())

    window = MainWindow()
    window.show()

    # [Fix] 전역 예외 처리기 (Global Exception Handler)
    def global_exception_handler(exctype, value, traceback):
        """
        프로그램에서 처리되지 않은 예외를 포착하여 로그에 기록하고
        치명적 오류임을 알립니다.
        """
        # KeyboardInterrupt는 정상 종료 흐름을 따르도록 무시 (또는 기본 핸들러 호출)
        if issubclass(exctype, KeyboardInterrupt):
            sys.__excepthook__(exctype, value, traceback)
            return

        logger.critical("Uncaught exception", exc_info=(exctype, value, traceback))

        # UI 모드일 경우 에러 메시지 박스를 띄우는 것도 고려 가능하지만,
        # 크래시 시점의 안정성을 위해 로그 기록 후 종료에 집중합니다.
        # 필요 시: QMessageBox.critical(None, "Critical Error", str(value))

        # 기존 핸들러 호출 (콘솔 출력 등)
        sys.__excepthook__(exctype, value, traceback)

    sys.excepthook = global_exception_handler

    # 프로그램 종료 시 로그 파일 정리 로직
    def cleanup_on_exit():
        from utils.logger import logger
        from utils.config_manager import ConfigManager
        from core.system_manager import SystemManager

        # [Fix] 중복 로그 제거 - main_window.py:112에서 이미 로깅함
        # logger.info(AppStrings.LOG_SYS_SHUTDOWN)  # 제거됨

        # 로깅 핸들러들을 플러시(Flush)하고 닫아 파일 스트림 점유를 해제합니다.
        # 주의: 윈도우에서 열린 파일을 삭제하려면 먼저 닫아야 하지만,
        # cleanup_logs 내부에서 '현재 세션 로그'는 건너뛰므로
        # 여기서는 핸들러를 닫지 않고(또는 닫더라도) 삭제 대상이 아닌 파일만 정리하면 됨.
        # 그러나 안전을 위해 플러시만 하고, 닫는 건 삭제 로직 이후로 미루거나,
        # cleanup_logs가 '현재 파일 제외' 로직을 믿고 수행.
        # 기존 로직 유지: 핸들러 닫기는 맨 마지막에 하거나,
        # cleanup_logs 호출 시점에는 로거가 살아있어야 로깅 가능.

        try:
            # 설정 로드 및 자동 저장 중지
            config_mgr = ConfigManager()
            config_mgr.stop()

            retention = config_mgr.get("log_retention", {"enabled": True, "max_files": 5, "max_days": 7})

            # 로그 디렉토리 경로
            app_data = os.getenv("APPDATA")
            if app_data:
                log_dir = os.path.join(app_data, "StringFinder")

                # [Refactor] SystemManager에게 정리 위임
                sys_mgr = SystemManager()
                sys_mgr.cleanup_logs(log_dir, retention)

        except Exception as e:
            print(f"Error during shutdown cleanup: {e}")

        # 마지막으로 핸들러 정리
        for handler in list(logger.handlers):
            try:
                handler.flush()
                handler.close()
                logger.removeHandler(handler)
            except Exception:
                pass

    app.aboutToQuit.connect(cleanup_on_exit)

    sys.exit(app.exec())


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
