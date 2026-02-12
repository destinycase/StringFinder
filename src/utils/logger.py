import logging
import os
import sys


from PySide6.QtCore import QObject, Signal


class QtLogHandler(logging.Handler, QObject):
    """로그 메시지를 Qt Signal로 방출하는 핸들러"""

    message_logged = Signal(str)

    def __init__(self):
        """로거와 Qt 객체를 필수로 초기화합니다."""
        logging.Handler.__init__(self)
        QObject.__init__(self)

    def emit(self, record):
        msg = self.format(record)
        self.message_logged.emit(msg)


def setup_logger():
    """애플리케이션 전역 로깅 설정"""
    logger = logging.getLogger("StringFinder")
    logger.setLevel(logging.DEBUG)

    # 포맷 설정
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    # 콘솔 핸들러 (표준 출력을 통해 로그 모니터링)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 파일 핸들러 (사용자 AppData 경로에 로그 파일 생성 및 기록)
    app_data = os.getenv("APPDATA")
    log_dir = os.path.join(app_data, "StringFinder")
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    # 타임스탬프가 포함된 로그 파일명 생성
    from datetime import datetime

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(log_dir, f"stringfinder_{timestamp}.log")
    file_handler = logging.FileHandler(log_file, encoding="utf-8", mode="w")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Qt GUI 연동을 위한 전용 핸들러 추가
    qt_handler = QtLogHandler()
    qt_handler.setFormatter(formatter)
    logger.addHandler(qt_handler)

    return logger, qt_handler


# 싱글톤 로거 및 Qt 핸들러 인스턴스 생성
logger, qt_log_handler = setup_logger()
