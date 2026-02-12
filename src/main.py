import sys
import multiprocessing
import qdarktheme
from PySide6.QtWidgets import QApplication
from ui.main_window import MainWindow
from utils.logger import logger
from utils.app_strings import AppStrings


def main():
    logger.info(f"--- {AppStrings.LOG_APP_STARTED} ---")
    app = QApplication(sys.argv)
    app.setApplicationName(AppStrings.APP_NAME)
    app.setApplicationVersion(AppStrings.APP_VERSION)

    app.setApplicationVersion(AppStrings.APP_VERSION)

    # 시스템 테마에 맞춰 고대비 또는 다크 테마를 강제 적용합니다.
    app.setStyleSheet(qdarktheme.load_stylesheet())

    window = MainWindow()
    window.show()

    # 프로그램 종료 시 로그 파일 정리 로직
    def cleanup_on_exit():
        import os
        import glob
        import time
        from utils.logger import logger
        from utils.config_manager import ConfigManager

        logger.info(AppStrings.LOG_APP_SHUTDOWN)

        # 로깅 핸들러들을 닫아 파일 스트림 점유를 해제합니다.
        for handler in logger.handlers:
            handler.close()

        try:
            config = ConfigManager()
            retention = config.get("log_retention", {})

            # AppData 경로 확인
            app_data = os.getenv("APPDATA")
            log_dir = os.path.join(app_data, "StringFinder")

            if not retention.get("enabled", False):
                # 기존 동작: 모든 로그 삭제
                for log_file in glob.glob(os.path.join(log_dir, "stringfinder_*.log")):
                    try:
                        os.remove(log_file)
                    except Exception:
                        pass
                return

            # 새 동작: 조건부 정리
            log_files = sorted(
                glob.glob(os.path.join(log_dir, "stringfinder_*.log")),
                key=os.path.getmtime,
                reverse=True,  # 최신 파일이 먼저
            )

            max_files = retention.get("max_files", 5)
            max_days = retention.get("max_days", 7)
            cutoff_time = time.time() - (max_days * 86400)

            for i, log_file in enumerate(log_files):
                try:
                    # 파일 수 제한 또는 보관 기간 초과 시 삭제
                    if i >= max_files or os.path.getmtime(log_file) < cutoff_time:
                        os.remove(log_file)
                except Exception:
                    pass

        except Exception:
            # 로그 정리 실패는 치명적이지 않으므로 무시
            pass

    app.aboutToQuit.connect(cleanup_on_exit)

    sys.exit(app.exec())


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
