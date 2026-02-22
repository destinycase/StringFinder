import logging
import os
import sys

from PySide6.QtCore import QObject, QtMsgType, Signal, qInstallMessageHandler


class LogSignaler(QObject):
    message_logged = Signal(str)
    level_message_logged = Signal(str, str)


class QtLogHandler(logging.Handler):
    """QtLogHandler 클래스."""

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
    if upper_level in {"DEBUG", "INFO", "WARNING", "CRITICAL"}:
        return upper_level
    if upper_level == "ERROR":
        return "CRITICAL"
    return "INFO"


_logger_instance = None
_qt_log_handler_instance = None


def get_logger():
    """로거 인스턴스를 반환합니다. (지연 초기화 지원)"""
    global _logger_instance, _qt_log_handler_instance
    if _logger_instance is None:
        _logger_instance = logging.getLogger("StringFinder")
        _logger_instance.setLevel(logging.DEBUG)
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        # 콘솔 핸들러
        if sys.stdout:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(formatter)
            _logger_instance.addHandler(console_handler)
        # 파일 핸들러
        app_data = os.getenv("APPDATA") or os.path.join(os.path.expanduser("~"), ".stringfinder")
        log_dir = os.path.join(app_data, "StringFinder")
        try:
            os.makedirs(log_dir, exist_ok=True)
        except Exception:
            import tempfile

            log_dir = os.path.join(tempfile.gettempdir(), "StringFinder")
            os.makedirs(log_dir, exist_ok=True)
        from datetime import datetime

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = os.path.join(log_dir, f"stringfinder_{timestamp}.log")
        try:
            file_handler = logging.FileHandler(log_file, encoding="utf-8", mode="w")
            file_handler.setFormatter(formatter)
            _logger_instance.addHandler(file_handler)
        except Exception as e:
            _logger_instance.debug(f"Failed to add file handler: {e}")
    return _logger_instance


def get_qt_log_handler():
    """get_qt_log_handler 함수."""
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


# 전역적으로 임포트하여 사용 가능하도록 로거는 즉시 초기화 (표준 핸들러만)
logger = get_logger()


class QtLogHandlerProxy:
    def __getattribute__(self, name):
        handler = get_qt_log_handler()
        if handler:
            return getattr(handler, name)
        raise RuntimeError("QtLogHandler is not initialized. Ensure QApplication exists.")


qt_log_handler = QtLogHandlerProxy()


def qt_message_handler(mode, context, message):
    """qt_message_handler 함수."""
    if mode == QtMsgType.QtInfoMsg:
        pass
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
