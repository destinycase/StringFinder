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

    # 테마 적용 (AttributeError 방지를 위해 load_stylesheet 사용)
    app.setStyleSheet(qdarktheme.load_stylesheet())

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
