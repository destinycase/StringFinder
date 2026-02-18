import logging
import os
import sys


from PySide6.QtCore import QObject, Signal, qInstallMessageHandler, QtMsgType


class LogSignaler(QObject):
    message_logged = Signal(str)


class QtLogHandler(logging.Handler):
    """로그 메시지를 Qt Signal로 방출하는 핸들러"""

    def __init__(self):
        super().__init__()
        self.signaler = LogSignaler()

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record)
        self.signaler.message_logged.emit(msg)


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
        except Exception:
            pass

    return _logger_instance


def get_qt_log_handler():
    """Qt 전용 로그 핸들러를 반환합니다. (QApplication 필요 지점에서 호출)"""
    global _qt_log_handler_instance, _logger_instance
    if _qt_log_handler_instance is None:
        # [v4.33.2 Fix] Essential: Only init Qt signal handler when a QApplication exists
        # to prevent hard crashes during pytest collection phase.
        from PySide6.QtWidgets import QApplication

        if QApplication.instance():
            _qt_log_handler_instance = QtLogHandler()
            formatter = logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
            )
            _qt_log_handler_instance.setFormatter(formatter)
            get_logger().addHandler(_qt_log_handler_instance)

            # Install message handler only when Qt is ready
            qInstallMessageHandler(qt_message_handler)

    return _qt_log_handler_instance


# 전역적으로 임포트하여 사용 가능하도록 로거는 즉시 초기화 (표준 핸들러만)
logger = get_logger()


# qt_log_handler는 Proxy 객체로 만들어 기존 코드 호환성 유지
class QtLogHandlerProxy:
    def __getattribute__(self, name):
        handler = get_qt_log_handler()
        if handler:
            return getattr(handler, name)
        raise RuntimeError("QtLogHandler is not initialized. Ensure QApplication exists.")


qt_log_handler = QtLogHandlerProxy()


def qt_message_handler(mode, context, message):
    """Qt 내부 메시지를 logging 모듈로 리다이렉트합니다."""
    if mode == QtMsgType.QtInfoMsg:
        # Qt Info 메시지는 너무 많으므로 Debug 레벨로 기록하거나 무시
        # logger.debug(f"[Qt Info] {message}")
        pass
    elif mode == QtMsgType.QtWarningMsg:
        from sf_utils.app_strings import AppStrings

        logger.warning(AppStrings.LOG_QT_WARNING.format(message))
    elif mode == QtMsgType.QtCriticalMsg:
        from sf_utils.app_strings import AppStrings

        logger.error(AppStrings.LOG_QT_CRITICAL.format(message))
    elif mode == QtMsgType.QtFatalMsg:
        from sf_utils.app_strings import AppStrings

        logger.critical(AppStrings.LOG_QT_FATAL.format(message))


# Qt 메시지 핸들러는 get_qt_log_handler() 호출 시점에 설치됩니다.
