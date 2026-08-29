import logging
import os
import sys

from PySide6.QtCore import QObject, QtMsgType, Signal, qInstallMessageHandler


class LogSignaler(QObject):
    message_logged = Signal(str)
    level_message_logged = Signal(str, str)


class QtLogHandler(logging.Handler):
    """Python의 로깅 시스템과 Qt의 시그널 시스템을 연결하는 핸들러입니다."""

    def __init__(self):
        super().__init__()
        self.signaler = LogSignaler()

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record)
        level = normalize_log_level(record.levelname)
        self.signaler.message_logged.emit(msg)
        self.signaler.level_message_logged.emit(level, msg)


def normalize_log_level(level_name: str) -> str:
    upper_level = str(level_name or "").upper()
    if upper_level in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        return upper_level
    return "INFO"


_logger_instance = None
_qt_log_handler_instance = None


def get_logger():
    """로거 인스턴스를 반환합니다. (지연 초기화 지원)"""
    global _logger_instance, _qt_log_handler_instance
    if _logger_instance is None:
        _logger_instance = logging.getLogger("StringFinder")
        _logger_instance.setLevel(logging.DEBUG)

    # [H-03 Fix] 핸들러 중복 등록 방지 — 이미 핸들러가 있으면 추가하지 않음
    # logging.getLogger()는 전역 레지스트리를 반환하므로 _logger_instance 리셋 후
    # 재호출 시에도 기존 핸들러가 보존됩니다.
    if not _logger_instance.handlers:
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        # 표준 출력을 위한 콘솔 로그 핸들러를 추가합니다.
        if sys.stdout:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(formatter)
            _logger_instance.addHandler(console_handler)
        # 로그 데이터를 파일로 저장하기 위한 핸들러를 구성합니다.
        app_data = os.getenv("APPDATA") or os.path.join(os.path.expanduser("~"), ".stringfinder")
        log_dir = os.path.join(app_data, "StringFinder")
        try:
            os.makedirs(log_dir, exist_ok=True)
        except Exception:
            import tempfile

            log_dir = os.path.join(tempfile.gettempdir(), "StringFinder")
            os.makedirs(log_dir, exist_ok=True)
        from datetime import datetime

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        process_id = os.getpid()
        log_file = os.path.join(log_dir, f"stringfinder_{timestamp}_{process_id}.log")
        try:
            # Windows spawn 자식 프로세스가 같은 시각에 초기화되어도 기존 로그를 덮어쓰지 않습니다.
            file_handler = logging.FileHandler(log_file, encoding="utf-8", mode="a")
            file_handler.setFormatter(formatter)
            _logger_instance.addHandler(file_handler)
        except Exception as e:
            _logger_instance.debug(f"Failed to add file handler: {e}")
    return _logger_instance


def get_qt_log_handler():
    """Qt 시그널 연동을 위한 전역 로그 핸들러 인스턴스를 반환합니다."""
    global _qt_log_handler_instance, _logger_instance
    if _qt_log_handler_instance is None:
        from PySide6.QtWidgets import QApplication

        if QApplication.instance():
            _qt_log_handler_instance = QtLogHandler()
            formatter = logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
            )
            _qt_log_handler_instance.setFormatter(formatter)
            get_logger().addHandler(_qt_log_handler_instance)
            qInstallMessageHandler(qt_message_handler)
    return _qt_log_handler_instance


# 모든 모듈에서 즉시 로깅을 사용할 수 있도록 전역 로거를 초기화합니다.
logger = get_logger()


class QtLogHandlerProxy:
    def __getattribute__(self, name):
        handler = get_qt_log_handler()
        if handler:
            return getattr(handler, name)
        raise RuntimeError("QtLogHandler is not initialized. Ensure QApplication exists.")


qt_log_handler = QtLogHandlerProxy()


def qt_message_handler(mode, _context, message):
    """Qt 내부에서 발생하는 메시지를 Python 로깅 시스템으로 전달하는 핸들러입니다."""
    if mode == QtMsgType.QtInfoMsg:
        from sf_utils.app_strings import AppStrings
        logger.info(AppStrings.LOG_QT_INFO.format(message))
    elif mode == QtMsgType.QtWarningMsg:
        if "DirectWrite: CreateFontFaceFromHDC() failed" in message:
            return
        from sf_utils.app_strings import AppStrings

        logger.warning(AppStrings.LOG_QT_WARNING.format(message))
    elif mode == QtMsgType.QtCriticalMsg:
        from sf_utils.app_strings import AppStrings

        logger.error(AppStrings.LOG_QT_CRITICAL.format(message))
    elif mode == QtMsgType.QtFatalMsg:
        from sf_utils.app_strings import AppStrings

        logger.critical(AppStrings.LOG_QT_FATAL.format(message))
