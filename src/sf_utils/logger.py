import collections
import logging
import os
import sys
import threading

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


LOG_HISTORY_MAX_COUNT = 5000
# (번호, 수준, 줄). 번호는 1부터 늘기만 해서, 로그 창은 마지막으로 보인 번호 뒤의 줄만 붙입니다(7.5.6).
_log_history: "collections.deque[tuple[int, str, str]]" = collections.deque(maxlen=LOG_HISTORY_MAX_COUNT)
_log_history_lock = threading.Lock()
_log_history_seq = 0


class LogHistoryHandler(logging.Handler):
    """프로그램 시작 후의 로그를 최근 5,000건까지 모아 둡니다. 모든 탭의 로그 창이 이 기록을 함께 봅니다(7.5.3)."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = (normalize_log_level(record.levelname), self.format(record))
        except Exception:
            return
        global _log_history_seq
        with _log_history_lock:
            _log_history_seq += 1
            _log_history.append((_log_history_seq, entry[0], entry[1]))


def get_log_history() -> "list[tuple[str, str]]":
    """지금까지 모은 (수준, 줄) 목록의 사본입니다."""
    with _log_history_lock:
        return [(level, message) for _, level, message in _log_history]


def get_log_snapshot() -> "tuple[int, list[tuple[str, str]]]":
    """(마지막 번호, (수준, 줄) 목록)을 한 번에 돌려줍니다. 화면을 다시 그릴 때 씁니다."""
    with _log_history_lock:
        return _log_history_seq, [(level, message) for _, level, message in _log_history]


def get_log_entries_after(seq: int, limit: int) -> "list[tuple[int, str, str]]":
    """번호가 `seq`보다 큰 (번호, 수준, 줄)을 오래된 것부터 최대 `limit`개 돌려줍니다."""
    with _log_history_lock:
        if not _log_history or _log_history[-1][0] <= seq:
            return []
        result = []
        for entry in _log_history:
            if entry[0] > seq:
                result.append(entry)
                if len(result) >= limit:
                    break
        return result


def get_last_log_seq() -> int:
    with _log_history_lock:
        return _log_history_seq


def clear_log_history() -> None:
    with _log_history_lock:
        _log_history.clear()


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

    # 핸들러 중복 등록 방지 — 이미 핸들러가 있으면 추가하지 않음
    # logging.getLogger()는 전역 레지스트리를 반환하므로 _logger_instance 리셋 후
    # 재호출 시에도 기존 핸들러가 보존됩니다.
    if not _logger_instance.handlers:
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        # 표준 출력을 위한 콘솔 로그 핸들러를 추가합니다.
        history_handler = LogHistoryHandler()
        history_handler.setFormatter(formatter)
        _logger_instance.addHandler(history_handler)
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
