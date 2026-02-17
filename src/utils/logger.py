import logging
import os
import sys


from PySide6.QtCore import QObject, Signal, qInstallMessageHandler, QtMsgType


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

    # 콘솔 핸들러 (표준 출력을 통해 로그 모니터링, stdout이 있을 때만)
    if sys.stdout:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    # 파일 핸들러 (사용자 AppData 경로에 로그 파일 생성 및 기록)
    app_data = os.getenv("APPDATA")
    if not app_data:
        # APPDATA 환경변수가 없는 경우(예: 일부 제한된 환경) 홈 디렉토리 하위로 폴백
        app_data = os.path.join(os.path.expanduser("~"), ".stringfinder")

    log_dir = os.path.join(app_data, "StringFinder")
    if not os.path.exists(log_dir):
        try:
            os.makedirs(log_dir, exist_ok=True)
        except Exception as e:
            # 폴더 생성 실패 시 임시 폴더로 최종 폴백
            import tempfile

            log_dir = os.path.join(tempfile.gettempdir(), "StringFinder")
            os.makedirs(log_dir, exist_ok=True)
            print(f"Warning: Failed to create log directory in AppData, using temp: {e}")

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
# 싱글톤 로거 및 Qt 핸들러 인스턴스 생성
logger, qt_log_handler = setup_logger()


def qt_message_handler(mode, context, message):
    """Qt 내부 메시지를 logging 모듈로 리다이렉트합니다."""
    if mode == QtMsgType.QtInfoMsg:
        # Qt Info 메시지는 너무 많으므로 Debug 레벨로 기록하거나 무시
        # logger.debug(f"[Qt Info] {message}")
        pass
    elif mode == QtMsgType.QtWarningMsg:
        logger.warning(f"[Qt Warning] {message}")
    elif mode == QtMsgType.QtCriticalMsg:
        logger.error(f"[Qt Critical] {message}")
    elif mode == QtMsgType.QtFatalMsg:
        logger.critical(f"[Qt Fatal] {message}")


# Qt 메시지 핸들러 설치 (전역)
qInstallMessageHandler(qt_message_handler)
