import os
import subprocess
import sys
import threading
from PySide6.QtCore import QByteArray, Qt, QTimer, Signal
from PySide6.QtCore import QObject, QRunnable, QThreadPool
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
    if isinstance(value, str) and value.startswith(_EXCEL_FORMULA_PREFIXES):
        return "'" + value
    return value


def _append_excel_row(worksheet, values):
    worksheet.append([_sanitize_excel_cell(value) for value in values])


def _detect_text_encoding(file_path):
    """BOM을 우선 확인하고 일반 텍스트는 UTF-8로 읽습니다."""
    with open(file_path, "rb") as file:
        prefix = file.read(4)
    if prefix.startswith(b"\xff\xfe\x00\x00"):
        return "utf-32-le"
    if prefix.startswith(b"\x00\x00\xfe\xff"):
        return "utf-32-be"
    if prefix.startswith(b"\xff\xfe"):
        return "utf-16-le"
    if prefix.startswith(b"\xfe\xff"):
        return "utf-16-be"
    if prefix.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    return "utf-8"


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


def normalize_skipped_files(skipped_files):
    """스킵 항목을 팝업과 세션 저장에 안전한 ``(경로, 사유)`` 튜플로 정규화합니다."""
    from core.search_engine import localize_skip_reason_for_display

    normalized = []
    for item in skipped_files or []:
        if isinstance(item, (list, tuple)):
            if not item:
                continue
            path = str(item[0])
            reason = localize_skip_reason_for_display(item[1]) if len(item) > 1 else ""
        else:
            path = str(item)
            reason = ""
        normalized.append((path, reason))
    return normalized


def _format_skipped_files_text(skipped_files, total_count):
    """건너뛴 파일 경로와 사유를 클립보드 친화적인 일반 텍스트로 만듭니다."""
    entries = normalize_skipped_files(skipped_files)
    blocks = []
    for index, (path, reason) in enumerate(entries, start=1):
        lines = [f"{index}. {path}"]
        if reason:
            lines.append(f"   {AppStrings.SKIPPED_FILES_REASON.format(reason)}")
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

        self.setWindowTitle(AppStrings.SKIPPED_FILES_DIALOG_TITLE)
        self.resize(760, 460)
        layout = QVBoxLayout(self)

        count_label = QLabel(AppStrings.SKIPPED_FILES_COUNT.format(self.total_count))
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


class ResultView(QWidget):
    CONTEXT_RADIUS = Constants.DEFAULT_CONTEXT_PREVIEW_LINES
    CONTEXT_MAX_RADIUS = Constants.MAX_CONTEXT_PREVIEW_LINES
    CONTEXT_MAX_SCAN_LINES = 200_000
    CONTEXT_MAX_LINE_CHARS = 4_000
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
        self.existence_only = False
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

        self._init_ui()
        self._restore_states()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        # 레이아웃 밀도를 높여 더 많은 정보를 한 화면에 표시하도록 간격을 조정합니다.
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(5)
        self.result_list_container = QWidget()
        result_list_layout = QVBoxLayout(self.result_list_container)
        result_list_layout.setContentsMargins(5, 5, 5, 5)  # (0, 0, 0, 0)에서 (5, 5, 5, 5)로 변경됨
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
        self.summary_label.setVisible(False)
        self.skipped_files_banner = QFrame()
        self.skipped_files_banner.setObjectName("skippedFilesBanner")
        self.skipped_files_banner.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
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
        self.result_view = QTableView()
        self.result_model = SearchResultModel(self.icon_provider)
        self.proxy_model = ResultProxyModel()
        self.proxy_model.setSourceModel(self.result_model)
        self.proxy_model.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.result_view.setModel(self.proxy_model)
        self.result_view.setItemDelegate(HtmlDelegate(self.result_view))
        self.result_model.sort_completed.connect(self._select_first_row_safely)
        self.result_model.limit_reached.connect(self._on_limit_reached)

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
        header.sectionResized.connect(lambda i, _o, _n: self._save_column_widths(Constants.VIEW_RESULT))
        self.result_view.setSortingEnabled(True)
        self.result_view.customContextMenuRequested.connect(self._show_result_context_menu)
        self.result_view.clicked.connect(self._on_result_clicked)
        # 키보드 방향키로 항목 이동 시에도 상세 매치 정보가 즉시 업데이트되도록 연결합니다.
        self.result_view.selectionModel().currentRowChanged.connect(lambda curr, _prev: self._on_result_clicked(curr))
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
        self.open_file_btn = QPushButton(AppStrings.OPEN_FILE)
        self.open_file_btn.clicked.connect(self._on_open_selected_file_clicked)
        self.open_folder_btn = QPushButton(AppStrings.OPEN_FOLDER)
        self.open_folder_btn.clicked.connect(self._on_open_selected_folder_clicked)
        file_info_layout.addWidget(self.file_info_label, 1)
        file_info_layout.addWidget(self.open_file_btn)
        file_info_layout.addWidget(self.open_folder_btn)
        # 선택 파일 정보는 한 줄 헤더이므로 세로 방향으로 확장되지 않게 제한합니다.
        self.file_info_header.setFixedHeight(32)
        self.file_info_header.setVisible(False)
        self.match_view = QTableView()
        self.match_model = MatchDetailModel()
        self.match_proxy_model = MatchProxyModel()
        self.match_proxy_model.setSourceModel(self.match_model)
        self.match_view.setModel(self.match_proxy_model)
        self.match_view.setItemDelegate(HtmlDelegate(self.match_view))
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
        self.match_view.clicked.connect(self._on_match_clicked)
        self.match_view.selectionModel().currentRowChanged.connect(lambda curr, _prev: self._on_match_clicked(curr))
        self.match_view.doubleClicked.connect(self._on_match_double_clicked)
        self.match_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.match_view.customContextMenuRequested.connect(self._show_match_context_menu)
        match_area_layout.setContentsMargins(5, 5, 5, 5)
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
        context_line_settings_layout = QHBoxLayout()
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
        context_preview_layout.addLayout(context_line_settings_layout)
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
        main_layout.addWidget(self.summary_label)
        main_layout.addWidget(self.skipped_files_banner)
        main_layout.addLayout(self.result_filter_layout)
        main_layout.addWidget(self.result_splitter, 1)
        self.empty_label = QLabel(AppStrings.RESULT_EMPTY_MSG)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setStyleSheet(UIStyles.STYLE_SELECTION_INFO)
        main_layout.insertWidget(2, self.empty_label, 1)
        self.result_splitter.setVisible(False)
        self._apply_theme_style()
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

    def _on_prev_page(self):
        curr = self.result_model._current_page
        if curr > 1:
            self.result_model.go_to_page(curr - 1)
            self._update_pagination_ui()

    def _on_next_page(self):
        curr = self.result_model._current_page
        total = self.result_model.get_total_pages()
        if curr < total:
            self.result_model.go_to_page(curr + 1)
            self._update_pagination_ui()

    def _on_page_jump(self):
        try:
            val = int(self.current_page_edit.text())
            total = self.result_model.get_total_pages()
            if 1 <= val <= total:
                self.result_model.go_to_page(val)
                self._update_pagination_ui()
            else:
                self.current_page_edit.setText(str(self.result_model._current_page))
        except ValueError:
            self.current_page_edit.setText(str(self.result_model._current_page))

    def _on_page_size_changed(self, index):
        size_str = self.page_size_combo.itemText(index)
        try:
            size = int(size_str)
            self.result_model.set_page_size(size)
            self._update_pagination_ui()
        except ValueError:
            pass

    def _update_pagination_ui(self):
        """결과 목록의 현재 페이지와 총 페이지 수를 UI에 갱신합니다."""
        self.result_view.setUpdatesEnabled(False)
        try:
            curr = self.result_model._current_page
            total = self.result_model.get_total_pages()
            self.current_page_edit.setText(str(curr))
            self.total_pages_label.setText(f"{AppStrings.PAGINATION_OF} {total}")
            self.prev_page_btn.setEnabled(curr > 1)
            self.next_page_btn.setEnabled(curr < total)
            # 결과가 한 페이지를 초과할 때만 페이지네이션을 표시합니다.
            self.pagination_widget.setVisible(total > 1)
        finally:
            self.result_view.setUpdatesEnabled(True)

    def _on_result_clicked(self, index):
        if not index.isValid():
            return
        real_index = self.proxy_model.mapToSource(index)
        item = self.result_model.get_item(real_index.row())
        if item:
            path = item[0]
            matches = item[2]
            self.selected_file_path = path
            self.match_model.set_matches(path, matches, self.search_text, self.search_mode)
            self.file_info_label.setText(
                AppStrings.SELECTED_FILE_INFO_TEMPLATE.format(path, self.match_model.match_count)
            )
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
                QTimer.singleShot(0, lambda: self.match_view.selectRow(0))

    def _on_match_column_resized(self, index, _old_width, _new_width):
        """상세 뷰의 컬럼 크기가 변경될 때 호출되는 로그 핸들러"""
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
        self._show_context_preview(match_item)

    def _clear_context_preview(self):
        """문맥 미리보기 영역을 초기 상태로 되돌립니다."""
        self._cancel_context_preview()
        self._current_match_item = None
        self.context_highlighter.set_language("text")
        self.context_preview.setPlainText(AppStrings.CONTEXT_PREVIEW_PLACEHOLDER)
        self.context_preview.setExtraSelections([])

    def _detect_text_encoding(self, file_path):
        """BOM을 우선 확인하고 일반 텍스트는 UTF-8로 읽습니다."""
        return _detect_text_encoding(file_path)

    def _read_context_lines(self, file_path, target_line):
        """대상 줄 주변만 스트리밍으로 읽어 메모리 사용량을 제한합니다."""
        if target_line > self.CONTEXT_MAX_SCAN_LINES:
            return None
        try:
            before_lines = min(max(int(self.context_before_combo.currentText()), 0), self.CONTEXT_MAX_RADIUS)
        except (AttributeError, TypeError, ValueError):
            before_lines = self.CONTEXT_RADIUS
        try:
            after_lines = min(max(int(self.context_after_combo.currentText()), 0), self.CONTEXT_MAX_RADIUS)
        except (AttributeError, TypeError, ValueError):
            after_lines = self.CONTEXT_RADIUS
        return _read_context_lines_from_file(file_path, target_line, before_lines, after_lines)

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
                line = line[: self.CONTEXT_MAX_LINE_CHARS]
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
        position = str(getattr(match_item, "position", match_item[0]))
        content = str(getattr(match_item, "content", match_item[1]))
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

    def _on_open_selected_folder_clicked(self):
        """선택된 파일의 위치를 운영체제 파일 탐색기에서 엽니다."""
        if self.selected_file_path:
            self._open_file_location(self.selected_file_path)

    def _on_open_selected_file_clicked(self):
        """선택된 파일을 운영체제의 기본 프로그램으로 엽니다."""
        if self.selected_file_path:
            self.file_double_clicked.emit(self.selected_file_path, 0)

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

    def _on_limit_reached(self, limit_count):
        """결과 적재 한도 도달 시 팝업을 표시하고 검색을 중단합니다."""
        from PySide6.QtWidgets import QMessageBox
        
        # 결과 한도 초과 시 안내 문자열을 표시하기 전 검색을 즉시 중단합니다.
        # ResultView는 보통 Tab을 통해 MainWindow의 자식으로 존재
        main_win = self.window()
        if hasattr(main_win, "stop_search"):
            main_win.stop_search()
        
        QMessageBox.warning(
            self,
            AppStrings.TITLE_LIMIT_REACHED,
            AppStrings.MSG_RESULT_LIMIT_REACHED.format(limit_count)
        )

    def sort_results(self):
        """검색 종료 시 호출되어 전체 결과를 정렬합니다 (비동기)."""
        self.result_model.sort_results()
        # 정렬 완료 후 지연 타이머를 통해 페이지네이션 UI를 한 번만 갱신합니다.
        QTimer.singleShot(300, self._update_pagination_ui)
        self.update_ui_visibility()

    def set_summary_info(self, file_count, match_count, duration, skip_count=0, state_prefix=""):
        """상단 요약 정보를 업데이트합니다."""
        if file_count > 0 or skip_count > 0:
            summary_text = AppStrings.RESULT_SUMMARY_FORMAT.format(
                file_count=file_count,
                match_count=match_count,
                skip_count=skip_count,
                duration=f"{duration:.2f}",
            )
            # 개별 파일 내 매치 수가 너무 많아 일부가 생략된 경우 안내 메시지를 추가합니다.
            if self.result_model.has_truncated_results:
                summary_text += f" {AppStrings.MSG_MATCH_TRUNCATION_NOTICE}"
                
            self.summary_label.setText(state_prefix + summary_text)
            self.summary_label.setVisible(True)
        else:
            self.summary_label.setText("")
            self.summary_label.setVisible(False)

    def set_skipped_files(self, skipped_files, total_count=None):
        """상단 스킵 배너와 팝업에서 사용할 파일 목록을 갱신합니다."""
        self._skipped_files = normalize_skipped_files(skipped_files)
        requested_count = len(self._skipped_files) if total_count is None else int(total_count or 0)
        self._skipped_file_count = max(requested_count, len(self._skipped_files), 0)
        self.skipped_files_label.setText(AppStrings.SKIPPED_FILES_COUNT.format(self._skipped_file_count))
        self.skipped_files_banner.setVisible(self._skipped_file_count > 0)

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
        self.summary_label.setText("")
        self.summary_label.setVisible(False)
        self.set_skipped_files([], total_count=0)
        self.selected_file_path = ""
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
        
        # 결과 존재 여부만 확인하는 모드에서는 상세 목록을 숨겨 화면을 효율적으로 사용합니다.
        if self.existence_only:
            self.match_area_widget.setVisible(False)
        else:
            self.match_area_widget.setVisible(has_results)

        for i in range(self.result_filter_layout.count()):
            item = self.result_filter_layout.itemAt(i)
            if item:
                w = item.widget()
                if w:
                    w.setVisible(has_results)

    def show_empty_message(self, text, is_error=False):
        self.empty_label.setText(text)
        if is_error:
            self.empty_label.setStyleSheet(UIStyles.STYLE_EMPTY_LABEL.format(Constants.COLOR_RED))
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

    def set_search_context(self, search_text, search_mode, existence_only=False):
        self.search_text = search_text
        self.existence_only = existence_only
        # 특수 검색 비활성화 시 모드 값을 기본값으로 변환합니다.
        if search_mode == AppStrings.SPECIAL_SEARCH_OFF:
            self.search_mode = Constants.MODE_NORMAL
        else:
            self.search_mode = search_mode
        self.update_ui_visibility()

    def set_results(self, results):
        """세션 로드 등 대량의 결과를 한꺼번에 설정할 때 사용합니다."""
        self.clear()
        if results:
            self.result_model.add_results(results)
            self.update_ui_visibility()
            QTimer.singleShot(0, self._select_first_row_safely)
        self._update_pagination_ui()

    def get_results(self):
        return self.result_model._data

    def save_state(self):
        self.config_manager.set_splitter_states(None, self.result_splitter.saveState(), None)
        self._save_column_widths(Constants.VIEW_RESULT)
        self._save_column_widths(Constants.VIEW_MATCH)

    def _restore_states(self):
        _, result_state, _ = self.config_manager.get_splitter_states()
        if result_state:
            try:
                self.result_splitter.restoreState(QByteArray.fromHex(result_state.encode()))
            except Exception:
                pass
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
        widths = self.config_manager.get_column_widths(view_type)
        if not widths:
            return
        target_view = self.result_view if view_type == Constants.VIEW_RESULT else self.match_view
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

        # [H-06 Fix] 가시성 변경 후 현재 입력된 텍스트들을 모델의 정확한 컬럼 인덱스에 재전송
        self.match_model.set_column_filter(self._get_match_filter_col(0), self.match_filter_1_edit.text())
        self.match_model.set_column_filter(self._get_match_filter_col(1), self.match_filter_2_edit.text())
        self.match_model.set_column_filter(self._get_match_filter_col(2), self.match_filter_3_edit.text())
        self.match_model.set_column_filter(self._get_match_filter_col(3), self.match_filter_4_edit.text())

    def _on_result_filters_changed(self):
        """결과 목록 필터 변경 시 호출됩니다."""
        file_text = self.result_file_filter_edit.text()
        folder_text = self.result_folder_filter_edit.text()
        self.result_model.set_filters(file_text, folder_text)

    def _on_match_filter_1_changed(self, text):
        """첫 번째 필터 입력 시 호출됩니다."""
        self.match_model.set_column_filter(self._get_match_filter_col(0), text)

    def _on_match_filter_2_changed(self, text):
        """두 번째 필터 입력 시 호출됩니다."""
        self.match_model.set_column_filter(self._get_match_filter_col(1), text)

    def _on_match_filter_3_changed(self, text):
        """세 번째 필터 입력 시 호출됩니다."""
        self.match_model.set_column_filter(self._get_match_filter_col(2), text)

    def _on_match_filter_4_changed(self, text):
        """네 번째 필터 입력 시 호출됩니다."""
        self.match_model.set_column_filter(self._get_match_filter_col(3), text)

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
            if index.isValid():
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

    def _copy_to_clipboard(self, text):
        clipboard = QApplication.clipboard()
        clipboard.setText(text)

    def _open_file_location(self, file_path):
        if not os.path.exists(file_path):
            return
        if os.name == "nt":
            subprocess.run(["explorer", "/select,", os.path.normpath(file_path)])
        else:
            folder = os.path.dirname(file_path)
            subprocess.run(["open" if sys.platform == "darwin" else "xdg-open", folder])

    def _export_results(self):
        if self.result_model.rowCount() == 0:
            return
        file_path, _ = QFileDialog.getSaveFileName(
            self, AppStrings.RESULT_EXPORT_TITLE, "", AppStrings.RESULT_EXPORT_FILTER
        )
        if not file_path:
            return
        try:
            if file_path.endswith(".xlsx"):
                self._export_to_excel(file_path)
            else:
                self._export_to_text(file_path)
        except Exception as e:
            logger.error(AppStrings.ERROR_EXPORT_FAIL.format(str(e)))

    def _export_to_excel(self, file_path):
        import openpyxl

        wb = openpyxl.Workbook()

        # [Sheet 1] 검색 파일 목록
        ws1 = wb.active
        ws1.title = AppStrings.EXCEL_SHEET_FILE_LIST
        _append_excel_row(ws1, [AppStrings.HEADER_COUNT, AppStrings.HEADER_FILE, AppStrings.HEADER_MATCH_COUNT])

        # [Sheet 2] 검색 상세
        ws2 = wb.create_sheet(title=AppStrings.EXCEL_SHEET_MATCH_DETAILS)

        # 모드별 헤더 설정
        mode = self.search_mode or Constants.MODE_NORMAL
        mode_upper = str(mode).upper()
        if Constants.MODE_EXCEL.upper() in mode_upper:
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
            _append_excel_row(ws1, [i + 1, full_path, count])

            # 시트 2 데이터 추가
            for m in matches:
                row = [full_path]
                # m의 구조는 models.py의 MatchDetailModel.set_matches 로직과 계약됨
                if Constants.MODE_EXCEL.upper() in mode_upper:
                    # [Line, Sheet, Cell, Val, ...]
                    row.extend([str(m[1]), str(m[2]), str(m[3])])
                elif Constants.MODE_XML.upper() in mode_upper or Constants.MODE_JSON.upper() in mode_upper:
                    # [Line, Name/Path, Value, ...]
                    row.extend([str(m[0]), str(m[1]), str(m[2])])
                else:
                    # 기본 모드: [Line, Content]
                    row.extend([str(m[0]), str(m[1])])
                _append_excel_row(ws2, row)

        wb.save(file_path)

    def _export_to_text(self, file_path):
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(AppStrings.EXPORT_TEXT_HEADER.format(AppStrings.APP_TITLE) + "\n")
            f.write(f"{AppStrings.EXPORT_SUMMARY_PREFIX}{AppStrings.DOCK_RESULT_TITLE}\n\n")
            all_results = self.result_model.get_all_results()
            for count, path, folder, full_path, matches in all_results:
                f.write(f"[{count}] {full_path}\n")
                for item in matches:
                    if len(item) >= 2:
                        line_no = item[0]
                        content = item[1]
                        f.write(AppStrings.EXPORT_TEXT_LINE_PREFIX.format(line_no, content) + "\n")
                f.write(AppStrings.EXPORT_TEXT_SEPARATOR + "\n")

    def _adjust_column_widths(self):
        """[REQ 2.1 / M-04] 컬럼 폭을 최적화합니다. 샘플링 및 호출 빈도 제한이 적용됩니다."""
        # [M-04] 과도한 호출 방지를 위해 가시성 확인 및 디바운싱 타이머 활용 (resizeEvent에서 트리거됨)
        if not self.result_view.isVisible():
            return

        for view in [self.result_view, self.match_view]:
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
        """검색 완료 후 첫 번째 행을 자동으로 선택하고 내용을 호출합니다."""
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

        # 2. 상세 테이블 가시성 확인 후 즉시 폭 조정
        if hasattr(self, "match_view") and self.match_view.isVisible():
            self._adjust_match_column_widths()

    def _adjust_match_column_widths(self):
        """[UI/UX] 검색 상세 테이블의 컬럼 폭을 최적화합니다.
        파일 선택, 페이지 이동, 창 크기 변경 시에 호출됩니다.
        """
        if not hasattr(self, "match_view"):
            return

        # [Bug Fix] width() <= 0 guard 제거:
        # 위젯이 Splitter 안에 있으면 표시 전에도 너비가 0이 될 수 있어
        # 100ms 지연 후 재시도에서도 조정이 스킵되는 문제 해결.
        # (match_model이 데이터를 가지고 있으면 너비 계산 수행)
        if not self.match_model or self.match_model.rowCount() == 0:
            return

        header = self.match_view.horizontalHeader()
        header.blockSignals(True)  # 무한 루프 방지
        from PySide6.QtWidgets import QHeaderView

        try:
            # 1. 모든 컬럼의 리사이즈 모드 초기화 (Stretch 충돌 방지)
            for i in range(self.match_proxy_model.columnCount()):
                header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)

            if self.search_mode == Constants.MODE_NORMAL:
                # 일반 모드(단일 컬럼)는 항상 뷰포트에 꽉 채우도록 설정
                header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            else:
                # 특수 검색 모드(다중 컬럼)
                # [M-04] 상세 뷰도 대용량(1000행 초과)일 경우 자동 리사이즈 생략
                row_count = self.match_proxy_model.rowCount()
                if row_count <= 1000:
                    self.match_view.resizeColumnsToContents()
                else:
                    logger.debug(AppStrings.LOG_PERF_SKIP_MATCH_RESIZE.format(row_count))

                # [UI/UX] 내용이 너무 길어 컬럼 폭이 부풀려지는 것을 방지 (최대 500px 제한)
                for i in range(self.match_proxy_model.columnCount()):
                    w = self.match_view.columnWidth(i)
                    if w > 500:
                        self.match_view.setColumnWidth(i, 500)

                # 내용(Content) 또는 마지막 주요 데이터 컬럼은 Stretch 정책 적용
                last_col = self.match_proxy_model.columnCount() - 1
                if last_col >= 0:
                    header.setSectionResizeMode(last_col, QHeaderView.ResizeMode.Stretch)

                # 파일 변경 시 자동 조절이 우선되도록 기존 고정 폭 복원 로직 제거
                # 사용자가 수동으로 조절한 폭은 필요 시 별도 시점에서 복원하도록 관리
        except Exception as e:
            logger.debug(AppStrings.LOG_PERF_ADJUST_MATCH_FAIL.format(e))
        finally:
            header.blockSignals(False)
