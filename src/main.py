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

    # 시스템 테마에 맞춰 고대비 또는 다크 테마를 강제 적용합니다.
    app.setStyleSheet(qdarktheme.load_stylesheet())

    window = MainWindow()
    window.show()

    # 프로그램 종료 시 로그 파일 삭제 로직 추가
    def cleanup_on_exit():
        import os
        from utils.logger import logger

        logger.info(AppStrings.LOG_APP_SHUTDOWN)

        # 로깅 핸들러들을 닫아 파일 스트림 점유를 해제합니다.
        for handler in logger.handlers:
            handler.close()

        try:
            if os.path.exists(AppStrings.LOG_FILE_NAME):
                os.remove(AppStrings.LOG_FILE_NAME)
        except Exception as e:
            logger.error(AppStrings.ERROR_LOG_DELETE.format(e))

    app.aboutToQuit.connect(cleanup_on_exit)

    sys.exit(app.exec())


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
