import html
import os
import subprocess
import sys
import threading
import tempfile
from pathlib import Path
from PySide6.QtCore import QByteArray, QItemSelection, QItemSelectionModel, QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtCore import QEvent, QObject, QRunnable, QThreadPool
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QKeySequence,
    QShortcut,
    QTextCharFormat,
    QTextCursor,
    QTextFormat,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTableView,
    QPlainTextEdit,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core import search_mode as sm
from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants
from sf_utils.logger import logger
from ui.models import MatchDetailModel, SearchResultModel
from ui.proxies import MatchProxyModel, ResultProxyModel
from ui.styles import UIStyles
from ui.syntax_highlighter import LightweightSyntaxHighlighter
from ui.widgets import HtmlDelegate


_EXCEL_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")


def _sanitize_excel_cell(value):
    """Return user-controlled text as a literal Excel cell value."""
    if isinstance(value, str):
        # Excel이 받지 않는 제어 문자(로그의 색 코드 ESC 등)가 하나라도 있으면 파일 전체 저장이
        # 실패하므로 지웁니다(검수 U-5).
        from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

        value = ILLEGAL_CHARACTERS_RE.sub("", value)
        if value.startswith(_EXCEL_FORMULA_PREFIXES):
            return "'" + value
    return value


def _text_or_empty(value):
    """내보낼 값: 없으면 빈 칸(None이라고 쓰지 않음, 검수 U-10)."""
    return "" if value is None else str(value)


def _append_excel_row(worksheet, values):
    worksheet.append([_sanitize_excel_cell(value) for value in values])


def _detect_text_encoding(file_path):
    """미리보기 인코딩을 검색 엔진과 같은 판정(파일 전체 기준, 설정의 지정 인코딩 우선)으로 정합니다."""
    with open(file_path, "rb") as file:
        sample = file.read(4)
    if sample.startswith(b"\xff\xfe\x00\x00"):
        return "utf-32-le"
    if sample.startswith(b"\x00\x00\xfe\xff"):
        return "utf-32-be"
    from core.search_engine import detect_preview_encoding

    return detect_preview_encoding(file_path)


def _read_context_lines_from_file(file_path, target_line, before_lines, after_lines, cancel_event=None):
    """파일에서 필요한 문맥만 읽습니다. UI 객체에는 접근하지 않습니다."""
    if target_line > ResultView.CONTEXT_MAX_SCAN_LINES:
        return None
    first_line = max(1, target_line - before_lines)
    last_line = target_line + after_lines
    encoding = _detect_text_encoding(file_path)
    context = []
    with open(file_path, "r", encoding=encoding, errors="replace") as file:
        for line_number, line in enumerate(file, start=1):
            if cancel_event is not None and cancel_event.is_set():
                return []
            if line_number >= first_line:
                context.append((line_number, line.rstrip("\r\n")))
            if line_number >= last_line:
                break
    return context


class MatchColumnWatcher(QObject):
    """검색 상세 표의 보이는 폭 변화와 열 경계 끌기 끝을 알려 줍니다(7.5.4).

    미리보기 창과의 분할선을 옮기거나 창 크기를 바꾸면 표의 보이는 폭이 바뀌어 열 폭을 다시 나눕니다.
    """

    def __init__(self, view, on_width_changed, on_drag_finished):
        super().__init__(view)
        self._view = view
        self._on_width_changed = on_width_changed
        self._on_drag_finished = on_drag_finished
        self._last_width = -1
        # 머리글에서 마우스를 누르고 있는 동안만 참입니다. 이때 바뀐 열 폭만 "사용자가 끈 폭"으로 봅니다(7.5.6).
        self.header_pressed = False
        # 표가 지워지는 중에도 이벤트가 오므로, 감시 대상은 미리 잡아 두고 이벤트마다 표를 부르지 않습니다.
        self._viewport = view.viewport()
        self._header_viewport = view.horizontalHeader().viewport()
        self._viewport.installEventFilter(self)
        self._header_viewport.installEventFilter(self)

    def eventFilter(self, watched, event):
        event_type = event.type()
        if event_type == QEvent.Type.Resize and watched is self._viewport:
            width = watched.width()
            if width != self._last_width:
                self._last_width = width
                self._on_width_changed()
        elif watched is self._header_viewport:
            if event_type == QEvent.Type.MouseButtonPress:
                self.header_pressed = True
            elif event_type == QEvent.Type.MouseButtonRelease:
                self.header_pressed = False
                self._on_drag_finished()
        return False


class ContextPreviewSignals(QObject):
    finished = Signal(int, object, object, bool)


class ContextPreviewWorker(QRunnable):
    """문맥 미리보기용 파일 읽기를 UI 스레드 밖에서 수행합니다."""

    def __init__(self, request_id, file_path, target_line, before_lines, after_lines, cancel_event):
        super().__init__()
        self.setAutoDelete(False)
        self.request_id = request_id
        self.file_path = file_path
        self.target_line = target_line
        self.before_lines = before_lines
        self.after_lines = after_lines
        self.cancel_event = cancel_event
        self.signals = ContextPreviewSignals()

    def run(self):
        try:
            lines = _read_context_lines_from_file(
                self.file_path,
                self.target_line,
                self.before_lines,
                self.after_lines,
                self.cancel_event,
            )
            cancelled = self.cancel_event.is_set()
            self.signals.finished.emit(self.request_id, lines, None, cancelled)
        except Exception as error:  # 파일/인코딩/예상하지 못한 읽기 오류를 UI로 전달
            self.signals.finished.emit(self.request_id, None, error, self.cancel_event.is_set())


def _is_walk_failure(path, reason):
    """폴더 탐색 실패(경로를 모르는 항목 포함)인지입니다. 사유는 코드 형식입니다(7.2.5)."""
    from core.skip_reason_codes import SKIP_CODE_WALK, UNKNOWN_WALK_PATH_PREFIX, decode_skip_reason

    return path.startswith(UNKNOWN_WALK_PATH_PREFIX) or any(
        decode_skip_reason(line)[0] == SKIP_CODE_WALK for line in str(reason or "").splitlines() if line.strip()
    )


def _skipped_count_text(entries, total):
    template = AppStrings.SKIPPED_ITEMS_COUNT if any(
        _is_walk_failure(path, reason) for path, reason in entries
    ) else AppStrings.SKIPPED_FILES_COUNT
    return template.format(total)


def normalize_skipped_files(skipped_files, *, strict_session: bool = False):
    """건너뛴 항목을 ``(경로, 사유 코드)`` 목록으로 정리합니다. 같은 경로의 사유는 줄로 합칩니다.

    사유는 코드 형식(``ERR_…|상세``)으로 두고, 문장은 보여 줄 때 ``render_skip_reason``으로 만듭니다(7.2.5).
    ``strict_session``이면 세션에서 읽은 값이라 모양을 엄격히 확인하고, 코드 형식이 아닌 사유는 버립니다.
    """
    from core.skip_reason_codes import TEMPLATE_NAMES, decode_skip_reason, is_coded_reason

    normalized_by_path: dict[str, str] = {}
    if not isinstance(skipped_files, (list, tuple)):
        return []
    for item in skipped_files:
        if isinstance(item, (list, tuple)):
            if not item:
                continue
            raw_path = item[0]
            raw_reason = item[1] if len(item) > 1 else ""
        else:
            if strict_session:
                continue
            raw_path = item
            raw_reason = ""

        if strict_session:
            if not isinstance(raw_path, str) or not isinstance(raw_reason, str):
                continue
            if (
                not raw_path.strip()
                or len(raw_path) > 32767
                or any(character in raw_path for character in ("\x00", "\r", "\n"))
                or len(raw_reason) > 65536
                or not is_coded_reason(raw_reason)
                or any(decode_skip_reason(line)[0] not in TEMPLATE_NAMES
                       for line in raw_reason.splitlines() if line.strip())
            ):
                continue

        path = str(raw_path).strip()
        if not path:
            continue
        reason = str(raw_reason or "").strip()
        previous_reason = normalized_by_path.get(path, "")
        if previous_reason and reason:
            reason_lines = list(dict.fromkeys(
                line for value in (previous_reason, reason) for line in value.splitlines() if line
            ))
            normalized_by_path[path] = "\n".join(reason_lines)
        elif reason or path not in normalized_by_path:
            normalized_by_path[path] = reason
    return list(normalized_by_path.items())


def _format_skipped_files_text(skipped_files, total_count):
    """건너뛴 파일 경로와 사유를 클립보드 친화적인 일반 텍스트로 만듭니다."""
    entries = normalize_skipped_files(skipped_files)
    blocks = []
    from core.skip_reason_codes import UNKNOWN_WALK_PATH_PREFIX

    walk_count = sum(_is_walk_failure(path, reason) for path, reason in entries)
    if walk_count:
        blocks.append(AppStrings.SKIPPED_FILES_AND_WALK_ERRORS.format(len(entries) - walk_count, walk_count))
    for index, (path, reason) in enumerate(entries, start=1):
        is_walk = _is_walk_failure(path, reason)
        if path.startswith(UNKNOWN_WALK_PATH_PREFIX):
            path = AppStrings.SKIPPED_WALK_PATH_UNKNOWN
        if is_walk:
            path = AppStrings.SKIPPED_WALK_ENTRY.format(path)
        lines = [f"{index}. {path}"]
        if reason:
            from core.skip_reason_formatter import render_skip_reason

            lines.append(f"   {AppStrings.SKIPPED_FILES_REASON.format(render_skip_reason(reason))}")
        blocks.append("\n".join(lines))

    missing_count = max(0, int(total_count or 0) - len(entries))
    if missing_count:
        blocks.append(AppStrings.SKIPPED_FILES_DETAILS_MISSING.format(missing_count))
    return "\n\n".join(blocks)


class SkippedFilesDialog(QDialog):
    """건너뛴 파일과 사유를 표시하고 전체 목록 복사를 제공하는 대화상자입니다."""

    def __init__(self, skipped_files, total_count, parent=None):
        super().__init__(parent)
        self.skipped_files = normalize_skipped_files(skipped_files)
        self.total_count = max(int(total_count or 0), len(self.skipped_files))
        self.list_text = _format_skipped_files_text(self.skipped_files, self.total_count)

        has_walk_errors = any(_is_walk_failure(path, reason) for path, reason in self.skipped_files)
        self.setWindowTitle(AppStrings.SKIPPED_ITEMS_DIALOG_TITLE if has_walk_errors else AppStrings.SKIPPED_FILES_DIALOG_TITLE)
        self.resize(760, 460)
        layout = QVBoxLayout(self)

        count_label = QLabel(_skipped_count_text(self.skipped_files, self.total_count))
        count_label.setStyleSheet("font-weight: 700;")
        layout.addWidget(count_label)

        self.list_edit = QPlainTextEdit()
        self.list_edit.setReadOnly(True)
        self.list_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.list_edit.setPlainText(self.list_text)
        layout.addWidget(self.list_edit)

        button_layout = QHBoxLayout()
        self.copy_confirmation_label = QLabel(AppStrings.SKIPPED_FILES_COPIED)
        self.copy_confirmation_label.setVisible(False)
        button_layout.addWidget(self.copy_confirmation_label)
        button_layout.addStretch(1)

        self.copy_button = QPushButton(AppStrings.SKIPPED_FILES_COPY_BUTTON)
        self.copy_button.clicked.connect(self.copy_to_clipboard)
        button_layout.addWidget(self.copy_button)

        close_button = QPushButton(AppStrings.SKIPPED_FILES_CLOSE_BUTTON)
        close_button.clicked.connect(self.accept)
        button_layout.addWidget(close_button)
        layout.addLayout(button_layout)

    def copy_to_clipboard(self):
        QApplication.clipboard().setText(self.list_text)
        self.copy_confirmation_label.setVisible(True)


def _result_note(result_model, path) -> str:
    """내보낼 때 쓸 파일 목록 기타 칸의 사유 문장입니다(지금 언어)."""
    note_for = getattr(result_model, "note_for", None)
    if not callable(note_for):
        return ""
    from core.skip_reason_formatter import render_skip_reason

    return render_skip_reason(note_for(path))


class SearchSpinner(QWidget):
    """검색 중 문구 앞에서 도는 원호입니다. 글자 기호는 글꼴마다 폭이 달라 흔들리므로 직접 그립니다."""

    SIZE = 14
    STEP_DEGREES = 30
    INTERVAL_MS = 80

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        # 부모(요약 줄)의 테두리·여백·최소 높이 스타일을 물려받지 않게 합니다.
        self.setStyleSheet("border: none; background: transparent; padding: 0; margin: 0; min-height: 0;")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._angle = 0
        self._color = QColor("#C47B00")
        self._timer = QTimer(self)
        self._timer.setInterval(self.INTERVAL_MS)
        self._timer.timeout.connect(self._advance)
        self.hide()

    def set_color(self, color):
        self._color = QColor(color)
        self.update()

    def start(self):
        if not self._timer.isActive():
            self._timer.start()
        self.show()

    def stop(self):
        self._timer.stop()
        self.hide()

    def is_spinning(self):
        return self._timer.isActive()

    def _advance(self):
        self._angle = (self._angle + self.STEP_DEGREES) % 360
        self.update()

    def paintEvent(self, _event):
        from PySide6.QtGui import QPainter, QPen

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(self._color, 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        rect = self.rect().adjusted(2, 2, -2, -2)
        painter.drawArc(rect, -self._angle * 16, 270 * 16)
        painter.end()


class SearchShimmer(QWidget):
    """검색 중 요약 줄 위로 밝은 띠가 왼쪽에서 오른쪽으로 지나가는 효과입니다(7.1.20).

    요약 줄과 같은 크기로 겹쳐 그리며, 마우스 입력은 아래로 넘깁니다.
    """

    PERIOD_MS = 1800
    INTERVAL_MS = 30
    BAND_RATIO = 0.4

    def __init__(self, parent):
        super().__init__(parent)
        self.setStyleSheet("border: none; background: transparent; padding: 0; margin: 0; min-height: 0;")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._target = parent
        self._phase = 0.0
        self._color = QColor(240, 196, 106, 70)
        self._timer = QTimer(self)
        self._timer.setInterval(self.INTERVAL_MS)
        self._timer.timeout.connect(self._advance)
        parent.installEventFilter(self)
        self.hide()

    def eventFilter(self, watched, event):
        if watched is self.parent() and event.type() == QEvent.Type.Resize:
            self.setGeometry(watched.rect())
        return False

    def set_color(self, color):
        self._color = QColor(color)
        self.update()

    def start(self):
        self.setGeometry(self._target.rect())
        if not self._timer.isActive():
            self._phase = 0.0
            self._timer.start()
        self.show()
        self.raise_()

    def stop(self):
        self._timer.stop()
        self.hide()

    def is_running(self):
        return self._timer.isActive()

    def _advance(self):
        self._phase = (self._phase + self.INTERVAL_MS / self.PERIOD_MS) % 1.0
        self.update()

    def paintEvent(self, _event):
        from PySide6.QtGui import QLinearGradient, QPainter

        width = self.width()
        band = max(1.0, width * self.BAND_RATIO)
        # 띠가 왼쪽 밖에서 들어와 오른쪽 밖으로 완전히 나갈 때까지 움직입니다.
        left = -band + (width + band) * self._phase
        gradient = QLinearGradient(left, 0, left + band, 0)
        clear = QColor(self._color)
        clear.setAlpha(0)
        gradient.setColorAt(0.0, clear)
        gradient.setColorAt(0.5, self._color)
        gradient.setColorAt(1.0, clear)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        # 테두리 안쪽만 칠합니다.
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 3, 3)
        painter.end()


class ResultView(QWidget):
    CONTEXT_RADIUS = Constants.DEFAULT_CONTEXT_PREVIEW_LINES
    # 검색 상세 열 폭 배분(7.5.4)
    MATCH_LAYOUT_DELAY_MS = 50
    MATCH_POSITION_MAX_WIDTH = 120
    MATCH_KEY_MIN_WIDTH = 80
    MATCH_KEY_MAX_RATIO = 0.15
    MATCH_VALUE_MIN_RATIO = 0.5
    MATCH_COLUMN_MIN_WIDTH = 40
    # 사용자가 열 경계를 직접 끈 비율입니다. 검색 방식(열 구성)마다 기억하며 모든 탭이 함께 씁니다.
    _match_user_ratios: dict = {}
    CONTEXT_MAX_RADIUS = Constants.MAX_CONTEXT_PREVIEW_LINES
    CONTEXT_MAX_SCAN_LINES = 200_000
    CONTEXT_MAX_LINE_CHARS = 4_000
    # Long-line previews show a compact asymmetric window around the match.
    CONTEXT_TRUNCATED_BEFORE_CHARS = 20
    CONTEXT_TRUNCATED_AFTER_CHARS = 500
    EXCEL_PREVIEW_EXTENSIONS = (".xlsx", ".xlsm", ".xls", ".xlsb", ".ods")
    """
    검색 결과 테이블, 상세 매치 테이블, 미리보기 패널을 관리하는 복합 뷰입니다.
    페이지네이션 기능도 포함합니다.
    """

    file_double_clicked = Signal(str, int)  # 파일 경로, 줄 번호
    match_double_clicked = Signal(str, int)  # 파일 경로, 줄 번호
    status_message_requested = Signal(str, int)  # 메시지, 표시 시간(ms)

    def __init__(self, icon_provider, config_manager, parent=None):
        """결과 뷰 객체를 초기화하고 필요한 필터/리사이즈 타이머를 설정합니다."""
        super().__init__(parent)
        self.icon_provider = icon_provider
        self.config_manager = config_manager
        self.search_text = ""
        self.search_mode = Constants.MODE_NORMAL
        self._skipped_files = []
        self._skipped_file_count = 0
        self.selected_file_path = ""
        self._current_match_item = None
        self._context_request_id = 0
        self._context_cancel_event = None
        self._context_workers = []
        self._context_thread_pool = QThreadPool(self)
        self._context_thread_pool.setMaxThreadCount(1)
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(200)  # 창 크기 변경이 멈춘 후 열 너비를 자동으로 조정하기 위한 디바운스 타이머입니다.
        self._resize_timer.timeout.connect(self._adjust_column_widths)

        # 결과 목록 필터링 시 성능을 위해 입력 후 일정 시간 뒤에 검색을 수행하는 타이머입니다.
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(250)
        self._filter_timer.timeout.connect(self._on_result_filters_changed)
        # 상세 목록 거르기: 결과가 많으면 입력이 멈춘 뒤 한 번만 거릅니다(7.1.33).
        self._pending_match_filters: dict = {}
        self._match_filter_timer = QTimer(self)
        self._match_filter_timer.setSingleShot(True)
        self._match_filter_timer.setInterval(250)
        self._match_filter_timer.timeout.connect(self._apply_pending_match_filters)

        self._init_ui()
        self._restore_states()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        # 레이아웃 밀도를 높여 더 많은 정보를 한 화면에 표시하도록 간격을 조정합니다.
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(5)
        self.result_list_container = QWidget()
        result_list_layout = QVBoxLayout(self.result_list_container)
        result_list_layout.setContentsMargins(0, 0, 0, 0)
        result_list_layout.setSpacing(2)
        self.result_filter_layout = QHBoxLayout()
        self.result_file_filter_edit = QLineEdit()
        self.result_file_filter_edit.setPlaceholderText(AppStrings.RESULT_FILTER_FILE_PLACEHOLDER)
        self.result_folder_filter_edit = QLineEdit()
        self.result_folder_filter_edit.setPlaceholderText(AppStrings.RESULT_FILTER_FOLDER_PLACEHOLDER)
        self.result_filter_layout.addWidget(self.result_file_filter_edit)
        self.result_filter_layout.addWidget(self.result_folder_filter_edit)
        # 검색 결과 요약 정보를 표시하는 레이블입니다.
        self.summary_label = QLabel()
        self.summary_label.setWordWrap(False)
        self.summary_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.summary_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.summary_label.setStyleSheet(UIStyles.get_summary_label_style(self._is_dark_theme()))
        self.summary_label.setFixedHeight(32)
        self.summary_label.setVisible(False)
        self.total_match_limit_banner = QFrame()
        self.total_match_limit_banner.setObjectName("totalMatchLimitBanner")
        self.total_match_limit_banner.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.total_match_limit_banner.setFixedHeight(32)
        total_limit_layout = QHBoxLayout(self.total_match_limit_banner)
        total_limit_layout.setContentsMargins(10, 5, 10, 5)
        self.total_match_limit_label = QLabel()
        self.total_match_limit_label.setObjectName("totalMatchLimitLabel")
        self.total_match_limit_label.setWordWrap(False)
        total_limit_layout.addWidget(self.total_match_limit_label)
        self.total_match_limit_banner.setVisible(False)
        self._total_match_limit_count = 0
        self.skipped_files_banner = QFrame()
        self.skipped_files_banner.setObjectName("skippedFilesBanner")
        self.skipped_files_banner.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.skipped_files_banner.setFixedHeight(32)
        skipped_banner_layout = QHBoxLayout(self.skipped_files_banner)
        skipped_banner_layout.setContentsMargins(10, 5, 6, 5)
        self.skipped_files_label = QLabel()
        self.skipped_files_label.setObjectName("skippedFilesCount")
        self.skipped_files_label.setWordWrap(False)
        skipped_banner_layout.addWidget(self.skipped_files_label)
        skipped_banner_layout.addStretch(1)
        self.skipped_files_button = QPushButton(AppStrings.SKIPPED_FILES_VIEW_BUTTON)
        self.skipped_files_button.setObjectName("skippedFilesButton")
        self.skipped_files_button.clicked.connect(self.show_skipped_files_dialog)
        skipped_banner_layout.addWidget(self.skipped_files_button)
        self.skipped_files_banner.setVisible(False)
        self.summary_row = QHBoxLayout()
        self.summary_row.setContentsMargins(0, 0, 0, 0)
        self.summary_row.setSpacing(8)
        self.summary_row.addWidget(self.summary_label, 1)
        self.summary_row.addWidget(self.total_match_limit_banner)
        self.summary_row.addWidget(self.skipped_files_banner)
        self.result_view = QTableView()
        self.result_model = SearchResultModel(self.icon_provider)
        self.proxy_model = ResultProxyModel()
        self.proxy_model.setSourceModel(self.result_model)
        self.proxy_model.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.result_view.setModel(self.proxy_model)
        self.result_delegate = HtmlDelegate(self.result_view)
        self.result_view.setItemDelegate(self.result_delegate)
        self._normal_result_row_height = self.result_view.verticalHeader().defaultSectionSize()
        self.result_model.sort_completed.connect(self._select_first_row_safely)

        # 텍스트 입력 시 즉시 필터링하지 않고 디바운스 타이머를 시작합니다.
        self.result_file_filter_edit.textChanged.connect(lambda: self._filter_timer.start())
        self.result_folder_filter_edit.textChanged.connect(lambda: self._filter_timer.start())
        self.result_view.setAlternatingRowColors(True)
        self.result_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.result_view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.result_view.verticalHeader().hide()
        # 우클릭 메뉴가 동작하도록 컨텍스트 메뉴 정책을 설정합니다.
        self.result_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header = self.result_view.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setStretchLastSection(True)
        from PySide6.QtWidgets import QHeaderView

        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)  # 매치 수
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)  # 파일명
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)  # 폴더
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)  # 기타(일부만 검색한 사유)
        header.sectionResized.connect(lambda i, _o, _n: self._save_column_widths(Constants.VIEW_RESULT))
        # 기본 정렬 표시는 검색 종료 시 정렬(매치 수 많은 순)과 같게 둡니다.
        header.setSortIndicator(0, Qt.SortOrder.DescendingOrder)
        self.result_view.setSortingEnabled(True)
        self.result_view.customContextMenuRequested.connect(self._show_result_context_menu)
        # 마우스·키보드로 현재 행이 바뀌면 상세 매치 정보를 갱신합니다. 누름 신호까지 연결하면 한 번 누를 때
        # 상세 목록을 두 번 만들므로 현재 행 변경 신호 하나만 씁니다(검수 S-4).
        self.result_view.selectionModel().currentRowChanged.connect(lambda curr, _prev: self._refresh_detail_from_selection())
        # Ctrl+A처럼 현재 행은 그대로이고 선택만 바뀌는 경우도 상세 목록에 반영합니다(7.1.24).
        self._detail_signature = None
        self._detail_refresh_timer = QTimer(self)
        self._detail_refresh_timer.setSingleShot(True)
        self._detail_refresh_timer.setInterval(0)
        self._detail_refresh_timer.timeout.connect(self._refresh_detail_from_selection)
        self.result_view.selectionModel().selectionChanged.connect(lambda *_: self._detail_refresh_timer.start())
        # 검색 중 결과가 추가되며 목록을 다시 만들 때 선택한 파일을 유지합니다(검수 U-3).
        self._reselect_path = None
        self._reselect_paths = set()
        self._result_page_changing = False
        self._result_filter_changing = False
        self.proxy_model.modelAboutToBeReset.connect(self._remember_result_selection)
        self.proxy_model.modelReset.connect(self._restore_result_selection)
        self.result_view.doubleClicked.connect(self._on_result_double_clicked)
        result_list_layout.addWidget(self.result_view)
        self.pagination_widget = self._create_pagination_widget()
        result_list_layout.addWidget(self.pagination_widget)
        self.match_area_widget = QWidget()
        match_area_layout = QVBoxLayout(self.match_area_widget)
        match_area_layout.setContentsMargins(0, 0, 0, 0)
        self.match_filter_layout = QHBoxLayout()
        self.match_filter_1_edit = QLineEdit()  # 줄 번호
        self.match_filter_2_edit = QLineEdit()  # 내용
        self.match_filter_3_edit = QLineEdit()  # 컬럼 3
        self.match_filter_4_edit = QLineEdit()  # 컬럼 4
        self.match_filter_3_edit.setVisible(False)
        self.match_filter_4_edit.setVisible(False)
        self.match_filter_1_edit.setPlaceholderText(AppStrings.MATCH_FILTER_LIST_PLACEHOLDER)
        self.match_filter_2_edit.setPlaceholderText(AppStrings.MATCH_FILTER_VALUE_PLACEHOLDER)
        self.match_filter_layout.addWidget(self.match_filter_1_edit)
        self.match_filter_layout.addWidget(self.match_filter_2_edit)
        self.match_filter_layout.addWidget(self.match_filter_3_edit)
        self.match_filter_layout.addWidget(self.match_filter_4_edit)
        self.file_info_header = QWidget()
        file_info_layout = QHBoxLayout(self.file_info_header)
        file_info_layout.setContentsMargins(0, 0, 0, 0)
        self.file_info_label = QLabel()
        self.file_info_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        file_info_layout.addWidget(self.file_info_label, 1)
        # 선택 파일 정보는 한 줄 헤더이므로 세로 방향으로 확장되지 않게 제한합니다.
        self.file_info_header.setFixedHeight(32)
        self.file_info_header.setVisible(False)
        self.match_view = QTableView()
        self.match_model = MatchDetailModel()
        self.match_model.note_lookup = lambda path: self.result_model.short_note_for(path)
        self.match_proxy_model = MatchProxyModel()
        self.match_proxy_model.setSourceModel(self.match_model)
        self.match_view.setModel(self.match_proxy_model)
        self.match_proxy_model.modelReset.connect(self._apply_group_spans)
        self.match_proxy_model.layoutChanged.connect(self._apply_group_spans)
        self.match_view.setItemDelegate(HtmlDelegate(self.match_view))
        self._normal_match_row_height = self.match_view.verticalHeader().defaultSectionSize()
        self.match_view.setFrameShape(QFrame.Shape.NoFrame)  # 디자인 일관성을 위해 매치 뷰의 프레임 테두리를 제거합니다.
        # 검색 모드에 따라 필터링 대상 컬럼이 달라지므로 전용 핸들러로 연결
        self.match_filter_1_edit.textChanged.connect(self._on_match_filter_1_changed)
        self.match_filter_2_edit.textChanged.connect(self._on_match_filter_2_changed)
        self.match_filter_3_edit.textChanged.connect(self._on_match_filter_3_changed)
        self.match_filter_4_edit.textChanged.connect(self._on_match_filter_4_changed)
        self.match_view.setAlternatingRowColors(True)
        self.match_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.match_view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.match_view.verticalHeader().hide()
        m_header = self.match_view.horizontalHeader()
        m_header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        m_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)  # 위치/라인 번호
        # 초기 Stretch 설정을 제거하여 특수 검색 모드에서도 컬럼 너비가 유연하게 조정되도록 합니다.
        # 섹션 resize 정책은 _adjust_match_column_widths가 모드별로 동적으로 설정함.
        # 대량 데이터 처리 시 성능 향상을 위해 리사이즈 계산 대상 항목 수를 제한합니다.
        if hasattr(m_header, "setResizeContentsPrecision"):
            m_header.setResizeContentsPrecision(50)
        m_header.setStretchLastSection(True)  # 마지막 컬럼이 남는 공간 채움
        m_header.sectionResized.connect(self._on_match_column_resized)
        # 열 폭 배분(7.5.4): 내용 폭은 파일을 고를 때 한 번 재고, 보이는 폭이 바뀌면 비율만 다시 나눕니다.
        self._match_natural_widths: list = []
        self._applying_match_widths = False
        self._match_user_resized = False
        self._match_layout_timer = QTimer(self)
        self._match_layout_timer.setSingleShot(True)
        self._match_layout_timer.setInterval(self.MATCH_LAYOUT_DELAY_MS)
        self._match_layout_timer.timeout.connect(self._layout_match_columns)
        self._match_column_watcher = MatchColumnWatcher(
            self.match_view, self._match_layout_timer.start, self._on_match_column_drag_finished
        )
        self.match_view.clicked.connect(self._on_match_clicked)
        self.match_view.selectionModel().currentRowChanged.connect(lambda curr, _prev: self._on_match_clicked(curr))
        self.match_view.doubleClicked.connect(self._on_match_double_clicked)
        self.match_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.match_view.customContextMenuRequested.connect(self._show_match_context_menu)
        # 세로 스플릿 핸들과 상세 결과 영역이 붙어 보이지 않도록 상단 여백을 둡니다.
        match_area_layout.setContentsMargins(0, 6, 0, 0)
        match_area_layout.setSpacing(5)
        match_area_layout.addWidget(self.file_info_header)
        match_area_layout.addLayout(self.match_filter_layout)
        self.match_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.match_splitter.addWidget(self.match_view)
        self.context_preview_container = QWidget()
        context_preview_layout = QVBoxLayout(self.context_preview_container)
        context_preview_layout.setContentsMargins(0, 0, 0, 0)
        context_preview_layout.setSpacing(3)
        self.context_preview = QPlainTextEdit()
        self.context_preview.setReadOnly(True)
        self.context_preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.context_preview.setFont(QFont("Consolas", 10))
        self.context_highlighter = LightweightSyntaxHighlighter(
            self.context_preview.document(), "text", self._is_dark_theme()
        )
        self.context_preview.setPlainText(AppStrings.CONTEXT_PREVIEW_PLACEHOLDER)
        context_preview_layout.addWidget(self.context_preview)
        self.context_line_settings_widget = QWidget()
        context_line_settings_layout = QHBoxLayout(self.context_line_settings_widget)
        context_line_settings_layout.setContentsMargins(0, 0, 0, 0)
        context_line_settings_layout.addStretch()
        context_line_settings_layout.addWidget(QLabel(AppStrings.CONTEXT_PREVIEW_BEFORE_LABEL))
        self.context_before_combo = self._create_context_line_combo(Constants.CONFIG_KEY_CONTEXT_BEFORE_LINES)
        context_line_settings_layout.addWidget(self.context_before_combo)
        context_line_settings_layout.addWidget(QLabel(AppStrings.CONTEXT_PREVIEW_LINE_UNIT))
        context_line_settings_layout.addSpacing(8)
        context_line_settings_layout.addWidget(QLabel(AppStrings.CONTEXT_PREVIEW_AFTER_LABEL))
        self.context_after_combo = self._create_context_line_combo(Constants.CONFIG_KEY_CONTEXT_AFTER_LINES)
        context_line_settings_layout.addWidget(self.context_after_combo)
        context_line_settings_layout.addWidget(QLabel(AppStrings.CONTEXT_PREVIEW_LINE_UNIT))
        context_preview_layout.addWidget(self.context_line_settings_widget)
        self.match_splitter.addWidget(self.context_preview_container)
        self.match_splitter.setStretchFactor(0, 3)
        self.match_splitter.setStretchFactor(1, 2)
        match_area_layout.addWidget(self.match_splitter)
        # 사용자 편의를 위해 페이지네이션 위젯을 테이블 하단에 배치합니다.
        self.match_pagination_widget = self._create_match_pagination_widget()
        match_area_layout.addWidget(self.match_pagination_widget)

        self.result_splitter = QSplitter(Qt.Orientation.Vertical)
        self.result_splitter.setHandleWidth(6)
        self.result_splitter.addWidget(self.result_list_container)
        self.result_splitter.addWidget(self.match_area_widget)
        self.result_splitter.setStretchFactor(0, 1)
        self.result_splitter.setStretchFactor(1, 1)
        main_layout.addLayout(self.summary_row)
        # 검색 중에만 보이는 움직이는 막대입니다. 전체 파일 수를 미리 세지 않으므로 진행률(%)은 없습니다.
        # 검색 중 문구 앞에서 도는 표시입니다. 요약 줄 안쪽 왼쪽에 겹쳐 두고, 그만큼 글자를 오른쪽으로 밉니다.
        self.search_shimmer = SearchShimmer(self.summary_label)
        self.search_spinner = SearchSpinner(self.summary_label)
        main_layout.addLayout(self.result_filter_layout)
        main_layout.addWidget(self.result_splitter, 1)
        self.empty_label = QLabel(AppStrings.RESULT_EMPTY_MSG)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setStyleSheet(UIStyles.STYLE_SELECTION_INFO)
        main_layout.insertWidget(2, self.empty_label, 1)  # 요약 줄·검색 중 막대 아래
        self.result_splitter.setVisible(False)
        self._apply_theme_style()
        self.apply_display_density()
        for i in range(self.result_filter_layout.count()):
            item = self.result_filter_layout.itemAt(i)
            if item:
                w = item.widget()
                if w:
                    w.setVisible(False)
        self._setup_copy_shortcuts()

    def _create_context_line_combo(self, config_key):
        combo = QComboBox()
        combo.addItems([str(value) for value in range(self.CONTEXT_MAX_RADIUS + 1)])
        try:
            saved_value = int(self.config_manager.get(config_key, self.CONTEXT_RADIUS))
        except (AttributeError, TypeError, ValueError):
            saved_value = self.CONTEXT_RADIUS
        saved_value = min(max(saved_value, 0), self.CONTEXT_MAX_RADIUS)
        combo.setCurrentIndex(saved_value)
        combo.setMinimumContentsLength(2)
        combo.setMinimumWidth(72)
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        combo.currentTextChanged.connect(
            lambda text, key=config_key: self._on_context_line_count_changed(key, text)
        )
        return combo

    def reload_shared_settings(self):
        """다른 탭에서 바꾼 앞뒤 줄 수 설정을 이 탭에도 반영합니다."""
        changed = False
        for combo, key in (
            (self.context_before_combo, Constants.CONFIG_KEY_CONTEXT_BEFORE_LINES),
            (self.context_after_combo, Constants.CONFIG_KEY_CONTEXT_AFTER_LINES),
        ):
            try:
                value = int(self.config_manager.get(key, self.CONTEXT_RADIUS))
            except (AttributeError, TypeError, ValueError):
                value = self.CONTEXT_RADIUS
            value = min(max(value, 0), self.CONTEXT_MAX_RADIUS)
            if combo.currentIndex() != value:
                with QSignalBlocker(combo):
                    combo.setCurrentIndex(value)
                changed = True
        if changed and self._current_match_item is not None:
            self._show_context_preview(self._current_match_item)

    def _on_context_line_count_changed(self, config_key, text):
        try:
            value = min(max(int(text), 0), self.CONTEXT_MAX_RADIUS)
        except (TypeError, ValueError):
            return
        self.config_manager.set(config_key, value)
        if self._current_match_item is not None:
            self._show_context_preview(self._current_match_item)

    def _apply_theme_style(self):
        """현재 테마에 맞게 테이블 스타일을 적용합니다."""
        is_dark = self._is_dark_theme()
        style = UIStyles.get_table_style(is_dark)
        self.result_view.setStyleSheet(style)
        self.match_view.setStyleSheet(style)
        self.summary_label.setStyleSheet(UIStyles.get_summary_label_style(is_dark))
        self.total_match_limit_banner.setStyleSheet(UIStyles.get_total_match_limit_banner_style(is_dark))
        self.skipped_files_banner.setStyleSheet(UIStyles.get_skipped_files_banner_style(is_dark))
        self.file_info_label.setStyleSheet(UIStyles.get_file_info_header_style(is_dark))
        self.context_preview.setStyleSheet(UIStyles.get_context_preview_style(is_dark))
        self.context_highlighter.set_dark_mode(is_dark)

    def _is_dark_theme(self) -> bool:
        """현재 설정된 테마가 다크 계열인지 반환합니다."""
        theme = str(self.config_manager.get_theme()).lower()
        return theme in [AppStrings.THEME_DARK.lower(), "dark", "auto"]

    def _create_pagination_widget(self):
        pagination_widget = QWidget()
        pagination_layout = QHBoxLayout(pagination_widget)
        pagination_layout.setContentsMargins(5, 5, 5, 5)
        self.prev_page_btn = QPushButton(AppStrings.PAGINATION_PREV)
        self.prev_page_btn.setEnabled(False)
        self.prev_page_btn.setMaximumWidth(80)
        self.prev_page_btn.clicked.connect(self._on_prev_page)
        self.page_info_label = QLabel(AppStrings.PAGINATION_PAGE)
        self.current_page_edit = QLineEdit("1")
        self.current_page_edit.setMaximumWidth(50)
        self.current_page_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.current_page_edit.returnPressed.connect(self._on_page_jump)
        self.total_pages_label = QLabel(f"{AppStrings.PAGINATION_OF} 1")
        self.next_page_btn = QPushButton(AppStrings.PAGINATION_NEXT)
        self.next_page_btn.setEnabled(False)
        self.next_page_btn.setMaximumWidth(80)
        self.next_page_btn.clicked.connect(self._on_next_page)
        self.page_size_combo = QComboBox()
        self.page_size_combo.addItems(
            [AppStrings.PAGINATION_SIZE_1000, AppStrings.PAGINATION_SIZE_2000, AppStrings.PAGINATION_SIZE_5000]
        )
        self.page_size_combo.setMaximumWidth(100)
        self.page_size_combo.currentIndexChanged.connect(self._on_page_size_changed)
        pagination_layout.addWidget(self.prev_page_btn)
        pagination_layout.addWidget(self.page_info_label)
        pagination_layout.addWidget(self.current_page_edit)
        pagination_layout.addWidget(self.total_pages_label)
        pagination_layout.addWidget(self.next_page_btn)
        pagination_layout.addStretch()
        pagination_layout.addWidget(QLabel(AppStrings.PAGINATION_DISPLAY))
        pagination_layout.addWidget(self.page_size_combo)
        pagination_widget.setVisible(False)
        pagination_widget.setFixedHeight(36)
        pagination_widget.setContentsMargins(0, 0, 10, 0)  # UI 균형을 위해 우측에 약간의 여백을 둡니다.
        return pagination_widget

    def _create_match_pagination_widget(self):
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(5, 5, 5, 5)

        self.match_prev_btn = QPushButton(AppStrings.PAGINATION_PREV)
        self.match_prev_btn.setEnabled(False)
        self.match_prev_btn.setMaximumWidth(80)
        self.match_prev_btn.clicked.connect(self._on_match_prev_page)

        self.match_page_info_label = QLabel(AppStrings.PAGINATION_PAGE)
        self.match_current_page_edit = QLineEdit("1")
        self.match_current_page_edit.setMaximumWidth(50)
        self.match_current_page_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.match_current_page_edit.returnPressed.connect(self._on_match_page_jump)

        self.match_total_pages_label = QLabel(f"{AppStrings.PAGINATION_OF} 1")
        self.match_next_btn = QPushButton(AppStrings.PAGINATION_NEXT)
        self.match_next_btn.setEnabled(False)
        self.match_next_btn.setMaximumWidth(80)
        self.match_next_btn.clicked.connect(self._on_match_next_page)

        self.match_page_size_combo = QComboBox()
        self.match_page_size_combo.addItems(["100", "200", "500"])
        self.match_page_size_combo.setMaximumWidth(100)
        self.match_page_size_combo.currentIndexChanged.connect(self._on_match_page_size_changed)

        layout.addWidget(self.match_prev_btn)
        layout.addWidget(self.match_page_info_label)
        layout.addWidget(self.match_current_page_edit)
        layout.addWidget(self.match_total_pages_label)
        layout.addWidget(self.match_next_btn)
        layout.addStretch()
        layout.addWidget(QLabel(AppStrings.PAGINATION_DISPLAY))
        layout.addWidget(self.match_page_size_combo)

        widget.setVisible(False)
        widget.setFixedHeight(36)
        return widget

    def _on_match_page_size_changed(self, index):
        size_str = self.match_page_size_combo.itemText(index)
        try:
            size = int(size_str)
            self.match_model.set_page_size(size)
            self._update_match_pagination_ui()
        except ValueError:
            pass

    def _on_match_page_jump(self):
        try:
            val = int(self.match_current_page_edit.text())
            total = self.match_model.get_total_pages()
            if 1 <= val <= total:
                self.match_model.go_to_page(val)
                self._update_match_pagination_ui()
            else:
                self.match_current_page_edit.setText(str(self.match_model._current_page))
        except ValueError:
            self.match_current_page_edit.setText(str(self.match_model._current_page))

    def _on_match_prev_page(self):
        curr = self.match_model._current_page
        if curr > 1:
            self.match_model.go_to_page(curr - 1)
            self._update_match_pagination_ui()

    def _on_match_next_page(self):
        curr = self.match_model._current_page
        total = self.match_model.get_total_pages()
        if curr < total:
            self.match_model.go_to_page(curr + 1)
            self._update_match_pagination_ui()

    def _update_match_pagination_ui(self):
        curr = self.match_model._current_page
        total = self.match_model.get_total_pages()
        self.match_current_page_edit.setText(str(curr))
        self.match_total_pages_label.setText(f"{AppStrings.PAGINATION_OF} {total}")
        self.match_prev_btn.setEnabled(curr > 1)
        self.match_next_btn.setEnabled(curr < total)
        # 결과가 한 페이지 이내일 경우 페이지네이션 위젯을 숨깁니다.
        self.match_pagination_widget.setVisible(total > 1)

        # 페이지 이동 후에도 내용에 맞춰 컬럼 폭을 최적화합니다.
        self._adjust_match_column_widths()

    def _change_result_page(self, change):
        """파일 목록의 페이지를 바꿉니다. 넘어간 페이지에 고른 파일이 없어도 상세 목록은 그대로 둡니다(7.6.1).

        필터로 고른 파일이 모두 숨을 때만 상세를 비웁니다(7.5.8). 페이지 이동은 숨기는 것이 아니므로 예외입니다.
        """
        self._result_page_changing = True
        try:
            change()
        finally:
            self._result_page_changing = False

    def _on_prev_page(self):
        curr = self.result_model.get_current_page()
        if curr > 1:
            self._change_result_page(lambda: self.result_model.go_to_page(curr - 1))
            self._update_pagination_ui()

    def _on_next_page(self):
        curr = self.result_model.get_current_page()
        total = self.result_model.get_total_pages()
        if curr < total:
            self._change_result_page(lambda: self.result_model.go_to_page(curr + 1))
            self._update_pagination_ui()

    def _on_page_jump(self):
        try:
            val = int(self.current_page_edit.text())
            total = self.result_model.get_total_pages()
            if 1 <= val <= total:
                self._change_result_page(lambda: self.result_model.go_to_page(val))
                self._update_pagination_ui()
            else:
                self.current_page_edit.setText(str(self.result_model.get_current_page()))
        except ValueError:
            self.current_page_edit.setText(str(self.result_model.get_current_page()))

    def _on_page_size_changed(self, index):
        size_str = self.page_size_combo.itemText(index)
        try:
            size = int(size_str)
            self._change_result_page(lambda: self.result_model.set_page_size(size))
            self._update_pagination_ui()
        except ValueError:
            pass

    def _update_pagination_ui(self):
        """결과 목록의 현재 페이지와 총 페이지 수를 UI에 갱신합니다."""
        self.result_view.setUpdatesEnabled(False)
        try:
            curr = self.result_model.get_current_page()
            total = self.result_model.get_total_pages()
            self.current_page_edit.setText(str(curr))
            self.total_pages_label.setText(f"{AppStrings.PAGINATION_OF} {total}")
            self.prev_page_btn.setEnabled(curr > 1)
            self.next_page_btn.setEnabled(curr < total)
            # 결과가 한 페이지를 초과할 때만 페이지네이션을 표시합니다.
            self.pagination_widget.setVisible(total > 1)
        finally:
            self.result_view.setUpdatesEnabled(True)

    def _path_at_proxy_index(self, index):
        item = self.result_model.get_item(self.proxy_model.mapToSource(index).row())
        return item[0] if item else None

    def _remember_result_selection(self):
        """목록을 다시 만들기 전에 보던 파일과 고른 파일 전체를 기억합니다(7.5.2: 여러 개 선택 유지)."""
        index = self.result_view.currentIndex()
        self._reselect_path = self._path_at_proxy_index(index) if index.isValid() else None
        selection = self.result_view.selectionModel()
        paths = set()
        if selection is not None:
            for selected in selection.selectedRows():
                path = self._path_at_proxy_index(selected)
                if path:
                    paths.add(path)
        self._reselect_paths = paths

    def _restore_result_selection(self):
        current_path, self._reselect_path = self._reselect_path, None
        paths, self._reselect_paths = self._reselect_paths, set()
        # 고른 파일만 되살립니다. Ctrl+클릭으로 선택을 푼 현재 위치 파일은 다시 고르지 않고 위치만 지킵니다.
        # 고른 것이 없고 현재 위치만 있는 상태는 사용자가 선택을 모두 푼 경우뿐이라, 보던 파일을 대신
        # 고르지 않습니다(7.5.8, 7.5.7 검수 3.2).
        remembered = set(paths)
        if not paths and not current_path:
            if getattr(self, "_result_filter_changing", False):
                # 페이지를 넘겨 고른 것이 없는 상태에서 필터를 바꾼 경우입니다. 상세가 보여 주던 파일이
                # 필터 결과에 하나도 없으면 상세를 비웁니다(7.6.2, 7.6.1 검수 7.4).
                self._clear_detail_if_filtered_out()
            return
        selection = self.result_view.selectionModel()
        if selection is None:
            return
        last_column = max(0, self.proxy_model.columnCount() - 1)
        restored = QItemSelection()
        restored_paths: set = set()
        current_index = None
        first_hit_index = None
        run_start = None
        row_count = self.proxy_model.rowCount()
        for row in range(row_count + 1):
            path = self._path_at_proxy_index(self.proxy_model.index(row, 0)) if row < row_count else None
            hit = path is not None and path in paths
            if path is not None and path == current_path:
                current_index = self.proxy_model.index(row, 0)
            if hit:
                if not restored_paths:
                    first_hit_index = self.proxy_model.index(row, 0)
                restored_paths.add(path)
            if hit and run_start is None:
                run_start = row
            elif not hit and run_start is not None:
                # 이어진 줄은 한 범위로 묶어 수천 개를 골라도 한 번에 되살립니다.
                restored.select(self.proxy_model.index(run_start, 0), self.proxy_model.index(row - 1, last_column))
                run_start = None
        if restored.isEmpty():
            self._restore_without_selection(selection, current_index, bool(remembered))
            return
        if current_index is None:
            # 보던 파일이 목록에서 빠졌으면 남은 선택의 첫 파일을 현재 위치로 둡니다.
            current_index = first_hit_index
        # 같은 파일들을 다시 고르는 것이므로 상세 목록을 다시 만들지 않습니다.
        selection.blockSignals(True)
        try:
            selection.select(
                restored,
                QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows,
            )
            if current_index is not None:
                selection.setCurrentIndex(current_index, QItemSelectionModel.SelectionFlag.NoUpdate)
        finally:
            selection.blockSignals(False)
        self.result_view.viewport().update()
        if remembered and restored_paths != remembered:
            # 파일 필터 등으로 고른 파일 일부가 목록에서 빠졌습니다. 신호를 막아 두었으므로
            # 상세 목록을 남은 선택에 맞춰 직접 다시 만듭니다(7.5.7, 검수 3).
            self._detail_refresh_timer.start()

    def _restore_without_selection(self, selection, current_index, had_selection: bool):
        """되살릴 선택이 없을 때 목록과 상세를 맞춥니다(7.5.8, 7.5.7 검수 3.1·3.2).

        - 현재 위치 파일이 목록에 있으면 고르지 않고 위치만 지킵니다. 숨은 파일들의 상세를 보던 중이었다면
          선택 없음 상태(현재 위치 파일)에 맞춰 상세를 다시 만듭니다.
        - 현재 위치 파일도 목록에 없으면(예: 고른 파일이 필터로 모두 숨음) 상세를 비웁니다.
        """
        if current_index is not None:
            selection.blockSignals(True)
            try:
                selection.setCurrentIndex(current_index, QItemSelectionModel.SelectionFlag.NoUpdate)
            finally:
                selection.blockSignals(False)
            self.result_view.viewport().update()
            if had_selection:
                # 숨은 파일들의 상세를 보던 중이었으므로 현재 위치 파일의 상세로 바꿉니다.
                self._on_result_clicked(current_index)
            return
        if getattr(self, "_result_page_changing", False):
            # 다른 페이지로 넘어갔을 뿐이므로 보던 상세 목록을 그대로 둡니다(7.6.1).
            return
        self._clear_detail_view()

    def _clear_detail_if_filtered_out(self):
        """상세가 보여 주는 파일이 필터 결과(모든 페이지)에 하나도 없으면 상세를 비웁니다."""
        shown = set(self._detail_signature or ())
        if not shown:
            return
        visible = {item[3] for item in self.result_model._filtered_buffer if len(item) > 3}
        if not shown & visible:
            self._clear_detail_view()

    def _user_cleared_selection(self) -> bool:
        """사용자가 Ctrl+클릭으로 선택을 모두 푼 상태인지 봅니다.

        고른 것은 없지만 현재 위치가 있고 상세가 그 파일을 보여 주고 있으면 그렇습니다. 이때는 검색 종료·정렬 완료
        때도 첫 파일을 대신 고르지 않습니다(7.5.8, 7.5.7 검수 3.2). 새 검색은 clear()로 상세가 비어 해당하지 않습니다.
        """
        selection = self.result_view.selectionModel()
        if selection is not None and selection.hasSelection():
            return False
        return self.result_view.currentIndex().isValid() and self._detail_signature is not None

    def _clear_detail_view(self):
        """오른쪽 상세 목록·파일 정보 줄·미리보기를 비웁니다. 파일 목록과 결과는 그대로 둡니다."""
        self.selected_file_path = ""
        self._detail_signature = None
        self.file_info_label.clear()
        self.file_info_header.setVisible(False)
        self._clear_context_preview()
        self.match_model.clear()
        self.match_pagination_widget.setVisible(False)

    def _selected_result_entries(self):
        """선택한 파일들의 (경로, 결과)를 파일 목록에 보이는 순서대로 돌려줍니다."""
        selection = self.result_view.selectionModel()
        if selection is None:
            return []
        rows = sorted({index.row() for index in selection.selectedRows()})
        entries = []
        for row in rows:
            item = self.result_model.get_item(self.proxy_model.mapToSource(self.proxy_model.index(row, 0)).row())
            if item:
                entries.append((item[0], item[2]))
        return entries

    def _refresh_detail_from_selection(self):
        """파일 목록의 선택에 맞춰 상세 목록을 만듭니다. 여러 파일이면 결과를 합쳐 보여 줍니다(7.1.24)."""
        entries = self._selected_result_entries()
        if not entries and self._detail_signature is not None:
            # Ctrl+클릭으로 선택을 모두 풀면 직전에 보던 상세를 그대로 둡니다. 현재 위치(먼저 푼 파일)로
            # 되돌아가지 않습니다(7.6.2, 7.6.1 검수 7.4).
            return
        if len(entries) == 1 and self._detail_entry_path(self.result_view.currentIndex()) != entries[0][0]:
            # 여러 개 중 Ctrl+클릭으로 풀어 하나만 남으면, 방금 푼 현재 위치 파일이 아니라 남은 파일을 보여 줍니다(7.6.1).
            if (entries[0][0],) != self._detail_signature:
                self._show_detail_entries(entries)
            return
        if len(entries) <= 1:
            current = self.result_view.currentIndex()
            if not current.isValid():
                return
            item = self.result_model.get_item(self.proxy_model.mapToSource(current).row())
            signature = (item[0],) if item else None
            if signature is not None and signature == self._detail_signature:
                return
            self._on_result_clicked(current)
            return
        signature = tuple(path for path, _ in entries)
        if signature == self._detail_signature:
            return
        self._show_detail_entries(entries)

    def _detail_entry_path(self, index):
        if not index.isValid():
            return None
        return self._path_at_proxy_index(index)

    def _on_result_clicked(self, index):
        if not index.isValid():
            return
        real_index = self.proxy_model.mapToSource(index)
        item = self.result_model.get_item(real_index.row())
        if item:
            self._show_detail_entries([(item[0], item[2])])

    def _show_detail_entries(self, entries):
        if entries:
            path = entries[0][0]
            self._detail_signature = tuple(entry[0] for entry in entries)
            self.selected_file_path = path
            self.match_model.set_files(entries, self.search_text, self.search_mode)
            if len(entries) > 1:
                info = AppStrings.SELECTED_FILES_INFO_TEMPLATE.format(len(entries), f"{self.match_model.match_count:,}")
            else:
                info = AppStrings.SELECTED_FILE_INFO_TEMPLATE.format(path, self.match_model.match_count)
            self.file_info_label.setText(info)
            self.file_info_header.setVisible(True)
            self._clear_context_preview()

            # 모델 데이터 변경 후 헤더 섹션 정보가 즉시 갱신되도록 뷰를 리셋합니다.
            # (headerDataChanged 시그널만으로는 뷰가 갱신되지 않는 경우가 있음)
            self.match_view.reset()

            self.update_match_filter_visibility(self.search_mode)
            self._update_match_pagination_ui()

            # 파일 선택 시 내용에 맞춰 상세 뷰의 컬럼 너비를 단계적으로 조정합니다.
            self._adjust_match_column_widths()
            QTimer.singleShot(100, self._adjust_match_column_widths)

            # 상세 뷰 로드 시 첫 번째 행을 자동으로 선택하여 정보를 즉시 제공합니다.
            if self.match_model.match_count > 0:
                QTimer.singleShot(0, self._select_first_match_row)

    def _select_first_match_row(self):
        row = self.match_model.first_match_row()
        if row >= 0:
            self.match_view.selectRow(row)

    def _apply_group_spans(self):
        """파일 머리줄이 표 전체 폭을 차지하고, 위쪽 여백·윗선 몫만큼 높게 보이게 합니다(7.1.26~7.1.27).

        지금 페이지의 행만 봅니다(행 높이를 내용에 맞춰 다시 재는 계산은 쓰지 않음).
        """
        from ui.widgets import GROUP_HEADER_GAP, GROUP_HEADER_LINE

        view = self.match_view
        view.clearSpans()
        columns = self.match_proxy_model.columnCount()
        header_height = view.verticalHeader().defaultSectionSize() + GROUP_HEADER_GAP + GROUP_HEADER_LINE
        for row in range(self.match_proxy_model.rowCount()):
            source_row = self.match_proxy_model.mapToSource(self.match_proxy_model.index(row, 0)).row()
            if self.match_model.is_group_row(source_row):
                if columns > 1:
                    view.setSpan(row, 0, 1, columns)
                view.setRowHeight(row, header_height)

    def _on_match_column_resized(self, index, _old_width, _new_width):
        """상세 뷰의 컬럼 크기가 변경될 때 호출되는 로그 핸들러"""
        if self._applying_match_widths:
            return
        watcher = getattr(self, "_match_column_watcher", None)
        if self._is_multi_column_match_mode() and watcher is not None and watcher.header_pressed:
            # 사용자가 머리글에서 열 경계를 끌고 있습니다. 놓으면 비율을 기억하고 표 폭에 다시 맞춥니다.
            # 프로그램이 바꾼 폭이나 머리글을 누르기만 한 경우(정렬)는 비율로 기억하지 않습니다(7.5.6, 검수 3.1).
            self._match_user_resized = True
        # Stretch 모드는 레이아웃 엔진에 의해 폭이 자동 결정되므로 저장 대상에서 제외 (진동 루프 방지)
        header = self.match_view.horizontalHeader()
        if index < header.count() and header.sectionResizeMode(index) == QHeaderView.ResizeMode.Stretch:
            return

        # 설정 저장 병행
        self._save_column_widths(Constants.VIEW_MATCH)

    def _on_result_double_clicked(self, index):
        if not index.isValid():
            return
        real_index = self.proxy_model.mapToSource(index)
        item = self.result_model.get_item(real_index.row())
        if item:
            path = item[0]
            self.file_double_clicked.emit(path, 0)

    def _on_match_clicked(self, index):
        if not index.isValid():
            self._clear_context_preview()
            return
        # 항목 선택 시 필요한 내부 상태 업데이트를 수행합니다.
        real_index = self.match_proxy_model.mapToSource(index)
        match_item = self.match_model.get_match(real_index.row())
        if match_item is None:
            self._clear_context_preview()
            return
        # 여러 파일을 합쳐 볼 때 미리보기·열기는 고른 줄이 나온 파일을 대상으로 합니다.
        source_path = getattr(match_item, "source_path", None)
        if source_path:
            self.match_model.current_file_path = source_path
            self.selected_file_path = source_path
        self._show_context_preview(match_item)

    def _clear_context_preview(self):
        """문맥 미리보기 영역을 초기 상태로 되돌립니다."""
        self._cancel_context_preview()
        self._current_match_item = None
        self._set_context_line_settings_visible(not (self._is_excel_preview() or self._is_archive_preview()))
        self.context_highlighter.set_language("text")
        self.context_preview.setPlainText(AppStrings.CONTEXT_PREVIEW_PLACEHOLDER)
        self.context_preview.setExtraSelections([])

    def _is_excel_preview(self):
        """검색 모드 또는 파일 확장자로 Excel 전용 미리보기 여부를 판별합니다."""
        modes = (self.search_mode, getattr(self.match_model, "search_mode", None))
        if any(sm.kind(mode) == sm.KIND_EXCEL for mode in modes):
            return True
        file_path = self.selected_file_path or getattr(self.match_model, "current_file_path", "")
        return str(file_path or "").lower().endswith(self.EXCEL_PREVIEW_EXTENSIONS)

    def _is_archive_preview(self):
        """UE 현지화 archive 결과는 위·아래 문맥 대신 검색한 항목의 값을 보여줍니다."""
        modes = (self.search_mode, getattr(self.match_model, "search_mode", None))
        return any(sm.kind(mode) == sm.KIND_ARCHIVE for mode in modes)

    def _show_archive_context_preview(self, match_item):
        """검색할 때 확보한 줄 번호·Namespace·Key·원문·번역문만 사용해 파일을 다시 읽지 않고 표시합니다."""
        values = []
        for value in (match_item.position, match_item.content, match_item.extra_1, match_item.extra_2,
                      match_item.extra_3):
            text = "" if value is None else str(value)
            if len(text) > self.CONTEXT_MAX_LINE_CHARS:
                text = text[: self.CONTEXT_MAX_LINE_CHARS] + "..."
            values.append(text.replace("\r\n", "\n").replace("\r", "\n"))
        self.context_highlighter.set_language("text")
        self.context_preview.setPlainText(AppStrings.CONTEXT_PREVIEW_ARCHIVE_MATCH.format(*values))
        self.context_preview.setExtraSelections([])

    def _set_context_line_settings_visible(self, visible):
        self.context_line_settings_widget.setVisible(bool(visible))

    @staticmethod
    def _unpack_excel_preview_fields(sheet, cell, value):
        """일반 검색 Excel 결과의 ``시트\t셀\t값`` 묶음을 미리보기용으로 나눕니다."""
        packed = None
        if "\t" in sheet:
            packed = sheet
        elif value is None and "\t" in cell:
            packed = cell

        if packed is None:
            return sheet, cell, value

        parts = packed.split("\t", 2)
        if len(parts) != 3 or not parts[0].strip() or not parts[1].strip():
            return sheet, cell, value
        return parts[0].strip(), parts[1].strip(), parts[2]

    def _show_excel_context_preview(self, match_item):
        """검색 시 확보한 셀 정보만 사용하여 Excel 파일 재읽기 없이 표시합니다."""
        sheet = str(getattr(match_item, "position", match_item[0]))
        cell = str(getattr(match_item, "content", match_item[1]))
        value = getattr(match_item, "extra_1", None)
        sheet, cell, value = self._unpack_excel_preview_fields(sheet, cell, value)
        value = "" if value is None else str(value)
        truncated = len(value) > self.CONTEXT_MAX_LINE_CHARS
        if truncated:
            value = value[: self.CONTEXT_MAX_LINE_CHARS]
        value = value.replace("\r\n", "\n").replace("\r", "\n")

        preview = AppStrings.CONTEXT_PREVIEW_EXCEL_MATCH.format(sheet, cell, value)
        if truncated:
            preview += AppStrings.CONTEXT_PREVIEW_EXCEL_VALUE_TRUNCATED
        self.context_highlighter.set_language("text")
        self.context_preview.setPlainText(preview)
        self.context_preview.setExtraSelections([])

    def _cancel_context_preview(self):
        if self._context_cancel_event is not None:
            self._context_cancel_event.set()
        self._context_request_id += 1

    def _on_context_preview_finished(self, request_id, lines, error, cancelled):
        worker = next((item for item in self._context_workers if item.request_id == request_id), None)
        if worker is not None:
            self._context_workers.remove(worker)
        if request_id != self._context_request_id or cancelled:
            return

        if error is not None:
            self.context_highlighter.set_language("text")
            if isinstance(error, FileNotFoundError):
                message = AppStrings.CONTEXT_PREVIEW_FILE_UNAVAILABLE
            else:
                message = AppStrings.CONTEXT_PREVIEW_READ_FAILED.format(error)
            self.context_preview.setPlainText(message)
            self.context_preview.setExtraSelections([])
            return
        if lines is None:
            self.context_highlighter.set_language("text")
            self.context_preview.setPlainText(AppStrings.CONTEXT_PREVIEW_SCAN_LIMIT)
            self.context_preview.setExtraSelections([])
            return

        self.context_highlighter.set_file_path(self.match_model.current_file_path)
        rendered = []
        target_block = 0
        truncated = False
        target_line = int(self._context_target_line)
        for block, (line_number, line) in enumerate(lines):
            if len(line) > self.CONTEXT_MAX_LINE_CHARS:
                # Center the bounded preview on the selected match instead of
                # always showing the beginning of a minified/long line.
                # 검색과 같은 비교 기준(엔진의 key())으로 일치 위치를 찾습니다. 분해형 글자처럼
                # 새 기준으로만 성립하는 일치도 미리보기에 보이게 합니다(검수 R-3).
                from core.search_engine import find_key_match_char

                query = str(self.search_text or "")
                # 선택한 상세 행의 내용(원문 일부)을 단서로, 같은 줄의 여러 일치 중 그 위치를 보여 줍니다.
                hint = str(getattr(self, "_context_match_content", "") or "").strip(".").strip()
                hint_pos = line.find(hint) if hint else -1
                found = find_key_match_char(line, query, max(0, hint_pos))
                if found is None and hint_pos > 0:
                    found = find_key_match_char(line, query, 0)
                match_start = found if found is not None else 0
                start = max(0, match_start - self.CONTEXT_TRUNCATED_BEFORE_CHARS)
                end = min(len(line), match_start + self.CONTEXT_TRUNCATED_AFTER_CHARS)
                line = ("..." if start else "") + line[start:end] + ("..." if end < len(line) else "")
                truncated = True
            marker = "▶" if line_number == target_line else " "
            if line_number == target_line:
                target_block = block
            rendered.append(f"{marker}{line_number:>6} | {line}")
        text = "\n".join(rendered)
        if truncated:
            text += AppStrings.CONTEXT_PREVIEW_TRUNCATED
        self.context_preview.setPlainText(text)
        selection = QTextEdit.ExtraSelection()
        selection.cursor = QTextCursor(self.context_preview.document().findBlockByLineNumber(target_block))  # type: ignore[attr-defined]
        selection.format = QTextCharFormat()  # type: ignore[attr-defined]
        selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)  # type: ignore[attr-defined]
        selection.format.setBackground(QColor("#355C7D" if self._is_dark_theme() else "#FFF2CC"))  # type: ignore[attr-defined]
        self.context_preview.setExtraSelections([selection])

    def _show_context_preview(self, match_item):
        """선택된 매치의 일반 텍스트 문맥을 안전하게 표시합니다."""
        self._cancel_context_preview()
        self._current_match_item = match_item
        if self._is_archive_preview():
            self._set_context_line_settings_visible(False)
            self._show_archive_context_preview(match_item)
            return
        if self._is_excel_preview():
            self._set_context_line_settings_visible(False)
            self._show_excel_context_preview(match_item)
            return

        self._set_context_line_settings_visible(True)
        position = str(getattr(match_item, "position", match_item[0]))
        content = str(getattr(match_item, "content", match_item[1]))
        self._context_match_content = content
        try:
            target_line = int(position)
        except (TypeError, ValueError):
            self.context_highlighter.set_language("text")
            self.context_preview.setPlainText(AppStrings.CONTEXT_PREVIEW_SPECIAL_MATCH.format(position, content))
            self.context_preview.setExtraSelections([])
            return
        if target_line <= 0:
            self.context_highlighter.set_language("text")
            self.context_preview.setPlainText(AppStrings.CONTEXT_PREVIEW_SPECIAL_MATCH.format(position, content))
            self.context_preview.setExtraSelections([])
            return

        file_path = self.match_model.current_file_path
        if not file_path:
            self.context_highlighter.set_language("text")
            self.context_preview.setPlainText(AppStrings.CONTEXT_PREVIEW_FILE_UNAVAILABLE)
            self.context_preview.setExtraSelections([])
            return
        try:
            before_lines = min(max(int(self.context_before_combo.currentText()), 0), self.CONTEXT_MAX_RADIUS)
        except (AttributeError, TypeError, ValueError):
            before_lines = self.CONTEXT_RADIUS
        try:
            after_lines = min(max(int(self.context_after_combo.currentText()), 0), self.CONTEXT_MAX_RADIUS)
        except (AttributeError, TypeError, ValueError):
            after_lines = self.CONTEXT_RADIUS

        self._context_target_line = target_line
        self.context_preview.setPlainText(AppStrings.CONTEXT_PREVIEW_LOADING)
        cancel_event = threading.Event()
        self._context_cancel_event = cancel_event
        request_id = self._context_request_id
        worker = ContextPreviewWorker(
            request_id, file_path, target_line, before_lines, after_lines, cancel_event
        )
        worker.signals.finished.connect(self._on_context_preview_finished)
        self._context_workers.append(worker)
        self._context_thread_pool.start(worker)

    def _on_match_double_clicked(self, index):
        if not index.isValid():
            return
        self._emit_match_open(index)

    def _emit_match_open(self, index):
        """선택된 매치의 파일과 줄 번호를 외부 편집기 연결부로 전달합니다."""
        if not index.isValid():
            return
        real_index = self.match_proxy_model.mapToSource(index)
        match_item = self.match_model.get_match(real_index.row())
        source_path = getattr(match_item, "source_path", None) if match_item is not None else None
        if source_path:
            line = 0
            try:
                line = int(match_item[0])
            except (ValueError, TypeError, IndexError):
                pass
            self.match_double_clicked.emit(source_path, line)
            return
        current_idx = self.result_view.currentIndex()
        if current_idx.isValid():
            real_src_idx = self.proxy_model.mapToSource(current_idx)
            item = self.result_model.get_item(real_src_idx.row())
            if item:
                path = item[0]
                line = 0
                try:
                    line = int(match_item[0])
                except (ValueError, TypeError, IndexError):
                    pass
                self.match_double_clicked.emit(path, line)

    def add_results(self, results):
        if not results:
            return
        was_empty = self.result_model.rowCount() == 0
        self.result_model.add_results(results)
        if was_empty and self.result_model.rowCount() > 0:
            self.update_ui_visibility()
            # 첫 번째 검색 결과가 발견되면 자동으로 선택하여 상세 정보를 표시합니다.
            QTimer.singleShot(0, self._select_first_row_safely)

    def _select_first_row_safely(self, force=False):
        """첫 번째 결과 항목을 명시적으로 선택하고 클릭 이벤트를 강제로 발생시킵니다."""
        try:
            if getattr(self, "result_view", None) is None:
                return
            if self.result_view.model() is not None and self.result_model.rowCount() > 0:
                selection_model = self.result_view.selectionModel()
                if selection_model and selection_model.hasSelection() and not force:
                    return
                if not force and self._user_cleared_selection():
                    return

                if selection_model:
                    selection_model.clearSelection()
                self.result_view.selectRow(0)

                # currentRowChanged만으로는 충분치 않을 수 있어 클릭 이벤트를 직접 트리거
                index = self.proxy_model.index(0, 0)
                if index.isValid():
                    self._on_result_clicked(index)
        except RuntimeError:
            # QTimer 이벤트가 UI 객체 파괴 후에 실행될 때 발생하는 예외 무시
            pass

    def sort_results(self):
        """검색 종료 시 호출되어 전체 결과를 정렬합니다 (비동기)."""
        self.result_model.sort_results()
        # 정렬 완료 후 지연 타이머를 통해 페이지네이션 UI를 한 번만 갱신합니다.
        QTimer.singleShot(300, self._update_pagination_ui)
        self.update_ui_visibility()

    SPINNER_TEXT_INDENT = 20

    def _start_spinner(self):
        self.search_spinner.set_color("#D69E2E" if self._is_dark_theme() else "#C47B00")
        y = max(0, (self.summary_label.height() - self.search_spinner.height()) // 2)
        self.search_spinner.move(12, y)
        self.search_shimmer.set_color(QColor(240, 196, 106, 60) if self._is_dark_theme() else QColor(239, 159, 39, 80))
        self.search_shimmer.start()
        self.search_spinner.start()
        self.search_spinner.raise_()

    def _stop_spinner(self):
        self.search_spinner.stop()
        self.search_shimmer.stop()

    def set_summary_info(self, file_count, match_count, duration, skip_count=0, state_prefix="", scanned_count=None):
        """상단 요약 정보를 업데이트합니다. 검색 중에는 확인한 파일 수·경과 시간을 보여 주고 요약 줄에 빛이 흐르게 합니다."""
        # 요약 색은 문구 일부가 아니라 정해진 상태 접두어와 같은지로 정합니다(검수 D-4).
        state = {
            AppStrings.SUMMARY_PREFIX_SEARCHING: "searching",
            AppStrings.SUMMARY_PREFIX_STOPPED: "stopped",
            AppStrings.SUMMARY_PREFIX_FAILED: "stopped",
        }.get(state_prefix, "finished")
        if state == "searching":
            seconds = max(0, int(duration))
            summary_text = AppStrings.RESULT_SUMMARY_SEARCHING_FORMAT.format(
                scanned_count=f"{max(0, int(scanned_count or 0)):,}",
                file_count=f"{file_count:,}",
                match_count=f"{match_count:,}",
                elapsed=f"{seconds // 60:02d}:{seconds % 60:02d}",
            )
            self.summary_label.setStyleSheet(
                UIStyles.get_summary_label_style(self._is_dark_theme(), state)
                + f" padding-left: {self.SPINNER_TEXT_INDENT + 10}px;"
            )
            self.summary_label.setText(state_prefix + summary_text)
            self.summary_label.setVisible(True)
            self._start_spinner()
            return
        self._stop_spinner()
        if file_count > 0 or skip_count > 0 or state_prefix == AppStrings.SUMMARY_PREFIX_FAILED:
            summary_text = AppStrings.RESULT_SUMMARY_FORMAT.format(
                file_count=file_count,
                match_count=match_count,
                skip_count=skip_count,
                duration=f"{duration:.2f}",
            )
            self.summary_label.setStyleSheet(
                UIStyles.get_summary_label_style(self._is_dark_theme(), state)
            )
            self.summary_label.setText(state_prefix + summary_text)
            self.summary_label.setVisible(True)
        else:
            self.summary_label.setText("")
            self.summary_label.setVisible(False)

    def set_skipped_files(self, skipped_files, total_count=None):
        """상단 스킵 배너와 팝업에서 사용할 파일 목록을 갱신합니다."""
        self._skipped_files = normalize_skipped_files(skipped_files)
        # 결과가 있는 파일의 사유는 파일 목록 기타 칸에 보입니다(7.1.24).
        notes: dict = {}
        for skipped_path, reason in self._skipped_files:
            if skipped_path and reason:
                notes[skipped_path] = f"{notes[skipped_path]}\n{reason}" if skipped_path in notes else str(reason)
        self.result_model.set_file_notes(notes)
        requested_count = len(self._skipped_files) if total_count is None else int(total_count or 0)
        self._skipped_file_count = max(requested_count, len(self._skipped_files), 0)
        self.skipped_files_label.setText(_skipped_count_text(self._skipped_files, self._skipped_file_count))
        has_walk_errors = any(_is_walk_failure(path, reason) for path, reason in self._skipped_files)
        self.skipped_files_button.setText(AppStrings.SKIPPED_ITEMS_VIEW_BUTTON if has_walk_errors else AppStrings.SKIPPED_FILES_VIEW_BUTTON)
        self.skipped_files_banner.setVisible(self._skipped_file_count > 0)

    def set_total_match_limit_reached(self, limit_count=0):
        """Show a dedicated banner when the configured global result limit is reached."""
        try:
            self._total_match_limit_count = max(0, int(limit_count or 0))
        except (TypeError, ValueError):
            self._total_match_limit_count = 0
        self.total_match_limit_banner.setVisible(self._total_match_limit_count > 0)
        if self._total_match_limit_count:
            self.total_match_limit_label.setText(
                AppStrings.TOTAL_MATCH_LIMIT_REACHED.format(self._total_match_limit_count)
            )

    def show_skipped_files_dialog(self):
        """건너뛴 파일 경로와 사유를 복사 가능한 팝업으로 표시합니다."""
        if self._skipped_file_count <= 0:
            return
        dialog = SkippedFilesDialog(self._skipped_files, self._skipped_file_count, self)
        dialog.exec()

    def set_searching_state(self, is_searching: bool):
        """검색 상태에 따른 UI 제어를 수행합니다. (검색 중에는 실시간 정렬 오버헤드를 막기 위해 정렬을 끕니다)"""
        # 검색 도중 데이터 유입으로 인한 UI 흔들림과 성능 저하를 방지하기 위해 정렬 기능을 일시적으로 끕니다.
        self.result_view.setSortingEnabled(not is_searching)

    def clear(self):
        # [UI/UX] 검색 시작 시 이전 검색의 요약 정보를 숨깁니다.
        self._stop_spinner()
        self.summary_label.setText("")
        self.summary_label.setVisible(False)
        self.set_total_match_limit_reached(0)
        self.set_skipped_files([], total_count=0)
        self.selected_file_path = ""
        self._detail_signature = None
        self.file_info_label.clear()
        self.file_info_header.setVisible(False)
        self._clear_context_preview()
        self.result_model.clear()
        self.match_model.clear()
        self.update_ui_visibility()

    def update_ui_visibility(self):
        has_results = self.result_model.rowCount() > 0
        self.empty_label.setVisible(not has_results)
        self.result_splitter.setVisible(has_results)
        self.match_area_widget.setVisible(has_results)

        for i in range(self.result_filter_layout.count()):
            item = self.result_filter_layout.itemAt(i)
            if item:
                w = item.widget()
                if w:
                    w.setVisible(has_results)

    NO_RESULT_FOLDER_LIMIT = 3

    def show_no_result_conditions(self, search_text, mode_label, folders, extensions, filenames, mode_extensions=None):
        """결과가 없을 때 이번 검색에 쓴 조건(모드·폴더·확장자·파일명)을 나열합니다.

        파일명 필터는 이름에 포함되는지로 비교하므로 입력한 그대로 보여 줍니다.
        """
        dark = self._is_dark_theme()
        muted = "#8FB0CC" if dark else "#5A7896"
        warn = "#F0C46A" if dark else "#B26B00"
        esc = html.escape

        def row(label, value_html):
            return (
                f'<tr><td style="color:{muted}; padding:2px 16px 2px 0;" valign="top">'
                f'{esc(label)}</td><td style="padding:2px 0;">{value_html}</td></tr>'
            )

        not_used = f'<span style="color:{muted};">{esc(AppStrings.RESULT_COND_NOT_USED)}</span>'
        is_default = mode_extensions is None
        mode_html = esc(mode_label) if is_default else f'<span style="color:{warn};">{esc(mode_label)}</span>'
        shown = list(folders[: self.NO_RESULT_FOLDER_LIMIT])
        folder_html = "<br>".join(esc(folder) for folder in shown) or "-"
        if len(folders) > len(shown):
            more = AppStrings.RESULT_COND_MORE_FOLDERS.format(len(folders) - len(shown))
            folder_html += f'<br><span style="color:{muted};">{esc(more)}</span>'
        if is_default:
            ext_html = esc(", ".join(extensions)) if extensions else not_used
        else:
            scope = ", ".join("." + ext.lstrip(".") for ext in mode_extensions)
            ext_html = f'<span style="color:{muted};">{esc(AppStrings.RESULT_COND_MODE_EXTENSIONS.format(scope))}</span>'
        name_html = esc(", ".join(filenames)) if filenames else not_used
        rows = "".join((
            row(AppStrings.RESULT_COND_MODE, mode_html),
            row(AppStrings.RESULT_COND_FOLDERS.format(len(folders)), folder_html),
            row(AppStrings.RESULT_COND_EXTENSIONS, ext_html),
            row(AppStrings.RESULT_COND_FILENAMES, name_html),
        ))
        text = (
            f'<div align="center">{esc(AppStrings.RESULT_EMPTY_NO_MATCH_TITLE.format(search_text))}</div><br>'
            '<table align="center" style="font-size:13px; font-weight:400;">'
            f'<tr><td colspan="2" style="color:{muted}; padding-bottom:4px;">{esc(AppStrings.RESULT_CONDITIONS_TITLE)}</td></tr>'
            f"{rows}</table><br>"
            f'<div align="center" style="font-size:13px; font-weight:400;">{esc(AppStrings.RESULT_EMPTY_ENCODING_HINT)}</div>'
        )
        self.show_empty_message(text, emphasized=True)
        self.empty_label.setTextFormat(Qt.TextFormat.RichText)

    def show_empty_message(self, text, is_error=False, emphasized=False, plain=False):
        # 검색어처럼 사용자가 입력한 글이 들어가면 plain=True로 서식 해석 없이 글자 그대로 보여 줍니다.
        self.empty_label.setTextFormat(Qt.TextFormat.PlainText if plain else Qt.TextFormat.AutoText)
        self.empty_label.setText(text)
        if is_error:
            self.empty_label.setStyleSheet(UIStyles.STYLE_EMPTY_LABEL.format(Constants.COLOR_RED))
        elif emphasized:
            self.empty_label.setStyleSheet(UIStyles.get_empty_message_style(self._is_dark_theme()))
        else:
            self.empty_label.setStyleSheet(UIStyles.STYLE_SELECTION_INFO)
        self.empty_label.setVisible(True)
        self.result_splitter.setVisible(False)
        for i in range(self.result_filter_layout.count()):
            item = self.result_filter_layout.itemAt(i)
            if item:
                w = item.widget()
                if w:
                    w.setVisible(False)

    def set_filename_filters(self, filters):
        self.result_model.set_filename_filters(filters)

    def set_search_context(self, search_text, search_mode):
        self.search_text = search_text
        # 검색 모드는 고정 값으로 맞춰 둡니다(core/search_mode.py).
        self.search_mode = sm.normalize(search_mode)
        no_context_mode = sm.kind(self.search_mode) in (sm.KIND_EXCEL, sm.KIND_ARCHIVE)
        self._set_context_line_settings_visible(not no_context_mode)
        self.update_ui_visibility()

    def set_results(self, results):
        """세션 로드 등 대량의 결과를 한꺼번에 설정할 때 사용합니다."""
        self.clear()
        if results:
            self.result_model.add_results(results)
            self.update_ui_visibility()
            QTimer.singleShot(0, self._select_first_row_safely)
        self._update_pagination_ui()

    def apply_display_density(self):
        """공통 표시 설정을 두 표에 적용하며 문맥 미리보기는 유지합니다."""
        self._apply_group_spans()
        compact = self.config_manager.get(Constants.CONFIG_KEY_COMPACT_RESULT_ROWS, True) is True
        for view, normal_height in (
            (self.result_view, self._normal_result_row_height),
            (self.match_view, self._normal_match_row_height),
        ):
            delegate = view.itemDelegate()
            if isinstance(delegate, HtmlDelegate):
                delegate.compact = compact
            height = max(22, view.fontMetrics().height() + 6) if compact else normal_height
            view.verticalHeader().setDefaultSectionSize(height)
            view.viewport().update()

    def save_state(self):
        self.config_manager.set_splitter_states(None, self.result_splitter.saveState(), None)
        self._save_column_widths(Constants.VIEW_RESULT)
        self._save_column_widths(Constants.VIEW_MATCH)

    def restore_shared_layout(self):
        self._restore_states()

    def _restore_states(self):
        _, result_state, _ = self.config_manager.get_splitter_states()
        if result_state:
            try:
                if not self.result_splitter.restoreState(QByteArray.fromHex(result_state.encode())):
                    logger.debug(AppStrings.LOG_UI_RESULT_LAYOUT_DEFAULT)
            except Exception:
                # 손상된 저장값은 현재 기본 배치를 유지하며 검색 데이터에는 영향을 주지 않습니다.
                logger.debug(AppStrings.LOG_UI_RESULT_LAYOUT_DEFAULT, exc_info=True)
        self._restore_column_widths(Constants.VIEW_RESULT)
        self._restore_column_widths(Constants.VIEW_MATCH)

    def _save_column_widths(self, view_type):
        if view_type == Constants.VIEW_RESULT:
            widths = [self.result_view.columnWidth(i) for i in range(self.result_model.columnCount())]
            self.config_manager.save_column_widths(Constants.VIEW_RESULT, widths)
        else:
            widths = [self.match_view.columnWidth(i) for i in range(self.match_model.columnCount())]
            self.config_manager.save_column_widths(Constants.VIEW_MATCH, widths)

    def _restore_column_widths(self, view_type):
        if view_type == Constants.VIEW_MATCH:
            # 검색 상세는 저장된 픽셀 폭을 쓰지 않습니다. 탭마다 검색 방식(열 수)이 달라 다른 탭의 폭을
            # 덮어쓰면 열이 어긋나고 가로 스크롤이 생깁니다. 현재 표 폭과 직접 끈 비율로 다시 나눕니다(7.5.7).
            # 한 열인 일반 모드는 늘 표 폭을 채우므로 따로 할 일이 없습니다.
            self._layout_match_columns()
            return
        widths = self.config_manager.get_column_widths(view_type)
        if not widths:
            return
        target_view = self.result_view
        for i, w in enumerate(widths):
            target_view.setColumnWidth(i, w)

    def _get_match_filter_col(self, edit_idx: int) -> int:
        """
        현재 검색 모드에 따라 필터 입력 필드(0~3)가 대응되는 모델 컬럼 인덱스를 반환합니다.
        
        대부분의 특수 모드(XML, JSON)는 0번 컬럼이 '라인 번호'이므로
        필터 입력은 데이터 컬럼인 1번부터 매핑되어야 합니다.
        """
        mode = self.search_mode or Constants.MODE_NORMAL
        
        # Normal 모드와 Excel 모드는 0번 컬럼부터 데이터 필터링 대상
        if mode == Constants.MODE_NORMAL or Constants.MODE_EXCEL in mode:
            return edit_idx
            
        # XML, JSON 등은 0번이 '라인 번호'이므로 필터 n은 n+1번 컬럼 대응
        return edit_idx + 1

    def update_match_filter_visibility(self, mode):
        # UI 콤보박스 값("끄기") 정규화
        mode = Constants.MODE_NORMAL if mode == AppStrings.SPECIAL_SEARCH_OFF else (mode or Constants.MODE_NORMAL)

        if Constants.MODE_XML in mode:
            self.match_filter_1_edit.setVisible(True)
            self.match_filter_2_edit.setVisible(True)
            self.match_filter_3_edit.setVisible(False)
            self.match_filter_1_edit.setPlaceholderText(AppStrings.MATCH_FILTER_KEY_PLACEHOLDER)
            self.match_filter_2_edit.setPlaceholderText(AppStrings.MATCH_FILTER_VALUE_PLACEHOLDER)
        elif Constants.MODE_JSON in mode:
            self.match_filter_1_edit.setVisible(True)
            self.match_filter_2_edit.setVisible(True)
            self.match_filter_3_edit.setVisible(False)
            self.match_filter_1_edit.setPlaceholderText(AppStrings.MATCH_FILTER_KEY_PLACEHOLDER)
            self.match_filter_2_edit.setPlaceholderText(AppStrings.MATCH_FILTER_VALUE_PLACEHOLDER)
        elif Constants.MODE_ARCHIVE in mode:
            for edit, placeholder in (
                (self.match_filter_1_edit, AppStrings.MATCH_FILTER_ARCHIVE_NAMESPACE_PLACEHOLDER),
                (self.match_filter_2_edit, AppStrings.MATCH_FILTER_ARCHIVE_KEY_PLACEHOLDER),
                (self.match_filter_3_edit, AppStrings.MATCH_FILTER_ARCHIVE_SOURCE_PLACEHOLDER),
                (self.match_filter_4_edit, AppStrings.MATCH_FILTER_ARCHIVE_TRANSLATION_PLACEHOLDER),
            ):
                edit.setVisible(True)
                edit.setPlaceholderText(placeholder)
        elif Constants.MODE_EXCEL in mode:
            self.match_filter_1_edit.setVisible(True)
            self.match_filter_2_edit.setVisible(True)
            self.match_filter_3_edit.setVisible(True)
            self.match_filter_4_edit.setVisible(False)
            self.match_filter_1_edit.setPlaceholderText(AppStrings.MATCH_FILTER_EXCEL_SHEET_PLACEHOLDER)
            self.match_filter_2_edit.setPlaceholderText(AppStrings.MATCH_FILTER_EXCEL_CELL_PLACEHOLDER)
            self.match_filter_3_edit.setPlaceholderText(AppStrings.MATCH_FILTER_EXCEL_VAL_PLACEHOLDER)
        else:
            # 기본 모드 (Normal) - 단일 컬럼 "목록" 대응
            self.match_filter_1_edit.setVisible(True)
            self.match_filter_2_edit.setVisible(False)
            self.match_filter_3_edit.setVisible(False)
            self.match_filter_4_edit.setVisible(False)
            self.match_filter_1_edit.setPlaceholderText(AppStrings.MATCH_FILTER_LIST_PLACEHOLDER)

        # 숨긴 입력란에 남은 글자가 다른 모드의 결과를 몰래 거르지 않도록 비웁니다(검수 U-1).
        for edit in (self.match_filter_1_edit, self.match_filter_2_edit,
                     self.match_filter_3_edit, self.match_filter_4_edit):
            if edit.isHidden() and edit.text():
                edit.blockSignals(True)
                edit.clear()
                edit.blockSignals(False)
        # 가시성 변경 후 현재 입력된 텍스트들을 모델의 정확한 컬럼 인덱스에 재전송
        self.match_model.set_column_filter(self._get_match_filter_col(0), self.match_filter_1_edit.text())
        self.match_model.set_column_filter(self._get_match_filter_col(1), self.match_filter_2_edit.text())
        self.match_model.set_column_filter(self._get_match_filter_col(2), self.match_filter_3_edit.text())
        self.match_model.set_column_filter(self._get_match_filter_col(3), self.match_filter_4_edit.text())

    def _on_result_filters_changed(self):
        """결과 목록 필터 변경 시 호출됩니다."""
        file_text = self.result_file_filter_edit.text()
        folder_text = self.result_folder_filter_edit.text()
        self._result_filter_changing = True
        try:
            self.result_model.set_filters(file_text, folder_text)
        finally:
            self._result_filter_changing = False

    MATCH_FILTER_DEBOUNCE_MIN = 20_000

    def _queue_match_filter(self, edit_idx: int, text: str):
        """결과가 적으면 바로 거르고, 많으면 입력이 멈춘 뒤 한 번에 거릅니다."""
        self._pending_match_filters[self._get_match_filter_col(edit_idx)] = text
        if self.match_model.match_count <= self.MATCH_FILTER_DEBOUNCE_MIN:
            self._apply_pending_match_filters()
        else:
            self._match_filter_timer.start()

    def _apply_pending_match_filters(self):
        self._match_filter_timer.stop()
        pending, self._pending_match_filters = self._pending_match_filters, {}
        if pending:
            self.match_model.set_column_filters(pending)
            self._update_match_pagination_ui()

    def _on_match_filter_1_changed(self, text):
        """첫 번째 필터 입력 시 호출됩니다."""
        self._queue_match_filter(0, text)

    def _on_match_filter_2_changed(self, text):
        """두 번째 필터 입력 시 호출됩니다."""
        self._queue_match_filter(1, text)

    def _on_match_filter_3_changed(self, text):
        """세 번째 필터 입력 시 호출됩니다."""
        self._queue_match_filter(2, text)

    def _on_match_filter_4_changed(self, text):
        """네 번째 필터 입력 시 호출됩니다."""
        self._queue_match_filter(3, text)

    def cleanup(self):
        self._cancel_context_preview()
        self._context_workers.clear()
        self.clear()
        self.result_model._data = []
        self.match_model._data = []

    def _show_result_context_menu(self, pos):
        menu = QMenu(self)
        selection = self.result_view.selectionModel().selectedRows()
        if not selection:
            index = self.result_view.indexAt(pos)
            if index.isValid():
                selection = [index]
        if selection:
            source_index = self.proxy_model.mapToSource(selection[0])
            file_path, _ = self.result_model.get_full_data(source_index.row())
            open_action = QAction(AppStrings.OPEN_FILE, self)
            open_action.triggered.connect(lambda: self.file_double_clicked.emit(file_path, 0))
            folder_action = QAction(AppStrings.OPEN_FOLDER, self)
            folder_action.triggered.connect(lambda: self._open_file_location(file_path))
            copy_label = AppStrings.COPY_PATH if len(selection) == 1 else f"{AppStrings.COPY_PATH} ({len(selection)})"
            copy_action = QAction(copy_label, self)
            copy_action.triggered.connect(self._copy_selected_result_path)
            menu.addAction(open_action)
            menu.addAction(folder_action)
            menu.addSeparator()
            menu.addAction(copy_action)
        if self.result_model.rowCount() > 0:
            export_action = QAction(AppStrings.RESULT_EXPORT_ALL, self)
            export_action.triggered.connect(self._export_results)
            menu.addAction(export_action)
        if not menu.isEmpty():
            menu.exec(self.result_view.viewport().mapToGlobal(pos))

    def _show_match_context_menu(self, pos):
        menu = QMenu(self)
        selection = self.match_view.selectionModel().selectedRows()
        if not selection:
            index = self.match_view.indexAt(pos)
            source_row = self.match_proxy_model.mapToSource(index).row() if index.isValid() else -1
            # 여러 파일 묶음 보기의 파일 머리줄에는 줄 열기·복사 메뉴를 띄우지 않습니다(7.6.5).
            if index.isValid() and not self.match_model.is_group_row(source_row):
                selection = [index]
        if selection:
            open_line_action = QAction(AppStrings.OPEN_LINE_IN_EDITOR, self)
            open_line_action.triggered.connect(lambda: self._emit_match_open(selection[0]))
            menu.addAction(open_line_action)
            menu.addSeparator()
            copy_label = (
                AppStrings.COPY_CONTENT if len(selection) == 1 else f"{AppStrings.COPY_CONTENT} ({len(selection)})"
            )
            copy_action = QAction(copy_label, self)
            copy_action.triggered.connect(self._copy_selected_match_content)
            menu.addAction(copy_action)
            # 칸 하나만 복사(7.6.3): 일반 모드는 위치·내용, 다른 모드는 열 이름마다 하나씩입니다.
            for field_index, label in enumerate(self._match_copy_field_labels()):
                field_action = QAction(label, self)
                field_action.triggered.connect(
                    lambda _checked=False, i=field_index: self._copy_selected_match_field(i)
                )
                menu.addAction(field_action)
            menu.addSeparator()
        if self.result_model.rowCount() > 0:
            export_action = QAction(AppStrings.RESULT_EXPORT_ALL, self)
            export_action.triggered.connect(self._export_results)
            menu.addAction(export_action)
        if not menu.isEmpty():
            menu.exec(self.match_view.viewport().mapToGlobal(pos))

    def _setup_copy_shortcuts(self):
        self.copy_result_shortcut = QShortcut(
            QKeySequence.StandardKey.Copy, self.result_view, context=Qt.ShortcutContext.WidgetShortcut
        )
        self.copy_result_shortcut.activated.connect(self._copy_selected_result_path)
        self.copy_match_shortcut = QShortcut(
            QKeySequence.StandardKey.Copy, self.match_view, context=Qt.ShortcutContext.WidgetShortcut
        )
        self.copy_match_shortcut.activated.connect(self._copy_selected_match_content)

    def _copy_selected_result_path(self):
        selection = self.result_view.selectionModel().selectedRows()
        if not selection:
            index = self.result_view.currentIndex()
            if index.isValid():
                selection = [index]
            else:
                return
        paths = []
        for index in selection:
            source_index = self.proxy_model.mapToSource(index)
            file_path, _ = self.result_model.get_full_data(source_index.row())
            if file_path:
                paths.append(file_path)
        if paths:
            self._copy_to_clipboard("\n".join(paths))

    def _copy_selected_match_content(self):
        selection = self.match_view.selectionModel().selectedRows()
        if not selection:
            index = self.match_view.currentIndex()
            if index.isValid():
                selection = [index]
            else:
                return
        contents = []
        # 현재는 화면에 보이는 데이터(필터 적용됨)를 기준으로 복사합니다.
        for index in selection:
            row_data = []
            for col in range(self.match_proxy_model.columnCount()):
                val = self.match_proxy_model.data(
                    self.match_proxy_model.index(index.row(), col), Qt.ItemDataRole.EditRole
                )
                row_data.append(str(val))
            contents.append("\t".join(row_data))
        if contents:
            self._copy_to_clipboard("\n".join(contents))

    def _match_copy_field_labels(self) -> list:
        """검색 상세 메뉴의 "칸 하나만 복사" 항목 이름입니다. 순서는 MatchDetailModel.copy_fields와 같습니다."""
        model = self.match_model
        if model.columnCount() <= 1:
            return [AppStrings.COPY_LOCATION_ONLY, AppStrings.COPY_TEXT_ONLY]
        return [
            AppStrings.COPY_COLUMN_ONLY.format(model.headerData(col, Qt.Orientation.Horizontal))
            for col in range(model.columnCount())
        ]

    def _selected_match_source_rows(self) -> list:
        """고른 상세 행의 원본 모델 행 번호를 화면 순서대로 돌려줍니다(파일 머리줄 포함, 복사 때 건너뜀)."""
        selection = self.match_view.selectionModel().selectedRows()
        if not selection:
            index = self.match_view.currentIndex()
            selection = [index] if index.isValid() else []
        proxy_rows = sorted({index.row() for index in selection})
        return [self.match_proxy_model.mapToSource(self.match_proxy_model.index(row, 0)).row() for row in proxy_rows]

    def _copy_selected_match_field(self, field_index: int):
        """고른 상세 행들에서 칸 하나만 줄마다 복사합니다(7.6.3). 예: 위치만, 키만, 값만."""
        lines = []
        for source_row in self._selected_match_source_rows():
            fields = self.match_model.copy_fields(source_row)
            if fields is None or field_index >= len(fields):
                continue
            lines.append(fields[field_index])
        if lines:
            self._copy_to_clipboard("\n".join(lines))

    def _copy_to_clipboard(self, text):
        clipboard = QApplication.clipboard()
        clipboard.setText(text)

    def _open_file_location(self, file_path):
        """파일이 있는 폴더를 탐색기에서 열고 그 파일을 선택합니다.

        탐색기가 끝날 때까지 기다리지 않으며, 파일이 없거나 실행하지 못하면 안내합니다(7.1.29).
        """
        if not file_path or not os.path.exists(file_path):
            self._show_open_location_error(file_path)
            return
        try:
            if os.name == "nt":
                command = ["explorer", "/select,", os.path.normpath(file_path)]
            else:
                command = ["open" if sys.platform == "darwin" else "xdg-open", os.path.dirname(file_path)]
            subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as error:
            logger.warning(AppStrings.LOG_OPEN_FOLDER_FAIL.format(file_path, error))
            self._show_open_location_error(file_path)

    def _show_open_location_error(self, file_path):
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.warning(self, AppStrings.ERROR_TITLE, AppStrings.ERROR_OPEN_FOLDER.format(file_path or ""))

    def _export_results(self):
        if self.result_model.rowCount() == 0:
            return
        file_path, selected_filter = QFileDialog.getSaveFileName(
            self, AppStrings.RESULT_EXPORT_TITLE, "", AppStrings.RESULT_EXPORT_FILTER
        )
        if not file_path:
            return
        export_path = Path(file_path)
        if not export_path.suffix:
            export_path = export_path.with_suffix(".xlsx" if "*.xlsx" in selected_filter else ".txt")
        file_path = str(export_path)
        try:
            if Path(file_path).suffix.lower() == ".xlsx":
                self._export_to_excel(file_path)
            else:
                self._export_to_text(file_path)
        except Exception as e:
            logger.error(AppStrings.ERROR_EXPORT_FAIL.format(str(e)))
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, AppStrings.RESULT_EXPORT_TITLE, AppStrings.ERROR_EXPORT_FAIL.format(str(e)))
        else:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, AppStrings.RESULT_EXPORT_TITLE, AppStrings.EXPORT_SUCCESS.format(file_path))

    def _export_to_excel(self, file_path):
        import openpyxl

        temp_path = None
        target = Path(file_path)
        wb = openpyxl.Workbook()

        # [Sheet 1] 검색 파일 목록
        ws1 = wb.active
        ws1.title = AppStrings.EXCEL_SHEET_FILE_LIST
        _append_excel_row(ws1, [AppStrings.HEADER_COUNT, AppStrings.HEADER_FILE, AppStrings.HEADER_MATCH_COUNT,
                                AppStrings.HEADER_NOTE])

        # [Sheet 2] 검색 상세
        ws2 = wb.create_sheet(title=AppStrings.EXCEL_SHEET_MATCH_DETAILS)

        # 모드별 헤더 설정
        mode = self.search_mode or Constants.MODE_NORMAL
        mode_upper = str(mode).upper()
        if Constants.MODE_ARCHIVE.upper() in mode_upper:
            headers = [
                AppStrings.HEADER_FILE,
                AppStrings.HEADER_LINE_NUMBER,
                AppStrings.HEADER_ARCHIVE_NAMESPACE,
                AppStrings.HEADER_ARCHIVE_KEY,
                AppStrings.HEADER_ARCHIVE_SOURCE,
                AppStrings.HEADER_ARCHIVE_TRANSLATION,
            ]
        elif Constants.MODE_EXCEL.upper() in mode_upper:
            headers = [
                AppStrings.HEADER_FILE,
                AppStrings.HEADER_EXCEL_SHEET,
                AppStrings.HEADER_EXCEL_CELL,
                AppStrings.HEADER_EXCEL_VALUE,
            ]
        elif Constants.MODE_JSON.upper() in mode_upper:
            headers = [
                AppStrings.HEADER_FILE,
                AppStrings.HEADER_POSITION,
                AppStrings.HEADER_JSON_KEY,
                AppStrings.HEADER_JSON_VALUE,
            ]
        elif Constants.MODE_XML.upper() in mode_upper:
            headers = [
                AppStrings.HEADER_FILE,
                AppStrings.HEADER_POSITION,
                AppStrings.HEADER_XML_NAME,
                AppStrings.HEADER_XML_VALUE,
            ]
        else:
            headers = [AppStrings.HEADER_FILE, AppStrings.HEADER_POSITION, AppStrings.HEADER_CONTENT]

        _append_excel_row(ws2, headers)

        all_results = self.result_model.get_all_results()

        for i, (count, path, folder, full_path, matches) in enumerate(all_results):
            # 시트 1 데이터 추가
            _append_excel_row(ws1, [i + 1, full_path, count, _result_note(self.result_model, full_path).replace("\n", " / ")])

            # 시트 2 데이터 추가
            for m in matches:
                # 파일당 상한·부분 검색 안내 행(-1/-2)은 일치가 아니므로 내보내지 않습니다(검수 U-10).
                if str(m[0]) in {"-1", "-2"}:
                    continue
                row = [full_path]
                if Constants.MODE_ARCHIVE.upper() in mode_upper:
                    # [Key 줄 번호, Namespace, Key, 원문, 번역문, ...]
                    row.extend([self._export_value(value) for value in list(m[:5]) + [""] * (5 - len(m[:5]))])
                elif Constants.MODE_EXCEL.upper() in mode_upper:
                    # [Line, Sheet, Cell, Val, ...]
                    row.extend([_text_or_empty(m[1]), _text_or_empty(m[2]), _text_or_empty(m[3])])
                elif Constants.MODE_XML.upper() in mode_upper or Constants.MODE_JSON.upper() in mode_upper:
                    # [Line, Name/Path, Value, ...]
                    row.extend([_text_or_empty(m[0]), _text_or_empty(m[1]), _text_or_empty(m[2])])
                else:
                    # 기본 모드: [Line, Content]. 일반 검색에 섞인 Excel 결과(시트<탭>셀<탭>값)는 화면처럼 보이게 바꿉니다.
                    content = _text_or_empty(m[1])
                    if "\t" in content:
                        sheet, cell, value = (content.split("\t", 2) + ["", ""])[:3]
                        content = f"{sheet}!{cell} | {value}"
                    row.extend([_text_or_empty(m[0]), content])
                _append_excel_row(ws2, row)

        temp_file = tempfile.NamedTemporaryFile(
            prefix=f".{target.stem}_", suffix=target.suffix, dir=target.parent,
            delete=False,
        )
        temp_path = temp_file.name
        temp_file.close()
        try:
            wb.save(temp_path)
            os.replace(temp_path, file_path)
        except Exception:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            raise

    def _export_to_text(self, file_path):
        target = Path(file_path)
        temp_path = None
        try:
            temp_file = tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="", prefix=f".{target.stem}_",
                suffix=target.suffix, dir=target.parent, delete=False,
            )
            temp_path = temp_file.name
            temp_file.close()
            with open(temp_path, "w", encoding="utf-8") as f:
                f.write(AppStrings.EXPORT_TEXT_HEADER.format(AppStrings.APP_TITLE) + "\n")
                f.write(f"{AppStrings.EXPORT_SUMMARY_PREFIX}{AppStrings.DOCK_RESULT_TITLE}\n\n")
                all_results = self.result_model.get_all_results()
                for count, path, folder, full_path, matches in all_results:
                    f.write(f"[{count}] {full_path}\n")
                    for note_line in _result_note(self.result_model, full_path).splitlines():
                        f.write(f"  ⚠ {note_line}\n")
                    for item in matches:
                        fields = self._match_fields_for_export(item)
                        if fields:
                            line_no, content = fields[0], "\t".join(fields[1:])
                            f.write(AppStrings.EXPORT_TEXT_LINE_PREFIX.format(line_no, content) + "\n")
                    f.write(AppStrings.EXPORT_TEXT_SEPARATOR + "\n")
            os.replace(temp_path, file_path)
            temp_path = None
        finally:
            if temp_path:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    @staticmethod
    def _export_value(value):
        return "" if value is None else str(value)

    def _match_fields_for_export(self, item):
        """Return the meaningful fields for the active search mode."""
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            return ()
        values = [self._export_value(value) for value in item]
        mode = str(self.search_mode or "").upper()
        if Constants.MODE_ARCHIVE.upper() in mode:
            return tuple(values[:5])
        if Constants.MODE_EXCEL.upper() in mode:
            return tuple(values[:4])
        if Constants.MODE_JSON.upper() in mode or Constants.MODE_XML.upper() in mode:
            return tuple(values[:3])
        return tuple(values[:2])

    def _adjust_column_widths(self):
        """[REQ 2.1 / M-04] 컬럼 폭을 최적화합니다. 샘플링 및 호출 빈도 제한이 적용됩니다."""
        # [M-04] 과도한 호출 방지를 위해 가시성 확인 및 디바운싱 타이머 활용 (resizeEvent에서 트리거됨)
        if not self.result_view.isVisible():
            return

        views = [self.result_view, self.match_view]
        if self._is_multi_column_match_mode():
            # 열이 여러 개인 검색 상세는 _layout_match_columns가 표 폭에 맞춰 나눕니다.
            # 여기서 "내용에 맞춤"을 다시 적용하면 그 배분을 덮어 가로 스크롤이 생깁니다(7.5.6, 검수 3.1).
            views = [self.result_view]
            self._match_layout_timer.start()
        for view in views:
            header = view.horizontalHeader()
            # 1. 성능을 위해 resizeColumnsToContents()는 큰 테이블에서 비용이 높으므로
            # PySide에서 제공하는 기본 최적화 옵션이 있다면 활용하거나,
            # 여기서는 마지막 섹션 확장을 우선함.
            header.setStretchLastSection(True)

            # [M-04] 대용량 데이터(1000행 초과)인 경우 리사이즈 생략하여 프리징 방지
            if view.model() and view.model().rowCount() > 0:
                row_count = view.model().rowCount()
                if row_count <= 1000:
                    if hasattr(header, "setResizeContentsPrecision"):
                        header.setResizeContentsPrecision(50)
                    view.resizeColumnsToContents()
                else:
                    logger.debug(AppStrings.LOG_PERF_SKIP_RESIZE.format(row_count))

            # 3. 가로 스크롤바 방지를 위해 마지막 컬럼을 제외한 나머지는 내용에 밀착
            for i in range(header.count() - 1):
                header.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(header.count() - 1, QHeaderView.ResizeMode.Stretch)

    def auto_select_first_result(self):
        """검색 완료 후 첫 번째 행을 자동으로 선택하고 내용을 호출합니다.

        검색 중에 사용자가 이미 파일을 골라 두었으면(여러 개 포함) 그 선택을 그대로 둡니다(7.5.5).
        """
        selection = self.result_view.selectionModel()
        if selection is not None and selection.hasSelection():
            return
        if self._user_cleared_selection():
            return
        if self.proxy_model.rowCount() > 0:
            index = self.proxy_model.index(0, 0)
            if index.isValid():
                self.result_view.selectRow(0)
                self.result_view.setCurrentIndex(index)
                self._on_result_clicked(index)

    def resizeEvent(self, event):
        """[REQ 2.2] 창 크기 변경 시 통합 핸들러 (중복 정의 이슈 해결)"""
        super().resizeEvent(event)
        # 1. 메인 결과 테이블용 타이머 트리거
        if hasattr(self, "_resize_timer"):
            self._resize_timer.start()

        # 2. 상세 테이블 폭은 MatchColumnWatcher가 보이는 폭 변화를 보고 다시 나눕니다(7.5.4).

    def _adjust_match_column_widths(self):
        """[UI/UX] 검색 상세 테이블의 컬럼 폭을 정합니다(7.5.4).
        파일 선택, 페이지 이동 때 내용 폭을 다시 재고 표 폭에 맞춰 나눕니다.
        """
        if not hasattr(self, "match_view"):
            return
        if not self.match_model or self.match_model.rowCount() == 0:
            return
        header = self.match_view.horizontalHeader()
        if not self._is_multi_column_match_mode():
            # 일반 모드(단일 컬럼)는 항상 뷰포트에 꽉 채우도록 설정
            self._applying_match_widths = True
            try:
                header.setStretchLastSection(True)
                header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            finally:
                self._applying_match_widths = False
            return
        try:
            self._match_natural_widths = self._measure_match_natural_widths()
        except Exception as e:
            logger.debug(AppStrings.LOG_PERF_ADJUST_MATCH_FAIL.format(e))
            return
        self._layout_match_columns()

    def _is_multi_column_match_mode(self) -> bool:
        return (
            self.search_mode != Constants.MODE_NORMAL
            and self.match_proxy_model is not None
            and self.match_proxy_model.columnCount() > 1
        )

    def _match_column_key(self) -> tuple:
        """사용자 비율을 기억하는 열 구성 이름입니다(검색 방식과 열 수)."""
        return (str(self.search_mode), self.match_proxy_model.columnCount())

    def _match_value_columns(self, count: int) -> list:
        """남는 폭을 나눠 가질 값 열입니다. UE archive는 원문·번역 두 열, 나머지는 마지막 열입니다."""
        headers = [self.match_model.headerData(i, Qt.Orientation.Horizontal) for i in range(count)]
        source, translation = AppStrings.HEADER_ARCHIVE_SOURCE, AppStrings.HEADER_ARCHIVE_TRANSLATION
        if count >= 3 and source in headers and translation in headers:
            return [headers.index(source), headers.index(translation)]
        return [count - 1]

    def _measure_match_natural_widths(self) -> list:
        """열마다 한 줄로 그렸을 때의 폭입니다. 결과가 1,000줄을 넘으면 직전 값이나 기본값을 씁니다."""
        count = self.match_proxy_model.columnCount()
        header = self.match_view.horizontalHeader()
        previous = list(self._match_natural_widths)
        row_count = self.match_proxy_model.rowCount()
        if row_count > 1000:
            logger.debug(AppStrings.LOG_PERF_SKIP_MATCH_RESIZE.format(row_count))
            if len(previous) == count:
                return previous
            return [60] + [150] * (count - 1)
        widths = []
        for column in range(count):
            content = self.match_view.sizeHintForColumn(column)
            widths.append(max(int(content), int(header.sectionSizeHint(column)), self.MATCH_COLUMN_MIN_WIDTH))
        return widths

    def _compute_match_column_widths(self, available: int) -> list:
        """보이는 폭 `available`을 열마다 나눕니다. 합이 정확히 `available`이 되어 가로 스크롤이 생기지 않습니다.

        - 사용자가 직접 끈 비율이 있으면 그 비율대로 나눕니다.
        - 없으면 위치 열은 내용 폭(최대 120px), 이름·키 열은 내용 폭이되 표 폭의 15%(최소 80px)까지,
          남는 폭은 값 열(UE archive는 원문·번역)이 똑같이 나눕니다. 값 열 몫이 표 폭의 절반보다 작으면
          이름·키 열을 같은 비율로 줄입니다.
        """
        count = self.match_proxy_model.columnCount()
        minimum = self.MATCH_COLUMN_MIN_WIDTH
        if count <= 0:
            return []
        available = max(int(available), minimum * count)
        ratios = self._match_user_ratios.get(self._match_column_key())
        if ratios and len(ratios) == count:
            widths = [max(minimum, int(available * ratio)) for ratio in ratios]
        else:
            natural = list(self._match_natural_widths)
            if len(natural) != count:
                natural = [60] + [150] * (count - 1)
            value_columns = self._match_value_columns(count)
            key_cap = max(self.MATCH_KEY_MIN_WIDTH, int(available * self.MATCH_KEY_MAX_RATIO))
            widths = [0] * count
            fixed_columns = [column for column in range(count) if column not in value_columns]
            for column in fixed_columns:
                cap = self.MATCH_POSITION_MAX_WIDTH if column == 0 else key_cap
                widths[column] = max(minimum, min(natural[column], cap))
            value_floor = int(available * self.MATCH_VALUE_MIN_RATIO)
            fixed_total = sum(widths[column] for column in fixed_columns)
            if fixed_total > available - value_floor and fixed_total > 0:
                scale = max(0.0, (available - value_floor) / fixed_total)
                for column in fixed_columns:
                    widths[column] = max(minimum, int(widths[column] * scale))
            remaining = available - sum(widths[column] for column in fixed_columns)
            share = max(minimum, remaining // len(value_columns))
            for column in value_columns:
                widths[column] = share
        # 반올림으로 남거나 모자란 폭은 마지막 열이 맡아 합을 표 폭에 정확히 맞춥니다.
        widths[-1] = max(minimum, widths[-1] + available - sum(widths))
        return widths

    def _layout_match_columns(self):
        """현재 보이는 폭에 맞춰 열 폭을 나눕니다. 미리보기 창 분할선·창 크기가 바뀔 때도 불립니다."""
        if not hasattr(self, "match_view") or not self._is_multi_column_match_mode():
            return
        if not self.match_model or self.match_model.rowCount() == 0:
            return
        available = self.match_view.viewport().width()
        if available <= 0:
            return
        widths = self._compute_match_column_widths(available)
        header = self.match_view.horizontalHeader()
        self._applying_match_widths = True
        try:
            header.setStretchLastSection(False)
            for column, width in enumerate(widths):
                header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
                if header.sectionSize(column) != width:
                    header.resizeSection(column, width)
        except Exception as e:
            logger.debug(AppStrings.LOG_PERF_ADJUST_MATCH_FAIL.format(e))
        finally:
            self._applying_match_widths = False

    def _on_match_column_drag_finished(self):
        """열 경계 끌기를 마치면 그 비율을 기억하고, 다음 파일·미리보기 크기 변경에도 같은 비율을 씁니다."""
        if not self._match_user_resized or not self._is_multi_column_match_mode():
            return
        self._match_user_resized = False
        count = self.match_proxy_model.columnCount()
        widths = [self.match_view.columnWidth(column) for column in range(count)]
        total = sum(widths)
        if total <= 0:
            return
        ResultView._match_user_ratios[self._match_column_key()] = [width / total for width in widths]
        self._layout_match_columns()
