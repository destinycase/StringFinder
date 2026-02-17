from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QListWidget,
    QSplitter,
    QTextEdit,
    QLabel,
    QCheckBox,
    QGroupBox,
    QListWidgetItem,
    QFileDialog,
    QHeaderView,
    QMenu,
    QApplication,
    QMessageBox,
    QFileIconProvider,
    QTabWidget,
    QTableView,
    QAbstractItemView,
    QMainWindow,
    QDockWidget,
    QComboBox,
)
from PySide6.QtCore import Qt, QByteArray, Signal, QTimer, QThreadPool
from core.worker import SearchWorker, ScanWorker
from core.search_engine import EXCEL_EXTS
from utils.logger import logger
from utils.app_strings import AppStrings
from utils.constants import Constants
from ui.styles import UIStyles
from ui.widgets import HistoryComboBox, HtmlDelegate
from ui.models import SearchResultModel, MatchDetailModel
from ui.proxies import ResultProxyModel, MatchProxyModel
import os
import sys
import time
import re
from PySide6.QtGui import QAction


class FilterItemWidget(QWidget):
    """
    리스트 위젯 내에서 개별 필터 항목(폴더 또는 확장자)을 표시하는 커스텀 위젯입니다.
    선택 상태(체크박스)와 제거 버튼 기능을 포함합니다.
    """

    def __init__(self, text, checked=True, on_delete=None, on_change=None):
        """필터 항목 위젯을 초기화합니다."""
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 2, 5, 2)

        self.checkbox = QCheckBox(text)
        self.checkbox.setChecked(checked)
        if on_change:
            self.checkbox.stateChanged.connect(on_change)

        self.delete_btn = QPushButton(AppStrings.DELETE_BTN)
        self.delete_btn.setFixedWidth(50)
        self.delete_btn.setStyleSheet(f"QPushButton {{ {UIStyles.STYLE_DANGER_TEXT} }}")  # 삭제 동작 강조를 위한 스타일
        if on_delete:
            self.delete_btn.clicked.connect(on_delete)

        layout.addWidget(self.checkbox)
        layout.addStretch()
        layout.addWidget(self.delete_btn)

    def text(self):
        return self.checkbox.text()

    def isChecked(self):
        return self.checkbox.isChecked()


class SearchTab(QMainWindow):
    """
    개별 검색 세션을 담당하는 탭 위젯입니다.
    검색어 입력, 필터 설정, 결과 표시 및 미리보기 기능을 통합 제공합니다.
    """

    status_message_requested = Signal(str, int)  # 하단 상태 표시줄(StatusBar) 업데이트용 (메시지 내용, 표시 지연 시간)
    progress_update_requested = Signal(
        int, int, bool
    )  # 진행률 표시줄(ProgressBar) 제어용 (현재 값, 전체 값, 가시성 여부)
    search_finished_with_data = Signal()  # 검색이 성공적으로 종료되었음을 최상위 창(MainWindow)에 알림

    def __init__(self, config_manager):
        """검색 탭 UI를 구성하고 초기 상태를 설정합니다."""
        super().__init__()
        self.config_manager = config_manager

        self.worker = None
        self.scan_worker = None

        self.icon_provider = QFileIconProvider()

        self.total_matches = 0
        self.total_files = 0
        self.scanned_count = 0  # 검색 프로세스 전반에서 다룰 총 스캔 대상 파일 수
        self.last_ui_update_time = 0

        # [State Machine] 검색 상태 초기화
        self.search_state = Constants.SearchState.IDLE
        self.pending_restart = False  # 재시작 요청 플래그

        self._init_ui()

    def _init_ui(self):
        """Qt 도킹(Docking) 시스템을 기반으로 유연한 레이아웃을 구성합니다."""
        # 패널 중첩, 탭 결합 및 애니메이션 효과를 활성화합니다.
        self.setDockOptions(QMainWindow.AllowNestedDocks | QMainWindow.AllowTabbedDocks | QMainWindow.AnimatedDocks)

        # 1. 검색 입력 도크 (상단 고정 영역)
        self.search_dock = QDockWidget(AppStrings.DOCK_SEARCH_TITLE, self)
        self.search_dock.setObjectName(Constants.OBJ_NAME_SEARCH_DOCK)
        search_container = QWidget()
        search_v_layout = QVBoxLayout(search_container)

        # 검색어 입력 (첫 번째 줄)
        search_input_layout = QHBoxLayout()
        search_label = QLabel(AppStrings.SEARCH_LABEL)
        self.search_combo = HistoryComboBox()
        self.search_combo.setPlaceholderText(AppStrings.SEARCH_EDIT_PLACEHOLDER)
        self.search_combo.setToolTip(AppStrings.SEARCH_EDIT_PLACEHOLDER)
        self.search_combo.lineEdit().returnPressed.connect(self.start_search)
        self.search_combo.history_item_deleted.connect(lambda t: self._remove_history_item(t, Constants.TYPE_SEARCH))
        self.search_combo.history_cleared.connect(lambda: self._clear_history(Constants.TYPE_SEARCH))
        search_input_layout.addWidget(search_label)
        search_input_layout.addWidget(self.search_combo, 1)
        search_v_layout.addLayout(search_input_layout)

        # 검색 버튼 (두 번째 줄 - 가로로 길게)
        self.search_btn = QPushButton(AppStrings.SEARCH_BTN)
        self.search_btn.setMinimumHeight(40)  # 높이 키움 (이전 동작 복원)
        self.search_btn.setStyleSheet(UIStyles.STYLE_SEARCH_BTN_PRIMARY)  # 강조 스타일 적용
        self.search_btn.clicked.connect(self.start_search)
        search_v_layout.addWidget(self.search_btn)

        self.stop_btn = QPushButton(AppStrings.SEARCH_BTN_STOP)

        self.search_dock.setWidget(search_container)
        self.addDockWidget(Qt.TopDockWidgetArea, self.search_dock)

        # 초기 상태 표시
        self.status_message_requested.emit(AppStrings.STATUS_READY, 0)

        # 2. 폴더 필터 도크
        self.folder_dock = QDockWidget(AppStrings.DOCK_FOLDER_TITLE, self)
        self.folder_dock.setObjectName(Constants.OBJ_NAME_FOLDER_DOCK)
        folder_container = QWidget()
        folder_vbox = QVBoxLayout(folder_container)
        folder_vbox.setContentsMargins(10, 15, 10, 10)
        self.folder_list = QListWidget()
        filters = self.config_manager.get_filters()
        for folder in filters.get("folders", []):
            self._add_folder_item(folder)
        folder_btn_layout = QHBoxLayout()
        add_folder_btn = QPushButton(AppStrings.ADD_FOLDER_BTN)
        add_folder_btn.clicked.connect(self._add_folder)
        self.folder_select_all_btn = QPushButton(AppStrings.SELECT_ALL_BTN)
        self.folder_select_all_btn.setFixedWidth(80)
        self.folder_select_all_btn.clicked.connect(lambda: self._toggle_all_filters(Constants.TYPE_FOLDER, True))
        self.folder_deselect_all_btn = QPushButton(AppStrings.DESELECT_ALL_BTN)
        self.folder_deselect_all_btn.setFixedWidth(80)
        self.folder_deselect_all_btn.clicked.connect(lambda: self._toggle_all_filters(Constants.TYPE_FOLDER, False))
        folder_btn_layout.addWidget(add_folder_btn)
        folder_btn_layout.addWidget(self.folder_select_all_btn)
        folder_btn_layout.addWidget(self.folder_deselect_all_btn)
        folder_vbox.addWidget(self.folder_list)
        folder_vbox.addLayout(folder_btn_layout)
        self.folder_dock.setWidget(folder_container)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.folder_dock)

        # 3. 확장자 필터 도크
        self.ext_dock = QDockWidget(AppStrings.DOCK_EXT_TITLE, self)
        self.ext_dock.setObjectName(Constants.OBJ_NAME_EXT_DOCK)
        ext_container = QWidget()
        ext_vbox = QVBoxLayout(ext_container)
        ext_vbox.setContentsMargins(10, 15, 10, 10)
        special_search_layout = QHBoxLayout()
        special_search_label = QLabel(AppStrings.SPECIAL_SEARCH_LABEL)
        self.special_search_combo = QComboBox()
        self.special_search_combo.addItems(AppStrings.SPECIAL_SEARCH_ITEMS)
        self.special_search_combo.currentTextChanged.connect(self._on_special_search_changed)
        special_search_layout.addWidget(special_search_label)
        special_search_layout.addWidget(self.special_search_combo, 1)
        ext_vbox.addLayout(special_search_layout)
        self.ext_list = QListWidget()
        for ext in filters.get("extensions", []):
            self._add_ext_item(ext)
        ext_input_layout = QHBoxLayout()
        self.ext_edit = QLineEdit()
        self.ext_edit.setPlaceholderText(AppStrings.EXT_EDIT_PLACEHOLDER)
        self.ext_edit.returnPressed.connect(self._add_ext)
        self.add_ext_btn = QPushButton(AppStrings.ADD_EXT_BTN)
        self.add_ext_btn.setFixedWidth(50)
        self.add_ext_btn.clicked.connect(self._add_ext)
        ext_input_layout.addWidget(self.ext_edit)
        ext_input_layout.addWidget(self.add_ext_btn)
        ext_toggle_layout = QHBoxLayout()
        self.ext_select_all_btn = QPushButton(AppStrings.SELECT_ALL_BTN)
        self.ext_select_all_btn.clicked.connect(lambda: self._toggle_all_filters(Constants.TYPE_EXT, True))
        self.ext_deselect_all_btn = QPushButton(AppStrings.DESELECT_ALL_BTN)
        self.ext_deselect_all_btn.clicked.connect(lambda: self._toggle_all_filters(Constants.TYPE_EXT, False))
        ext_toggle_layout.addWidget(self.ext_select_all_btn)
        ext_toggle_layout.addWidget(self.ext_deselect_all_btn)
        ext_vbox.addWidget(self.ext_list)
        ext_vbox.addLayout(ext_input_layout)
        ext_vbox.addLayout(ext_toggle_layout)
        self.ext_dock.setWidget(ext_container)
        # [Fix] 탭 대신 세로로 나열: LeftDockWidgetArea에 추가
        self.addDockWidget(Qt.LeftDockWidgetArea, self.ext_dock)

        # 3.5 파일명 필터 도크 (추가)
        self.filename_dock = QDockWidget(AppStrings.DOCK_FILENAME_TITLE, self)
        self.filename_dock.setObjectName(Constants.OBJ_NAME_FILENAME_DOCK)
        filename_container = QWidget()
        filename_vbox = QVBoxLayout(filename_container)
        filename_vbox.setContentsMargins(10, 15, 10, 10)

        # 상단에 텍스트 입력 필터 유지
        text_filter_layout = QHBoxLayout()
        text_filter_label = QLabel(AppStrings.FILENAME_FILTER_LABEL)
        self.filename_combo = HistoryComboBox()
        self.filename_combo.setPlaceholderText(AppStrings.FILENAME_EDIT_PLACEHOLDER)
        self.filename_combo.lineEdit().returnPressed.connect(self.start_search)
        self.filename_combo.history_item_deleted.connect(
            lambda t: self._remove_history_item(t, Constants.TYPE_FILENAME)
        )
        self.filename_combo.history_cleared.connect(lambda: self._clear_history(Constants.TYPE_FILENAME))
        text_filter_layout.addWidget(text_filter_label)
        text_filter_layout.addWidget(self.filename_combo, 1)
        filename_vbox.addLayout(text_filter_layout)

        # 필터 목록
        self.filename_list = QListWidget()
        for fn in filters.get("filenames", []):
            self._add_filename_item(fn)

        filename_add_layout = QHBoxLayout()
        self.filename_list_edit = QLineEdit()
        self.filename_list_edit.setPlaceholderText(AppStrings.FILENAME_LIST_PLACEHOLDER)
        self.filename_list_edit.returnPressed.connect(self._add_filename_list_item)
        self.add_filename_btn = QPushButton(AppStrings.ADD_EXT_BTN)  # "추가" 텍스트 재사용
        self.add_filename_btn.setFixedWidth(50)
        self.add_filename_btn.clicked.connect(self._add_filename_list_item)
        filename_add_layout.addWidget(self.filename_list_edit)
        filename_add_layout.addWidget(self.add_filename_btn)

        filename_toggle_layout = QHBoxLayout()
        self.filename_select_all_btn = QPushButton(AppStrings.SELECT_ALL_BTN)
        self.filename_select_all_btn.clicked.connect(
            lambda: self._toggle_all_filters(Constants.TYPE_FILENAME_LIST, True)
        )
        self.filename_deselect_all_btn = QPushButton(AppStrings.DESELECT_ALL_BTN)
        self.filename_deselect_all_btn.clicked.connect(
            lambda: self._toggle_all_filters(Constants.TYPE_FILENAME_LIST, False)
        )
        filename_toggle_layout.addWidget(self.filename_select_all_btn)
        filename_toggle_layout.addWidget(self.filename_deselect_all_btn)

        filename_vbox.addWidget(self.filename_list)
        filename_vbox.addLayout(filename_add_layout)
        filename_vbox.addLayout(filename_toggle_layout)
        self.filename_dock.setWidget(filename_container)
        # [Fix] 탭 대신 세로로 나열: LeftDockWidgetArea에 추가
        self.addDockWidget(Qt.LeftDockWidgetArea, self.filename_dock)

        # 4. 결과 및 로그 영역 (중앙 위젯으로 설정됨)
        result_container = QWidget()
        result_area_layout = QVBoxLayout(result_container)
        result_area_layout.setContentsMargins(10, 15, 10, 10)

        self.tab_widget = QTabWidget()
        self.results_tab = QWidget()
        results_tab_layout = QVBoxLayout(self.results_tab)
        results_tab_layout.setContentsMargins(0, 5, 0, 0)

        # 검색 결과(목록), 매칭 상세, 미리보기 창을 세로로 배치하는 3단 분할 창(Splitter)을 생성합니다.
        self.result_splitter = QSplitter(Qt.Vertical)  # 세로 분할로 변경
        self.result_splitter.setHandleWidth(6)

        self.result_view = QTableView()
        self.result_model = SearchResultModel(self.icon_provider)
        self.proxy_model = ResultProxyModel()
        self.proxy_model.setSourceModel(self.result_model)
        self.proxy_model.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.result_view.setModel(self.proxy_model)

        # [Highlighting] HTML 렌더링을 위한 델리게이트 설정
        self.result_view.setItemDelegate(HtmlDelegate(self.result_view))

        self.result_filter_layout = QHBoxLayout()
        self.result_file_filter_edit = QLineEdit()
        self.result_file_filter_edit.setPlaceholderText(AppStrings.RESULT_FILTER_FILE_PLACEHOLDER)
        self.result_file_filter_edit.textChanged.connect(self.proxy_model.setFileFilter)
        self.result_folder_filter_edit = QLineEdit()
        self.result_folder_filter_edit.setPlaceholderText(AppStrings.RESULT_FILTER_FOLDER_PLACEHOLDER)
        self.result_folder_filter_edit.textChanged.connect(self.proxy_model.setFolderFilter)
        self.result_filter_layout.addWidget(self.result_file_filter_edit)
        self.result_filter_layout.addWidget(self.result_folder_filter_edit)

        self._restore_column_widths(Constants.VIEW_RESULT)
        self.result_view.horizontalHeader().setStretchLastSection(False)
        self.result_view.horizontalHeader().sectionResized.connect(
            lambda i, o, n: self._save_column_widths(Constants.VIEW_RESULT)
        )
        self.result_view.setAlternatingRowColors(True)
        self.result_view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.result_view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.result_view.verticalHeader().hide()
        self.result_view.clicked.connect(self._show_matches_from_view)
        self.result_view.doubleClicked.connect(self._open_file_from_view)
        self.result_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.result_view.customContextMenuRequested.connect(self._show_result_context_menu)
        self.result_view.setSortingEnabled(True)

        # 3단 구성을 위해 result_view, pagination, match_area_widget, preview_group을 직접 splitter에 추가
        self.result_splitter.addWidget(self.result_view)

        # 페이지네이션 위젯을 파일 목록과 결과 상세 사이에 추가
        self.pagination_widget = self._create_pagination_widget()
        self.result_splitter.addWidget(self.pagination_widget)

        self.match_area_widget = QWidget()
        match_area_layout = QVBoxLayout(self.match_area_widget)
        match_area_layout.setContentsMargins(0, 0, 0, 0)

        self.match_filter_layout = QHBoxLayout()
        self.match_filter_1_edit = QLineEdit()
        self.match_filter_2_edit = QLineEdit()
        self.match_filter_3_edit = QLineEdit()
        self.match_filter_1_edit.textChanged.connect(lambda t: self.match_proxy_model.setColumnFilter(1, t))
        self.match_filter_2_edit.textChanged.connect(lambda t: self.match_proxy_model.setColumnFilter(2, t))
        self.match_filter_3_edit.textChanged.connect(
            lambda t: self.match_proxy_model.setColumnFilter(4, t)
        )  # Default to col 4 if needed
        self.match_filter_layout.addWidget(self.match_filter_1_edit)
        self.match_filter_layout.addWidget(self.match_filter_2_edit)
        self.match_filter_layout.addWidget(self.match_filter_3_edit)
        self.match_filter_3_edit.setVisible(False)
        self.match_view = QTableView()
        self.match_model = MatchDetailModel()
        self.match_proxy_model = MatchProxyModel()
        self.match_proxy_model.setSourceModel(self.match_model)
        self.match_view.setModel(self.match_proxy_model)
        match_area_layout.addLayout(self.match_filter_layout)
        match_area_layout.addWidget(self.match_view)

        self._restore_column_widths(Constants.VIEW_MATCH)
        self.match_view.horizontalHeader().setStretchLastSection(False)
        self.match_view.horizontalHeader().sectionResized.connect(
            lambda i, o, n: self._save_column_widths(Constants.VIEW_MATCH)
        )
        self.match_view.setItemDelegate(HtmlDelegate(self.match_view))
        self.match_view.setAlternatingRowColors(True)
        self.match_view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.match_view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.match_view.verticalHeader().hide()
        self.match_view.clicked.connect(self._on_view_clicked)
        self.match_view.doubleClicked.connect(self._open_file_from_match_view)
        self.match_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.match_view.customContextMenuRequested.connect(self._show_match_context_menu)

        self.preview_group = QGroupBox(AppStrings.RESULT_PREVIEW_TITLE)
        preview_layout = QVBoxLayout(self.preview_group)
        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_text.setLineWrapMode(QTextEdit.NoWrap)
        self.preview_text.setStyleSheet(UIStyles.STYLE_PREVIEW_EMPTY)
        preview_layout.addWidget(self.preview_text)

        self.result_splitter.addWidget(self.match_area_widget)
        self.result_splitter.addWidget(self.preview_group)

        # Stretch factors: result_view(1), pagination(0-고정높이), match_area(1), preview(1)
        self.result_splitter.setStretchFactor(0, 1)  # result_view
        self.result_splitter.setStretchFactor(1, 0)  # pagination (고정 높이)
        self.result_splitter.setStretchFactor(2, 1)  # match_area_widget
        self.result_splitter.setStretchFactor(3, 1)  # preview_group

        self.empty_label = QLabel(AppStrings.RESULT_EMPTY_MSG)
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet(UIStyles.STYLE_SELECTION_INFO)
        results_tab_layout.addWidget(self.empty_label)
        results_tab_layout.addLayout(self.result_filter_layout)
        results_tab_layout.addWidget(self.result_splitter)

        self.logs_tab = QWidget()
        logs_tab_layout = QVBoxLayout(self.logs_tab)
        logs_tab_layout.setContentsMargins(0, 5, 0, 0)

        self.logs_output = QTextEdit()
        self.logs_output.setReadOnly(True)
        logs_tab_layout.addWidget(self.logs_output)
        from utils.logger import qt_log_handler

        qt_log_handler.message_logged.connect(self.logs_output.append)

        self.tab_widget.addTab(self.results_tab, AppStrings.TAB_RESULTS)
        self.tab_widget.addTab(self.logs_tab, AppStrings.TAB_LOGS)
        result_area_layout.addWidget(self.tab_widget)

        # 중앙 위젯 설정: 결과/로그 영역을 도킹의 중심점으로 지정합니다.
        self.setCentralWidget(result_container)

        # 초기 가시성 설정
        for i in range(self.result_filter_layout.count()):
            self.result_filter_layout.itemAt(i).widget().setVisible(False)
        self.result_splitter.setVisible(False)
        self.empty_label.setVisible(True)

        # 검색 창 히스토리 데이터 로드
        self._load_histories()

        # 도킹 상태 및 스플리터 상태 복원
        dock_state = self.config_manager.get_dock_state()
        if dock_state:
            self.restoreState(QByteArray.fromHex(dock_state.encode()))
        else:
            # 저장된 상태가 없으면 기본값 적용
            default_dock_state = self.config_manager.defaults.get("dock_layout_state")
            if default_dock_state:
                self.restoreState(QByteArray.fromHex(default_dock_state.encode()))

        # result_splitter는 QDockWidget 내부에 있으므로, QMainWindow의 상태와 별개로 저장/복원
        _, result_state, _ = self.config_manager.get_splitter_states()
        if result_state:
            self.result_splitter.restoreState(QByteArray.fromHex(result_state.encode()))
        else:
            # 저장된 상태가 없으면 기본값 적용
            default_result_state = self.config_manager.defaults.get("result_splitter_state")
            if default_result_state:
                self.result_splitter.restoreState(QByteArray.fromHex(default_result_state.encode()))

        # 레이아웃 잠금 설정 적용
        self._apply_lock_layout()
        self.search_combo.setFocus()

    def _create_pagination_widget(self):
        """페이지네이션 UI 생성"""
        from PySide6.QtWidgets import QComboBox

        pagination_widget = QWidget()
        pagination_layout = QHBoxLayout(pagination_widget)
        pagination_layout.setContentsMargins(5, 5, 5, 5)

        # 이전 버튼
        self.prev_page_btn = QPushButton(AppStrings.PAGINATION_PREV)
        self.prev_page_btn.setEnabled(False)
        self.prev_page_btn.setMaximumWidth(80)
        self.prev_page_btn.clicked.connect(self._on_prev_page)

        # 페이지 정보
        self.page_info_label = QLabel(AppStrings.PAGINATION_PAGE)
        self.current_page_edit = QLineEdit("1")
        self.current_page_edit.setMaximumWidth(50)
        self.current_page_edit.setAlignment(Qt.AlignCenter)
        self.current_page_edit.returnPressed.connect(self._on_page_jump)

        self.total_pages_label = QLabel(f"{AppStrings.PAGINATION_OF} 1")

        # 다음 버튼
        self.next_page_btn = QPushButton(AppStrings.PAGINATION_NEXT)
        self.next_page_btn.setEnabled(False)
        self.next_page_btn.setMaximumWidth(80)
        self.next_page_btn.clicked.connect(self._on_next_page)

        # 페이지 크기 선택
        self.page_size_combo = QComboBox()
        self.page_size_combo.addItems(
            [AppStrings.PAGINATION_SIZE_1000, AppStrings.PAGINATION_SIZE_2000, AppStrings.PAGINATION_SIZE_5000]
        )
        self.page_size_combo.setMaximumWidth(100)
        self.page_size_combo.currentIndexChanged.connect(self._on_page_size_changed)

        # 레이아웃 구성
        pagination_layout.addWidget(self.prev_page_btn)
        pagination_layout.addWidget(self.page_info_label)
        pagination_layout.addWidget(self.current_page_edit)
        pagination_layout.addWidget(self.total_pages_label)
        pagination_layout.addWidget(self.next_page_btn)
        pagination_layout.addStretch()
        pagination_layout.addWidget(QLabel(AppStrings.PAGINATION_DISPLAY))
        pagination_layout.addWidget(self.page_size_combo)

        # 초기에는 숨김
        pagination_widget.setVisible(False)

        return pagination_widget

    def _on_prev_page(self):
        """이전 페이지로 이동"""
        current_page = self.result_model.get_current_page()
        if current_page > 1:
            self.result_model.go_to_page(current_page - 1)
            self._update_pagination_ui()

    def _on_next_page(self):
        """다음 페이지로 이동"""
        current_page = self.result_model.get_current_page()
        total_pages = self.result_model.get_total_pages()
        if current_page < total_pages:
            self.result_model.go_to_page(current_page + 1)
            self._update_pagination_ui()

    def _on_page_jump(self):
        """페이지 직접 입력"""
        try:
            page_number = int(self.current_page_edit.text())
            self.result_model.go_to_page(page_number)
            self._update_pagination_ui()
        except ValueError:
            # 잘못된 입력 시 현재 페이지로 복원
            self._update_pagination_ui()

    def _on_page_size_changed(self, index):
        """페이지 크기 변경"""
        sizes = [1000, 2000, 5000]
        self.result_model.set_page_size(sizes[index])
        self._update_pagination_ui()

    def _update_pagination_ui(self):
        """페이지네이션 UI 업데이트"""
        current_page = self.result_model.get_current_page()
        total_pages = self.result_model.get_total_pages()

        # 페이지 정보 업데이트
        self.current_page_edit.setText(str(current_page))
        self.total_pages_label.setText(f"{AppStrings.PAGINATION_OF} {total_pages}")

        # 버튼 활성화/비활성화
        self.prev_page_btn.setEnabled(current_page > 1)
        self.next_page_btn.setEnabled(current_page < total_pages)

        # 페이지네이션 위젯 표시 여부 (1페이지 초과 시만 표시)
        self.pagination_widget.setVisible(total_pages > 1)

    def _load_histories(self):
        """설정 파일에서 검색어와 파일명 필터 히스토리를 불러와 콤보박스에 로드합니다."""
        # 사용자가 현재 입력 중인 텍스트가 사라지지 않도록 백업합니다.
        current_search = self.search_combo.currentText()
        current_filename = self.filename_combo.currentText()

        self.search_combo.set_history(self.config_manager.get_history())
        self.filename_combo.set_history(self.config_manager.get_filename_history())

        self.search_combo.setEditText(current_search)
        self.filename_combo.setEditText(current_filename)

    def _remove_history_item(self, text, history_type):
        """특정 히스토리 항목을 삭제하고 UI를 갱신합니다."""
        if history_type == Constants.TYPE_SEARCH:
            self.config_manager.remove_history_item(text)
        else:
            self.config_manager.remove_filename_history_item(text)
        self._load_histories()

    def _clear_history(self, history_type):
        """전체 히스토리 내역을 삭제합니다."""
        if history_type == Constants.TYPE_SEARCH:
            self.config_manager.clear_history()
        else:
            self.config_manager.clear_filename_history()
        self._load_histories()

    def stop(self):
        """
        현재 진행 중인 검색 작업을 안전하게 중단하도록 플래그를 설정합니다.
        """
        self._stop_existing_search()  # 기존 _stop_existing_search 호출

    def cleanup(self):
        """탭이 닫힐 때 대규모 데이터 구조를 명시적으로 해제하여 메모리 누수를 방지합니다."""
        logger.debug(AppStrings.LOG_RES_CLEANUP_START)
        try:
            self.stop()  # 현재 탭의 검색 작업 중단
            self.result_model.clear()
            self.match_model.clear()
            # 순환 참조 방지를 위해 데이터 구조 전면 초기화
            self.result_model._data = []
            self.match_model._data = []
            self.preview_text.clear()
            self.logs_output.clear()
        except Exception as e:
            logger.warning(AppStrings.LOG_RES_CLEANUP_ERROR.format(e))

    def save_splitter_states(self):
        """현재 UI 도킹 상태 및 스플리터들의 위치 상태를 설정 관리자에 저장합니다."""
        self.config_manager.set_dock_state(self.saveState())
        self.config_manager.set_splitter_states(None, self.result_splitter.saveState(), None)

    def _apply_lock_layout(self):
        """설정에 따라 도킹 패널의 이동 및 띄우기 기능을 제한하거나 허용합니다."""
        is_locked = self.config_manager.get_lock_dock_layout()
        if is_locked:
            features = QDockWidget.DockWidgetFeature.NoDockWidgetFeatures
        else:
            features = (
                QDockWidget.DockWidgetFeature.DockWidgetClosable
                | QDockWidget.DockWidgetFeature.DockWidgetMovable
                | QDockWidget.DockWidgetFeature.DockWidgetFloatable
            )

        for dock in [self.search_dock, self.folder_dock, self.ext_dock, self.filename_dock]:
            dock.setFeatures(features)

    def reset_layout(self):
        """도킹 레이아웃을 초기 기본값으로 복구합니다."""
        # 기존 도킹 패널들을 기본 위치로 재배치
        self.addDockWidget(Qt.TopDockWidgetArea, self.search_dock)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.folder_dock)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.ext_dock)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.filename_dock)
        # [Fix] 탭 대신 세로로 나열 (tabifyDockWidget 제거)

        # 모든 도크 보이기
        for dock in [self.search_dock, self.folder_dock, self.ext_dock, self.filename_dock]:
            dock.show()

        # [Fix] 탭이 아니므로 raise_() 불필요
        # self.folder_dock.raise_()  # 제거됨

        self.save_splitter_states()

    def get_state(self):
        """현재 탭의 모든 상태(입력값, 결과, 로그)를 딕셔너리로 반환합니다."""
        # 폴더/확장자 체크 상태 수집
        folder_states = {}
        for i in range(self.folder_list.count()):
            item = self.folder_list.item(i)
            widget = self.folder_list.itemWidget(item)
            if widget:
                folder_states[widget.text()] = widget.checkbox.isChecked()

        ext_states = {}
        for i in range(self.ext_list.count()):
            item = self.ext_list.item(i)
            widget = self.ext_list.itemWidget(item)
            if widget:
                ext_states[widget.text()] = widget.checkbox.isChecked()

        filename_states = {}
        for i in range(self.filename_list.count()):
            item = self.filename_list.item(i)
            widget = self.filename_list.itemWidget(item)
            if widget:
                filename_states[widget.text()] = widget.checkbox.isChecked()

        return {
            "inputs": {
                "search": self.search_combo.currentText(),
                "filename": self.filename_combo.currentText(),
                "special_mode": self.special_search_combo.currentText(),
                "folders": folder_states,
                "extensions": ext_states,
                "filenames": filename_states,
            },
            "results": self.result_model._data,
            "logs": self.logs_output.toPlainText(),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    def load_state(self, state):
        """저장된 상태로부터 탭 UI와 데이터를 복원합니다."""
        if not state:
            return

        inputs = state.get("inputs", {})
        self.search_combo.set_current_text(inputs.get("search", ""))
        self.filename_combo.set_current_text(inputs.get("filename", ""))
        self.special_search_combo.setCurrentText(inputs.get("special_mode", AppStrings.SPECIAL_SEARCH_OFF))

        # 폴더 필터 체크 상태 복원
        folder_states = inputs.get("folders", {})
        for i in range(self.folder_list.count()):
            item = self.folder_list.item(i)
            widget = self.folder_list.itemWidget(item)
            if widget and widget.text() in folder_states:
                widget.checkbox.setChecked(folder_states[widget.text()])

        # 확장자 필터 체크 상태 복원
        ext_states = inputs.get("extensions", {})
        for i in range(self.ext_list.count()):
            item = self.ext_list.item(i)
            widget = self.ext_list.itemWidget(item)
            if widget and widget.text() in ext_states:
                widget.checkbox.setChecked(ext_states[widget.text()])

        # 파일명 필터 체크 상태 복원
        filename_states = inputs.get("filenames", {})
        for i in range(self.filename_list.count()):
            item = self.filename_list.item(i)
            widget = self.filename_list.itemWidget(item)
            if widget and widget.text() in filename_states:
                widget.checkbox.setChecked(filename_states[widget.text()])

        # 결과 데이터 복원
        results = state.get("results", [])
        if results:
            self.result_model.beginResetModel()
            self.result_model._data = results
            self.result_model.endResetModel()

            self.empty_label.setVisible(False)
            self.result_splitter.setVisible(True)
            for i in range(self.result_filter_layout.count()):
                self.result_filter_layout.itemAt(i).widget().setVisible(True)

            self.total_matches = sum(len(row[4]) for row in results)
            self.total_files = len(results)
            self.status_message_requested.emit(
                f"{AppStrings.DOCK_RESULT_TITLE} ({AppStrings.RESULT_SUMMARY_TEMPLATE.format(self.total_files, self.total_matches)})",
                0,
            )

            # 첫 번째 결과 선택
            QTimer.singleShot(100, self._auto_select_first_result)

        # 로그 복원
        logs = state.get("logs", "")
        if logs:
            self.logs_output.setPlainText(logs)

    def _add_folder(self):
        """사용자로부터 폴더를 선택받아 필터 리스트에 추가합니다."""
        folder = QFileDialog.getExistingDirectory(self, AppStrings.SELECT_FOLDER_TITLE)
        if folder:
            # 리스트에 이미 존재하는 폴더인지 확인하여 중복 추가를 방지합니다.
            for i in range(self.folder_list.count()):
                widget = self.folder_list.itemWidget(self.folder_list.item(i))
                if widget and widget.text() == folder:
                    return
            self._add_folder_item(folder)
            self._sync_filters_to_config()

    def _add_folder_item(self, folder, checked=True):
        """UI 리스트 위젯에 폴더 필터 항목 위젯을 생성하여 삽입합니다."""
        item = QListWidgetItem(self.folder_list)
        widget = FilterItemWidget(
            folder, checked, on_delete=lambda: self._delete_folder_item(item), on_change=self._sync_filters_to_config
        )
        item.setSizeHint(widget.sizeHint())
        self.folder_list.addItem(item)
        self.folder_list.setItemWidget(item, widget)

    def _delete_folder_item(self, item):
        """특정 폴더 필터 항목을 리스트에서 제거합니다."""
        row = self.folder_list.row(item)
        self.folder_list.takeItem(row)
        self._sync_filters_to_config()

    def _add_ext(self):
        """입력 필드의 텍스트를 정규화하여 확장자 필터 리스트에 추가합니다."""
        ext = self.ext_edit.text().strip().lower().replace(".", "")
        if ext:
            # 중복 체크
            for i in range(self.ext_list.count()):
                widget = self.ext_list.itemWidget(self.ext_list.item(i))
                if widget and widget.text() == ext:
                    return
            self._add_ext_item(ext)
            self.ext_edit.clear()
            self._sync_filters_to_config()

    def _add_ext_item(self, ext, checked=True):
        """UI 리스트 위젯에 확장자 필터 항목 위젯을 생성하여 삽입합니다."""
        item = QListWidgetItem(self.ext_list)
        widget = FilterItemWidget(
            ext, checked, on_delete=lambda: self._delete_ext_item(item), on_change=self._sync_filters_to_config
        )
        item.setSizeHint(widget.sizeHint())
        self.ext_list.addItem(item)
        self.ext_list.setItemWidget(item, widget)

    def _delete_ext_item(self, item):
        """특정 확장자 필터 항목을 리스트에서 제거합니다."""
        row = self.ext_list.row(item)
        self.ext_list.takeItem(row)
        self._sync_filters_to_config()

    def _add_filename_list_item(self):
        """입력 필드의 텍스트를 파일명 필터 리스트에 추가합니다."""
        fn = self.filename_list_edit.text().strip()
        if fn:
            # 중복 체크
            for i in range(self.filename_list.count()):
                widget = self.filename_list.itemWidget(self.filename_list.item(i))
                if widget and widget.text() == fn:
                    return
            self._add_filename_item(fn)
            self.filename_list_edit.clear()
            self._sync_filters_to_config()

    def _add_filename_item(self, fn, checked=True):
        """UI 리스트 위젯에 파일명 필터 항목 위젯을 생성하여 삽입합니다."""
        item = QListWidgetItem(self.filename_list)
        widget = FilterItemWidget(
            fn, checked, on_delete=lambda: self._delete_filename_item(item), on_change=self._sync_filters_to_config
        )
        item.setSizeHint(widget.sizeHint())
        self.filename_list.addItem(item)
        self.filename_list.setItemWidget(item, widget)

    def _delete_filename_item(self, item):
        """특정 파일명 필터 항목을 리스트에서 제거합니다."""
        row = self.filename_list.row(item)
        self.filename_list.takeItem(row)
        self._sync_filters_to_config()

    def _sync_filters_to_config(self):
        """현재 UI에 표시된 필터 목록들을 설정 파일(DB)과 동기화합니다."""
        folders = []
        for i in range(self.folder_list.count()):
            widget = self.folder_list.itemWidget(self.folder_list.item(i))
            if widget:
                folders.append(widget.text())

        extensions = []
        for i in range(self.ext_list.count()):
            widget = self.ext_list.itemWidget(self.ext_list.item(i))
            if widget:
                extensions.append(widget.text())

        filenames = []
        for i in range(self.filename_list.count()):
            widget = self.filename_list.itemWidget(self.filename_list.item(i))
            if widget:
                filenames.append(widget.text())

        self.config_manager.update_filters(folders, extensions, filenames)

    def _on_special_search_changed(self, text):
        """특수 검색 모드 변경 시 관련 UI의 활성화 상태를 제어합니다."""
        is_off = text == AppStrings.SPECIAL_SEARCH_OFF
        self.ext_list.setEnabled(is_off)
        self.ext_edit.setEnabled(is_off)
        self.ext_select_all_btn.setEnabled(is_off)
        self.ext_deselect_all_btn.setEnabled(is_off)

        # '추가' 버튼 등은 ext_edit 옆에 있으므로 레이아웃을 통해 찾거나 직접 필드를 추가해야 함
        # 여기서는 self.ext_edit 위젯의 부모 레이아웃 등에서 찾지 않고 직접 참조가 필요할 수 있음
        # _init_ui에서 add_ext_btn을 멤버 변수로 승격시킬 필요가 있음
        if hasattr(self, "add_ext_btn"):
            self.add_ext_btn.setEnabled(is_off)

    def _toggle_all_filters(self, filter_type, select_all):
        """모든 폴더, 확장자 또는 파일명 필터의 체크 상태를 일괄 변경합니다."""
        target_list = None
        if filter_type == Constants.TYPE_FOLDER:
            target_list = self.folder_list
        elif filter_type == Constants.TYPE_EXT:
            target_list = self.ext_list
        elif filter_type == Constants.TYPE_FILENAME_LIST:
            target_list = self.filename_list
        else:
            return

        if target_list:
            for i in range(target_list.count()):
                widget = target_list.itemWidget(target_list.item(i))
                if widget:
                    widget.checkbox.setChecked(select_all)
        self._sync_filters_to_config()

    def _stop_existing_search(self):
        """현재 실행 중인 모든 검색 관련 객체들을 중단시키고 자원을 정리합니다.
        [State Machine] 상태를 STOPPING으로 변경하고 비동기 중단을 요청합니다.
        """
        if self.search_state == Constants.SearchState.IDLE:
            return True

        logger.info(AppStrings.LOG_SCH_STOP_REQUESTED)
        self.search_state = Constants.SearchState.STOPPING

        # [3-State Button] 즉시 '중지 중..' 상태로 변경
        self.search_btn.setText(AppStrings.SEARCH_BTN_STOPPING)
        self.search_btn.setStyleSheet(UIStyles.STYLE_STOP_BTN_WAIT)

        # 1. 파일 스캔 워커 및 스레드 중지
        try:
            if hasattr(self, "scan_worker") and self.scan_worker:
                try:
                    self.scan_worker.signals.scan_finished.disconnect()
                    self.scan_worker.signals.error.disconnect()
                except (RuntimeError, TypeError, AttributeError):
                    pass
                self.scan_worker.stop()
                self.scan_worker = None

        except Exception as e:
            logger.debug(f"Error stopping scan thread: {e}")

        # 2. 실제 문자열 검색 워커 및 스레드 중지
        try:
            if hasattr(self, "worker") and self.worker:
                try:
                    self.worker.signals.progress_updated.disconnect()
                    self.worker.signals.results_found.disconnect()
                    self.worker.signals.skipped_found.disconnect()
                    self.worker.signals.search_finished.disconnect()
                    self.worker.signals.error.disconnect()
                except (RuntimeError, TypeError, AttributeError):
                    pass
                self.worker.stop()
                self.worker.setAutoDelete(True)
                self.worker = None

        except Exception as e:
            logger.debug(f"Error stopping search thread: {e}")

        return True

    def start_search(self):
        """
        사용자 입력을 검증하고 병렬 검색 워커 스레드를 시작합니다.

        [프로세스 흐름]
        1. 검색 상태 및 입력값 검증 (State Machine)
        2. UI 초기화 및 검색어 히스토리 저장
        3. 필터(폴더, 확장자, 파일명) 수집 및 상세 로그 출력
        4. Rust 최적화 엔진(Fast Path) 또는 일반 검색 모드 결정
        5. QThreadPool을 통한 비동기 워커 실행 (ScanWorker -> SearchWorker)
        """
        try:
            # [State Machine] 상태 기반 동작 제어
            if self.search_state != Constants.SearchState.IDLE:
                # 이미 실행 중이거나 중단 중인 경우, 재시작 요청 플래그를 설정하고 중단 시도
                logger.info(AppStrings.LOG_SCH_RESTART_SCHEDULED.format(self.search_state))
                self.pending_restart = True
                self._stop_existing_search()
                return

            search_text = self.search_combo.currentText().strip()
            if not search_text:
                self.status_message_requested.emit(AppStrings.LOG_SCH_EMPTY_QUERY, 5000)
                return

            filename_filter = self.filename_combo.currentText().strip()
            if not filename_filter:
                self.status_message_requested.emit(AppStrings.LOG_SCH_ALL_FILES_GUIDE, 3000)
            else:
                self.status_message_requested.emit(AppStrings.LOG_SCH_FILENAME_FILTER_GUIDE, 3000)

            # [Highlighting] 파일명 필터 모델에 전달하여 하이라이팅 준비
            if hasattr(self, "current_filename_filters"):
                self.result_model.set_filename_filters(self.current_filename_filters)
            else:
                self.result_model.set_filename_filters([])

            # 검색 시작과 동시에 불필요한 입력 UI 팝업을 닫습니다.
            self.search_combo.hidePopup()
            self.filename_combo.hidePopup()

            # 로그 확인이 용이하도록 로그 탭으로 즉시 전환하고 기존 출력을 비웁니다.
            self.logs_output.clear()
            self.tab_widget.setCurrentIndex(1)

            # [State Machine]        # [UI] 검색 시작 시 비활성화
            self._set_inputs_enabled(False)

            # [상태] 즉시 SCANNING으로 변경
            self.search_state = Constants.SearchState.SCANNING

            # 스캔 시작 시간 초기화 (스레드 시작 전 보장)
            self.scan_start_time = time.time()

            # 현재 검색 조건을 히스토리에 저장하고 UI를 동기화합니다.
            self.config_manager.add_history(search_text)
            if filename_filter:
                self.config_manager.add_filename_history(filename_filter)

            self._load_histories()

            # 활성화된 필터 조건(체크된 폴더, 확장자, 파일명)을 수집합니다.
            # [Performance] 대규모 리스트에서 itemWidget 호출은 성능 병목이 될 수 있으므로 주의가 필요합니다.
            selected_folders = []
            for i in range(self.folder_list.count()):
                item = self.folder_list.item(i)
                widget = self.folder_list.itemWidget(item)
                if widget and widget.isChecked():
                    selected_folders.append(widget.text())

            selected_exts = []
            special_mode = self.special_search_combo.currentText()
            if special_mode == AppStrings.SPECIAL_SEARCH_OFF:
                for i in range(self.ext_list.count()):
                    item = self.ext_list.item(i)
                    widget = self.ext_list.itemWidget(item)
                    if widget and widget.isChecked():
                        selected_exts.append(widget.text())
            else:
                # 특수 검색 모드(XML, JSON)에 따른 기본 확장자 강제 설정
                if Constants.MODE_XML in special_mode:
                    selected_exts = [".xml"]
                elif Constants.MODE_JSON in special_mode:
                    selected_exts = [".json"]
                elif Constants.MODE_ARCHIVE in special_mode:
                    selected_exts = [".archive"]
                elif Constants.MODE_EXCEL in special_mode:
                    # 엑셀 확장자 목록 적용 (set -> list 변환)
                    selected_exts = list(EXCEL_EXTS)
                else:
                    selected_exts = [special_mode.lower()]

            # 추가된 파일명 필터들 수집 (콤보박스 입력 + 체크된 프리셋)
            selected_filenames = []

            # 1. 콤보박스 입력값: 콤마(,)로 구분하여 다중 필터 지원
            if filename_filter:
                # "npc, id" -> ["npc", "id"]
                splits = [s.strip() for s in filename_filter.split(",") if s.strip()]
                selected_filenames.extend(splits)

            # 2. 체크리스트 프리셋
            for i in range(self.filename_list.count()):
                item = self.filename_list.item(i)
                widget = self.filename_list.itemWidget(item)
                if widget and widget.isChecked():
                    # 이미 리스트에 있는 단어라도 중복 허용 (OR 조건이므로 상관 없음)
                    selected_filenames.append(widget.text())

            # [Safety] NameError 방지를 위해 클래스 멤버에 저장
            self.current_filename_filters = selected_filenames

            # [Log] 검색 시작 로그 (상세 출력)

            # [Log] 검색 시작 로그 (상세 출력)
            logger.info(AppStrings.LOG_SCH_STARTED)
            logger.info(AppStrings.LOG_SCH_COND_QUERY.format(search_text))

            for folder in selected_folders:
                logger.info(AppStrings.LOG_SCH_COND_FOLDER.format(folder))

            filters_str = ", ".join(selected_filenames) if selected_filenames else "-"
            logger.info(AppStrings.LOG_SCH_COND_FILTER.format(filters_str))

            special_mode_str = special_mode if special_mode else AppStrings.SPECIAL_SEARCH_OFF
            logger.info(AppStrings.LOG_SCH_COND_SPECIAL.format(special_mode_str))

            for ext in selected_exts:
                logger.info(AppStrings.LOG_SCH_COND_EXT.format(ext))

            logger.info(AppStrings.LOG_SCH_PATH_ENTER)

            # 2. [Fast Path] Rust 검색 엔진 사용 여부 확인
            # 1) 특수 파일 포맷(Excel 등)이 아니고

            # 유효성 검사: 폴더와 확장자 각각 최소 1개 선택 확인
            if not selected_folders or not selected_exts:
                # [Fix] 유효성 검사 실패 시 상태를 IDLE로 되돌리고 입력 재활성화
                self.search_state = Constants.SearchState.IDLE
                self._set_inputs_enabled(True)  # 입력 다시 활성화
                QMessageBox.warning(self, AppStrings.ERROR_TITLE, AppStrings.ERROR_NO_SELECTION)
                return

            # 검색 결과를 담을 모델과 UI 상태를 전역적으로 초기화합니다.
            self.results_buffer = []  # 결과 버퍼링 (검색 종료 후 일괄 출력용)
            self.total_matches = 0
            self.total_files = 0
            self.result_view.setSortingEnabled(False)
            self.result_model.clear()
            self.match_model.clear()
            self.preview_text.clear()
            self.status_message_requested.emit(
                AppStrings.STATUS_SEARCHING,
                0,
            )
            self.result_file_filter_edit.clear()  # 필터 입력란 초기화
            self.result_folder_filter_edit.clear()
            self.match_filter_1_edit.clear()
            self.match_filter_2_edit.clear()

            self.empty_label.setVisible(False)
            self.result_splitter.setVisible(False)
            for i in range(self.result_filter_layout.count()):
                self.result_filter_layout.itemAt(i).widget().setVisible(False)

            # 프로그레스 바를 노출하고 검색 버튼의 역할을 '중단'으로 변경하여 시각적 피드백을 제공합니다.
            self.progress_update_requested.emit(0, 100, True)
            self.search_btn.setText(AppStrings.SEARCH_BTN_STOP)
            self.search_btn.setStyleSheet(UIStyles.STYLE_STOP_BTN_ACTIVE)
            # 기존 시작 슬롯과의 연결을 끊고 중단 슬롯을 연결하여 '중지' 버튼으로 전환합니다.
            self.search_btn.clicked.disconnect()
            self.search_btn.clicked.connect(self._stop_existing_search)

            self.start_timer = time.time()
            self.last_ui_update_time = time.time()

            # 2. Rust 병렬 엔진(Phase 2) 가용 여부 확인
            # 조건: Rust 모듈 로드됨 + 특수모드 꺼짐 + 파일명 필터 없음
            from core.search_engine import HAS_RUST_ENGINE

            # 파일명 필터가 하나라도 있으면 현재 Rust 엔진은 미지원이므로 Python 경로로 우회
            use_rust_fast_path = (
                HAS_RUST_ENGINE and special_mode == AppStrings.SPECIAL_SEARCH_OFF and not selected_filenames
            )

            if use_rust_fast_path:
                # [Step 1] 고성능 엔진에서는 스캔과 검색이 통합되어 진행됨을 로그로 명시
                # 스캔 자체는 매우 순식간에 일어나므로 측정된 시간을 로그에 반영
                # scan_duration = time.time() - self.start_timer
                logger.info(AppStrings.LOG_SCH_SCAN_DONE_FAST_PATH)
                self.scan_start_time = self.start_timer  # 시간 측정 호환성 유지
                self.search_stage_start = time.time()
                self.scanned_count = 0  # 초기값 0, 완료 후 found_count로 보정

                # 스캔 단계 없이 바로 검색 워커 시작
                # 스캔 단계 없이 바로 검색 워커 시작
                search_params = {
                    "file_list": [],
                    "search_string": search_text,
                    "special_mode": None,
                    "search_paths": selected_folders,
                    "extensions": selected_exts,
                    "filename_filter": selected_filenames,
                }
                # [Fix] Rust 고속 경로에서도 이전 스레드 정리 필요
                self._setup_search_worker(search_params)
                self.skipped_files_list = []
                QThreadPool.globalInstance().start(self.worker)
                return

            else:
                logger.info(AppStrings.LOG_SCH_PYTHON_MODE)

            # [Worker] 스캔 워커(ScanWorker) 초기화 및 시작
            # QRunnable 기반 워커를 생성하고 QThreadPool에서 실행합니다.
            # 스캔 워커는 파일 목록을 수집 완료하면 자동으로 검색 워커를 연쇄 실행합니다.
            # (start_search_worker 메서드 참조)
            self.scan_worker = ScanWorker(
                selected_folders, selected_exts, self.search_combo.currentText(), use_rust_fast_path
            )

            # 시그널 연결 (스캔 상태 업데이트, 완료, 에러)
            self.scan_worker.signals.progress.connect(self._on_progress)
            self.scan_worker.signals.finished.connect(self._on_scan_finished)
            self.scan_worker.signals.error.connect(self._on_search_error)

            # 전역 스레드 풀(QThreadPool)을 통해 작업 제출
            QThreadPool.globalInstance().start(self.scan_worker)

        except Exception as e:
            import traceback

            error_msg = AppStrings.ERROR_SEARCH_START_FAIL.format(str(e), traceback.format_exc())
            logger.error(error_msg)

            # UI 복구
            self.search_state = Constants.SearchState.IDLE
            self._set_inputs_enabled(True)
            self.progress_update_requested.emit(0, 0, False)  # 프로그레스 바 숨김

            # [Fix] 버튼 상태 복구가 필요할 수 있음 (만약 변경 후 에러가 났다면)
            if self.search_btn.text() == AppStrings.SEARCH_BTN_STOP:
                self._restore_search_button()

            QMessageBox.critical(
                self, AppStrings.ERROR_SEARCH_CRITICAL_TITLE, AppStrings.ERROR_SEARCH_CRITICAL_MSG.format(e)
            )

    def _on_scan_thread_finished(self):
        """스캔 워커가 종료되었을 때 참조를 정리합니다."""
        try:
            # 버튼 상태 복원 로직
            if not self.worker:
                self._restore_search_button()
                self.status_message_requested.emit(AppStrings.STATUS_READY, 0)

                if self.search_state != Constants.SearchState.SEARCHING:
                    self.search_state = Constants.SearchState.IDLE
                    self._check_pending_restart()

            self.scan_worker = None
        except Exception as e:
            import traceback

            logger.error(f"Error in _on_scan_thread_finished: {e}\n{traceback.format_exc()}")
            # UI 복구 로직이 필요하다면 여기에 추가

    def _on_scan_finished(self, file_list, search_text, selected_folders, selected_filenames=None):
        """스캔이 완료되면 실제 문자열 검색 워커를 실행합니다."""
        # [Fix] 이미 중단된 상태(STOPPING)라면 검색 단계로 진입하지 않고 종료
        if self.search_state == Constants.SearchState.STOPPING or self.search_state == Constants.SearchState.IDLE:
            logger.info(AppStrings.LOG_SCH_STOPPED_BY_USER)
            return

        # [Fix] NameError 방지: 인자로 전달받지 못했다면(구버전 호출 등) 클래스 멤버 사용
        if selected_filenames is None:
            selected_filenames = getattr(self, "current_filename_filters", [])

        # [Highlighting] 스캔 완료 시점에서도 필터 모델에 재전달 (확실한 동기화)
        self.result_model.set_filename_filters(selected_filenames)

        scan_duration = time.time() - self.scan_start_time
        logger.info(AppStrings.LOG_SCH_SCAN_DONE.format(len(file_list), scan_duration))

        self.scanned_count = len(file_list)
        self.search_stage_start = time.time()

        # [State Machine] 상태 전이: SCANNING -> SEARCHING
        # 스캔된 파일이 없으면 바로 IDLE로 가겠지만, 여기서는 검색 단계 진입을 표시
        if file_list:
            self.search_state = Constants.SearchState.SEARCHING

        # 스캔된 파일이 없는 경우 작업을 종료하고 안내 메시지를 표시합니다.
        if not file_list:
            logger.info(AppStrings.LOG_SCH_NO_FILES)
            self.progress_update_requested.emit(0, 100, False)
            if not selected_folders:
                self.empty_label.setText(AppStrings.RESULT_EMPTY_NO_FOLDER)
            else:
                self.empty_label.setText(AppStrings.RESULT_EMPTY_NO_MATCH.format(search_text))

            # 에러/상태 안내용 스타일 적용
            self.empty_label.setStyleSheet(UIStyles.STYLE_EMPTY_LABEL.format(Constants.COLOR_RED))
            self.empty_label.setVisible(True)
            self._restore_search_button()
            # 검색할 파일이 없을 때 상태 바를 초기화합니다.
            self.status_message_requested.emit(AppStrings.STATUS_READY, 0)

            # [State Machine] 스캔 완료(파일없음) -> IDLE
            self.search_state = Constants.SearchState.IDLE
            self._check_pending_restart()
            return

        # 3. 실제 문자열 검색 단계
        logger.info(AppStrings.LOG_WKR_INIT)
        special_mode_val = self.special_search_combo.currentText()
        if special_mode_val == AppStrings.SPECIAL_SEARCH_OFF:
            special_mode_val = None

        search_params = {
            "file_list": file_list,
            "search_string": search_text,
            "special_mode": special_mode_val,
            "filename_filter": selected_filenames,
        }
        self._setup_search_worker(search_params)
        self.skipped_files_list = []
        QThreadPool.globalInstance().start(self.worker)

    def _setup_search_worker(self, params):
        """SearchWorker 인스턴스를 생성하고 공통 시그널을 연결합니다."""
        # [Fix] 이전 워커 정리
        if self.worker:
            self.worker.stop()
            self.worker = None

        # QThreadPool 사용에 맞게 워커 생성 (QRunnable)
        # SearchWorker는 파일 리스트를 받아 실제 문자열 검색을 수행합니다.
        self.worker = SearchWorker(params)

        # 시그널 연결 (WorkerSignals 사용)
        self.worker.signals.progress_updated.connect(self._on_progress)
        self.worker.signals.results_found.connect(self._on_results_found)
        self.worker.signals.skipped_found.connect(self._on_skipped_found)
        self.worker.signals.search_finished.connect(self._on_search_finished)
        self.worker.signals.error.connect(self._on_search_error)
        self.worker.signals.finished.connect(self._on_worker_finished)

    def _check_pending_restart(self):
        """재시작 요청이 대기 중이라면 즉시 새 검색을 시작합니다."""
        if self.pending_restart:
            logger.info(AppStrings.LOG_SCH_RESTART_PENDING)
            self.pending_restart = False
            # 스택 풀림 방지를 위해 이벤트 루프에 태워 호출
            QTimer.singleShot(0, self.start_search)

    def _on_worker_finished(self):
        """워커 작업이 완전히 종료(리소스 정리 포함)된 후 호출됩니다."""
        # 검색 도중 중단되었거나 완료되었을 때, 지금까지의 버퍼를 모델에 반영
        if hasattr(self, "results_buffer") and self.results_buffer:
            self.result_model.add_results(self.results_buffer)
            self.total_files = len(self.results_buffer)
            self.results_buffer = []

        # 결과 영역이 숨겨져 있었다면 결과가 1개라도 있을 때 표시
        if self.total_files > 0 and not self.result_splitter.isVisible():
            self.result_splitter.setVisible(True)
            self.empty_label.setVisible(False)
            for i in range(self.result_filter_layout.count()):
                widget = self.result_filter_layout.itemAt(i).widget()
                if widget:
                    widget.setVisible(True)

        # 모든 작업이 끝났으므로 버튼 상태 복구
        self._restore_search_button()

    def _restore_search_button(self):
        """검색 버튼의 상태를 초기 '검색' 모드로 복구합니다."""
        self.search_btn.setText(AppStrings.SEARCH_BTN)
        self.search_btn.setStyleSheet(UIStyles.STYLE_SEARCH_BTN_PRIMARY)  # 강조 스타일 복구
        self.search_btn.clicked.disconnect()
        self.search_btn.clicked.connect(self.start_search)
        self.search_btn.setEnabled(True)

        # [UI] 검색 종료/중단 시 입력 활성화
        self._set_inputs_enabled(True)

    def _set_inputs_enabled(self, enabled):
        """
        검색 중 UI 입력 요소들의 활성화 여부를 토글합니다.

        Args:
            enabled (bool): True면 활성화, False면 비활성화
        """
        # 1. 검색어 입력
        self.search_combo.setEnabled(enabled)

        # 2. 파일명 필터 그룹
        # 도크 자체를 비활성화하면 내부 위젯들도 함께 비활성화됩니다.
        # 단, 도크 타이틀바의 닫기 버튼 등은 유지되어야 하므로 내부 컨테이너 위젯이나 개별 컨트롤을 제어합니다.
        self.filename_dock.widget().setEnabled(enabled)

        # 3. 폴더 필터 그룹
        self.folder_dock.widget().setEnabled(enabled)

        # 4. 확장자 필터 그룹
        # 특수 검색 모드가 켜져 있을 때는 enabled=True가 되어도 확장자 관련 입력은 비활성 상태여야 합니다.
        self.ext_dock.widget().setEnabled(enabled)

        if enabled:
            # 활성화 시점에 특수 검색 모드 상태를 재확인하여 확장자 입력 필드의 최종 상태를 결정합니다.
            # _on_special_search_changed 내부 로직을 재사용합니다.
            current_mode = self.special_search_combo.currentText()
            self._on_special_search_changed(current_mode)

    def _on_skipped_found(self, file_paths):
        """스킵된 파일 목록을 누적합니다."""
        if not hasattr(self, "skipped_files_list"):
            self.skipped_files_list = []
        self.skipped_files_list.extend(file_paths)

    def _on_progress(self, current, total):
        """워커 작업 진행률 정보를 수신하여 UI에 반영합니다."""
        self.progress_update_requested.emit(current, total, True)
        self.status_message_requested.emit(AppStrings.STATUS_SEARCHING, 0)

    def _on_results_found(self, results):
        """워커로부터 전달받은 검색 결과를 버퍼에 저장합니다.
        UI 반응성 확보를 위해 실제 모델 추가는 검색 종료 시 수행합니다.
        """
        if results:
            self.results_buffer.extend(results)
            # 매칭 수 합산 (요약 갱신용)
            for _, count, _ in results:
                self.total_matches += count

            # 요약만 즉각 갱신하여 "진행 중"임을 알림
            # 요약만 즉각 갱신하여 "진행 중"임을 알림
            # match_summary = AppStrings.RESULT_SUMMARY_TEMPLATE.format(len(self.results_buffer), self.total_matches)

            # 진행 상태를 알리기 위해 결과 테이블의 파일 수 헤더 등은 동기화할 수 있으나
            # 여기서는 모델 업데이트를 하지 않으므로 헤더 갱신은 skip 하거나
            # 타이틀바 갱신으로 충분함.

    def _show_matches_from_view(self, index):
        """파일 리스트에서 특정 항목이 클릭되면 해당 파일의 모든 매칭 지점을 상세 뷰에 표시합니다."""
        # Proxy Model 인덱스를 원본 모델 인덱스로 변환합니다.
        source_index = self.proxy_model.mapToSource(index)
        file_path, matches = self.result_model.get_full_data(source_index.row())
        if file_path:
            # 검색어를 모델에 전달하여 강조된 HTML이 생성되도록 합니다.
            search_text = self.search_combo.currentText()
            special_mode = self.special_search_combo.currentText()
            if special_mode == AppStrings.SPECIAL_SEARCH_OFF:
                special_mode = AppStrings.SPECIAL_SEARCH_OFF
            self.match_model.set_matches(file_path, matches, search_text=search_text, search_mode=special_mode)

            # 컬럼 리사이징 모드 및 너비 복원
            self._restore_column_widths(Constants.VIEW_MATCH)

            # 모드에 따른 상세 필터 UI 업데이트
            self._update_match_filter_ui(special_mode)

            # 레이아웃 최적화: 위치/키 컬럼은 내용에 맞게(정밀도 제한), 마지막 컬럼(내용)은 남은 공간 채우기
            m_header = self.match_view.horizontalHeader()
            m_header.setResizeContentsPrecision(100)
            col_count = self.match_model.columnCount()
            for i in range(col_count - 1):
                m_header.setSectionResizeMode(i, QHeaderView.ResizeToContents)
            m_header.setSectionResizeMode(col_count - 1, QHeaderView.Stretch)

    def _on_view_clicked(self, index):
        """상세 매칭 리스트에서 특정 행이 클릭되면 미리보기 패널에 해당 라인 주변 코드를 노출합니다."""
        # Proxy Model 인덱스를 원본 모델 인덱스로 변환합니다.
        source_index = self.match_proxy_model.mapToSource(index)
        line_no = self.match_model.get_line_no(source_index.row())
        file_path = self.match_model.current_file_path

        if line_no is not None:
            self.preview_text.clear()
            try:
                line_no_int = int(line_no)
                if file_path and os.path.exists(file_path):
                    self._update_preview(file_path, line_no_int)
            except (ValueError, TypeError):
                self.preview_text.setPlainText(AppStrings.RESULT_PREVIEW_ERROR)

    def _update_preview(self, file_path, target_line):
        """지정한 파일의 특정 라인 전후 맥락을 읽어 미리보기 패널에 렌더링합니다."""
        try:
            if not os.path.exists(file_path):
                self.preview_text.setPlainText(AppStrings.RESULT_PREVIEW_ERROR)
                return

            # 바이너리 파일 체크 (core.search_engine 기능 활용)
            from core.search_engine import is_binary_file

            if is_binary_file(file_path):
                # 엑셀, 매크로 포함 엑셀 등도 이 단계에서 걸러짐
                self.preview_text.setPlainText(AppStrings.RESULT_PREVIEW_ERROR)
                return

            # 특수 모드 확인
            special_mode = self.special_search_combo.currentText()

            # Archive 특수 검색 모드일 경우 대용량 파일 미리보기 생략 (불필요한 오버헤드 방지)
            if "Archive" in special_mode:
                self.preview_text.clear()
                return

            # Excel 모드일 경우 미리보기 비활성화 (바이너리 체크에서 걸러지겠지만 명시적으로 한 번 더 방어)
            if "Excel" in special_mode:
                self.preview_text.setPlainText(AppStrings.RESULT_PREVIEW_ERROR)
                return

            # 인코딩 자동 감지 (core.search_engine의 기능 재사용)
            from core.search_engine import detect_encoding_quickly

            encoding = Constants.ENC_UTF8
            try:
                with open(file_path, "rb") as f:
                    head = f.read(1024)
                    encoding = detect_encoding_quickly(head)
            except Exception:
                pass

            # 감지된 인코딩으로 파일 열기 (mmap을 통한 고속 접근)
            import mmap

            preview_lines_data = []
            context_range = 5

            with open(file_path, "rb") as f:
                try:
                    # Windows 특성상 파일 크기가 0이면 mmap 에러 발생
                    f_size = os.path.getsize(file_path)
                    if f_size == 0:
                        self.preview_text.clear()
                        return

                    with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                        # target_line까지의 오프셋을 빠르게 찾기 위해 \n 위치 추적
                        # 대용량 파일에서 모든 내용을 string으로 변환 후 split하는 오버헤드 방지
                        current_pos = 0
                        line_offsets = [0]

                        # 최적화: target_line 근처(context_range 포함)까지만 오프셋 수집
                        for _ in range(target_line + context_range):
                            pos = mm.find(b"\n", current_pos)
                            if pos == -1:
                                break
                            current_pos = pos + 1
                            line_offsets.append(current_pos)

                        start_idx = max(0, target_line - context_range - 1)
                        # end_idx를 포함하도록 range 설정 변경 (i + 1이 line_no)
                        # target_line + context_range 까지 가져와야 함
                        end_idx = min(len(line_offsets), target_line + context_range)

                        for i in range(start_idx, end_idx):
                            s_off = line_offsets[i]
                            e_off = line_offsets[i + 1] if i + 1 < len(line_offsets) else f_size

                            line_bytes = mm[s_off:e_off]
                            try:
                                line_text = line_bytes.decode(encoding, errors="replace").rstrip()
                            except Exception:
                                line_text = AppStrings.MSG_DECODE_ERROR

                            preview_lines_data.append((i + 1, line_text))
                except Exception as e:
                    logger.debug(f"Mmap preview failed: {e}")
                    # mmap 실패 시 레거시 방식으로 폴백 (안전성 확보)
                    with open(file_path, "r", encoding=encoding, errors="replace") as f_text:
                        current_idx = 0
                        for line in f_text:
                            current_idx += 1
                            if current_idx < max(1, target_line - context_range):
                                continue
                            if current_idx > target_line + context_range:
                                break
                            preview_lines_data.append((current_idx, line.rstrip()))

            # 다크모드 및 라이트모드 모두에서 잘 보이는 스타일 지정
            # 배경은 투명하게 하고, 텍스트 색상은 위젯의 기본 색상을 따르되 명시적으로 div로 감쌉니다.
            preview_content = "<div style='font-family: inherit; font-size: inherit; line-height: 1.4;'>"
            search_text = self.search_combo.currentText()
            special_mode = self.special_search_combo.currentText()
            is_json = "JSON" in special_mode
            is_xml = "XML" in special_mode
            is_exact = "전체 일치" in special_mode

            for ln, content in preview_lines_data:
                from html import escape

                escaped_content = escape(content)

                # 가독성과 유닛 테스트를 위해 강조 로직을 정적 메서드(get_highlighted_html)로 분리하여 처리
                highlighted = self.get_highlighted_html(escaped_content, search_text, is_xml, is_json, is_exact)

                # 다크모드 대응: 글자색을 테마에 따라 자동 조절하거나 명시적으로 밝게 지정
                line_style = "padding: 2px 5px;"
                if ln == target_line:
                    line_style += UIStyles.STYLE_PREVIEW_HIGHLIGHT_LINE

                preview_content += f"<div style='{line_style}'>{ln:4}: {highlighted}</div>"

            preview_content += "</div>"
            self.preview_text.setHtml(preview_content)
        except Exception as e:
            logger.error(AppStrings.ERROR_PREVIEW.format(e))
            self.preview_text.setPlainText(AppStrings.RESULT_PREVIEW_ERROR)

    def _on_search_finished(self, found_count, skipped_count):
        """검색 작업이 모든 배치를 마치고 성공적으로 종료되었을 때 호출됩니다."""
        # 1. 버퍼링된 모든 결과를 모델에 일괄 추가하여 UI에 그립니다.
        if self.results_buffer:
            self.result_model.add_results(self.results_buffer)
            self.total_files = len(self.results_buffer)
            self.results_buffer = []

        # duration = time.time() - self.start_timer

        # 프로그레스바를 채우고 메시지를 표시합니다.
        self.progress_update_requested.emit(self.scanned_count, self.scanned_count, False)

        # 로그에 스킵된 정보 출력 (개별 파일 목록 포함)
        if hasattr(self, "skipped_files_list") and self.skipped_files_list:
            logger.warning(AppStrings.LOG_SCH_SKIP_COUNT.format(len(self.skipped_files_list)))
            for item in self.skipped_files_list:
                if isinstance(item, tuple) and len(item) == 2:
                    f_path, reason = item
                    logger.warning(AppStrings.LOG_SCH_SKIP_REASON.format(f_path, reason))
                else:
                    logger.warning(AppStrings.LOG_SCH_SKIP_SIMPLE.format(item))

        self._restore_search_button()

        # [Fix] 검색이 정상적으로 종료되었음을 상태 머신에 반영합니다.
        # 이를 누락하면 다음 검색 시작 시 이전 검색이 진행 중인 것으로 오인하여 재시작(Stop -> Start) 로직이 불필요하게 실행됩니다.
        self.search_state = Constants.SearchState.IDLE

        # 결과 유무에 따른 UI 가시성 최종 조정
        self.tab_widget.setCurrentIndex(0)
        has_results = found_count > 0 and self.result_model.rowCount() > 0

        self.empty_label.setVisible(not has_results)
        self.result_splitter.setVisible(has_results)
        for i in range(self.result_filter_layout.count()):
            self.result_filter_layout.itemAt(i).widget().setVisible(has_results)

        # 페이지네이션 UI 업데이트
        if has_results:
            self._update_pagination_ui()

        if has_results:
            # 첫 번째 행 자동 선택 (QTimer 사용하여 UI 갱신 후 실행 보장)
            QTimer.singleShot(100, self._auto_select_first_result)
        else:
            # 필터 텍스트 초기화
            self.result_file_filter_edit.clear()
            self.result_folder_filter_edit.clear()
            self.match_filter_1_edit.clear()
            self.match_filter_2_edit.clear()

        # 결과 테이블 정렬을 활성화하고 빈도가 높은 순으로 기본 정렬합니다.
        self.result_view.setSortingEnabled(True)
        self.result_view.sortByColumn(0, Qt.DescendingOrder)

        # 레이아웃 최적화: 일치 컬럼은 고정 너비, 파일 컬럼은 상위 100개 기준 자동 조절, 폴더 경로는 남은 공간 채우기
        r_header = self.result_view.horizontalHeader()
        r_header.setResizeContentsPrecision(100)  # 성능 최적화: 모든 행이 아닌 100개 행만 스캔
        r_header.setSectionResizeMode(0, QHeaderView.Fixed)
        self.result_view.setColumnWidth(0, 50)  # "일치" 헤더와 숫자가 보이기에 충분한 너비
        r_header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        r_header.setSectionResizeMode(2, QHeaderView.Stretch)

        # 최종 성능 데이터 및 요약 메시지를 생성합니다.
        elapsed = time.time() - self.start_timer
        search_stage_duration = time.time() - self.search_stage_start
        logger.info(AppStrings.LOG_SCH_SEARCH_DONE.format(search_stage_duration))

        # summary = AppStrings.SEARCH_FINISHED_MSG.format(self.total_files, found_count, skipped_count, float(elapsed))
        # summary는 로그용이므로 그대로 두고, 상태바는 요청된 형식으로 출력

        if self.scanned_count == 0:
            self.scanned_count = self.total_files

        status_msg = AppStrings.STATUS_SEARCH_COMPLETED.format(
            self.total_files,
            found_count,
            skipped_count,
            elapsed,
        )
        self.status_message_requested.emit(status_msg, 0)
        self.search_finished_with_data.emit()

    def _on_search_error(self, error_msg):
        """작업 도중 발생한 치명적 오류를 처리하고 사용자에게 알립니다."""
        logger.error(AppStrings.LOG_SCH_ERROR.format(error_msg))
        self.status_message_requested.emit(f"{AppStrings.STATUS_ERROR_PREFIX}{error_msg}", 5000)

        self._restore_search_button()

        # [Fix] 오류로 인한 종료 시에도 상태를 IDLE로 초기화
        self.search_state = Constants.SearchState.IDLE

    def _open_file_from_match_view(self, index):
        """매칭 상세 뷰 더블클릭 시 해당 파일을 외부 편집기/탐색기로 엽니다."""
        file_path = self.match_model.current_file_path
        if file_path:
            from utils.file_helper import open_file

            open_file(file_path)

    def _open_file_from_view(self, index):
        """결과 테이블 일치 항목 더블클릭 시 해당 파일을 외부 프로그램으로 실행합니다."""
        # Proxy Model 인덱스를 원본 모델 인덱스로 변환합니다.
        source_index = self.proxy_model.mapToSource(index)
        file_path, _ = self.result_model.get_full_data(source_index.row())
        if file_path:
            from utils.file_helper import open_file

            open_file(file_path)

    def _show_result_context_menu(self, pos):
        """결과 리스트 위젯에서 마우스 우클릭 시 열기, 복사, 내보내기 팝업 메뉴를 노출합니다."""
        proxy_index = self.result_view.indexAt(pos)
        menu = QMenu(self)

        if proxy_index.isValid():
            source_index = self.proxy_model.mapToSource(proxy_index)
            file_path, _ = self.result_model.get_full_data(source_index.row())

            open_action = QAction(AppStrings.OPEN_FILE, self)
            open_action.triggered.connect(lambda: self._open_specific_file(file_path))

            folder_action = QAction(AppStrings.OPEN_FOLDER, self)
            folder_action.triggered.connect(lambda: self._open_file_location(file_path))

            copy_action = QAction(AppStrings.COPY_PATH, self)
            copy_action.triggered.connect(lambda: self._copy_to_clipboard(file_path))

            menu.addAction(open_action)
            menu.addAction(folder_action)
            menu.addSeparator()
            menu.addAction(copy_action)
            menu.addSeparator()

        # 검색 결과가 하나라도 있을 때만 내보내기 기능을 노출합니다.
        if self.result_model.rowCount() > 0:
            export_action = QAction(AppStrings.RESULT_EXPORT_ALL, self)
            export_action.triggered.connect(self._export_results)
            menu.addAction(export_action)

        if not menu.isEmpty():
            menu.exec(self.result_view.viewport().mapToGlobal(pos))

    def _show_match_context_menu(self, pos):
        """매칭 상세 뷰에서 개별 텍스트 내용을 복사할 수 있는 컨텍스트 메뉴를 엽니다."""
        index = self.match_view.indexAt(pos)
        if not index.isValid():
            return

        content = self.match_view.model().data(index, Qt.DisplayRole)

        menu = QMenu(self)
        copy_action = QAction(AppStrings.COPY_CONTENT, self)
        copy_action.triggered.connect(lambda: self._copy_to_clipboard(content))

        menu.addAction(copy_action)
        menu.exec(self.match_view.viewport().mapToGlobal(pos))

    def keyPressEvent(self, event):
        # Ctrl+C 처리
        if event.modifiers() == Qt.ControlModifier and event.key() == Qt.Key_C:
            if self.result_view.hasFocus():
                self._copy_selected_result_path()
            elif self.match_view.hasFocus():
                self._copy_selected_match_content()
        super().keyPressEvent(event)

    def _copy_selected_result_path(self):
        index = self.result_view.currentIndex()
        if not index.isValid():
            return
        # [Fix] Proxy Model 인덱스를 Source Model 인덱스로 변환해야 올바른 데이터에 접근 가능
        source_index = self.proxy_model.mapToSource(index)
        file_path, _ = self.result_model.get_full_data(source_index.row())
        self._copy_to_clipboard(file_path)

    def _copy_selected_match_content(self):
        index = self.match_view.currentIndex()
        if not index.isValid():
            return
        content = self.match_view.model().data(index, Qt.DisplayRole)
        self._copy_to_clipboard(content)

    def _copy_to_clipboard(self, text):
        clipboard = QApplication.clipboard()
        clipboard.setText(text)

    def _open_specific_file(self, file_path):
        from utils.file_helper import open_file

        open_file(file_path)

    def _open_file_location(self, file_path):
        """파일이 포함된 폴더를 열고, 가능하면 해당 파일을 선택(highlight) 처리합니다."""
        if not os.path.exists(file_path):
            return

        import subprocess

        # Windows의 경우 explorer /select 옵션을 사용하여 파일 강조 처리를 시도합니다.
        if os.name == "nt":
            subprocess.run(["explorer", "/select,", os.path.normpath(file_path)])
        else:
            # macOS 및 Linux의 경우 단순 폴더 열기를 수행합니다.
            folder = os.path.dirname(file_path)
            subprocess.run(["open" if sys.platform == "darwin" else "xdg-open", folder])

    def _export_results(self):
        """현재 테이블에 나열된 모든 검색 결과를 로컬 파일(Excel/Text)로 저장합니다."""
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
        """openpyxl을 사용하여 검색 결과 및 세부 매칭 정보를 Excel 통합본으로 저장합니다."""
        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = AppStrings.EXCEL_SHEET_TITLE

        # 테이블 헤더 구성
        headers = [AppStrings.HEADER_COUNT, AppStrings.HEADER_FILE, AppStrings.EXCEL_MATCH_DETAIL]
        ws.append(headers)

        # 모델 데이터를 순회하며 시트에 기록합니다.
        for row in range(self.result_model.rowCount()):
            path, matches = self.result_model.get_full_data(row)
            count = len(matches)

            # Excel 내 가독성을 위해 상세 매칭 정보를 개행 문자로 합칩니다.
            matches_str = "\n".join([f"[{m[0]}] {m[1]}" for m in matches])

            ws.append([count, path, matches_str])
            # 세부 열의 텍스트가 잘리지 않도록 자동 줄바꿈 설정을 적용합니다.
            ws.cell(row + 2, 3).alignment = openpyxl.styles.Alignment(wrapText=True, vertical="top")

        wb.save(file_path)

    def _export_to_text(self, file_path):
        """검색 결과를 일반 텍스트 문서 형식으로 내보냅니다."""
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(AppStrings.EXPORT_TEXT_HEADER.format(AppStrings.APP_TITLE) + "\n")
            # 제목 대신 기본 텍스트 사용
            f.write(f"{AppStrings.EXPORT_SUMMARY_PREFIX}{AppStrings.DOCK_RESULT_TITLE}\n\n")

            for row in range(self.result_model.rowCount()):
                path, matches = self.result_model.get_full_data(row)
                count = len(matches)

                f.write(f"[{count}] {path}\n")
                for line_no, content in matches:
                    f.write(AppStrings.EXPORT_TEXT_LINE_PREFIX.format(line_no, content))
                f.write(AppStrings.EXPORT_TEXT_SEPARATOR)

    def _auto_select_first_result(self):
        """검색 완료 시 첫 번째 행을 자동으로 선택하고 내용을 노출합니다."""
        if self.proxy_model.rowCount() > 0:
            index = self.proxy_model.index(0, 0)
            if index.isValid():
                self.result_view.selectRow(0)
                self.result_view.setCurrentIndex(index)
                self._show_matches_from_view(index)

    def _update_match_filter_ui(self, mode):
        """특수 검색 모드에 따라 상세 목록의 필터 레이블과 가시성을 조정합니다."""
        self.match_proxy_model.clearFilters()
        self.match_filter_1_edit.clear()
        self.match_filter_2_edit.clear()
        self.match_filter_3_edit.clear()

        # [Fix] 기존 시그널 연결 해제 (중복 연결 방지)
        try:
            self.match_filter_1_edit.textChanged.disconnect()
        except RuntimeError:
            pass
        # 필터 입력 필드의 시그널 연결 해제 (안전하게)
        try:
            self.match_filter_2_edit.textChanged.disconnect()
        except (RuntimeError, TypeError):
            pass  # 이미 연결 해제되었거나 연결되지 않음

        try:
            self.match_filter_3_edit.textChanged.disconnect()
        except (RuntimeError, TypeError):
            pass  # 이미 연결 해제되었거나 연결되지 않음

        if "XML" in mode:
            self.match_filter_1_edit.setVisible(True)
            self.match_filter_2_edit.setVisible(True)
            self.match_filter_3_edit.setVisible(False)
            self.match_filter_1_edit.setPlaceholderText(AppStrings.MATCH_FILTER_NAME_PLACEHOLDER)
            self.match_filter_2_edit.setPlaceholderText(AppStrings.MATCH_FILTER_CONTENT_PLACEHOLDER)

            # 필터 대상 컬럼 재연결
            self.match_filter_1_edit.textChanged.connect(lambda t: self.match_proxy_model.setColumnFilter(1, t))
            self.match_filter_2_edit.textChanged.connect(lambda t: self.match_proxy_model.setColumnFilter(2, t))

        elif "JSON" in mode:
            self.match_filter_1_edit.setVisible(True)
            self.match_filter_2_edit.setVisible(True)
            self.match_filter_3_edit.setVisible(False)
            self.match_filter_1_edit.setPlaceholderText(AppStrings.MATCH_FILTER_KEY_PLACEHOLDER)
            self.match_filter_2_edit.setPlaceholderText(AppStrings.MATCH_FILTER_VALUE_PLACEHOLDER)

            self.match_filter_1_edit.textChanged.connect(lambda t: self.match_proxy_model.setColumnFilter(1, t))
            self.match_filter_2_edit.textChanged.connect(lambda t: self.match_proxy_model.setColumnFilter(2, t))

        elif "Archive" in mode:
            self.match_filter_1_edit.setVisible(True)
            self.match_filter_2_edit.setVisible(True)
            self.match_filter_3_edit.setVisible(True)

            self.match_filter_1_edit.setPlaceholderText(AppStrings.MATCH_FILTER_ARCHIVE_NS_PLACEHOLDER)
            self.match_filter_2_edit.setPlaceholderText(AppStrings.MATCH_FILTER_ARCHIVE_SOURCE_PLACEHOLDER)
            self.match_filter_3_edit.setPlaceholderText(AppStrings.MATCH_FILTER_ARCHIVE_TRANS_PLACEHOLDER)

            # Archive Column: 1: Namespace, 3: Source, 4: Translation
            self.match_filter_1_edit.textChanged.connect(lambda t: self.match_proxy_model.setColumnFilter(1, t))
            self.match_filter_2_edit.textChanged.connect(lambda t: self.match_proxy_model.setColumnFilter(3, t))
            self.match_filter_3_edit.textChanged.connect(lambda t: self.match_proxy_model.setColumnFilter(4, t))

        elif "Excel" in mode:
            # Excel: [Position] [Value]
            self.match_filter_1_edit.setVisible(True)
            self.match_filter_2_edit.setVisible(True)
            self.match_filter_3_edit.setVisible(False)

            self.match_filter_1_edit.setPlaceholderText(AppStrings.MATCH_FILTER_EXCEL_POS_PLACEHOLDER)
            self.match_filter_2_edit.setPlaceholderText(AppStrings.MATCH_FILTER_EXCEL_VAL_PLACEHOLDER)

            # Excel Column: 0: Position, 1: Value
            self.match_filter_1_edit.textChanged.connect(lambda t: self.match_proxy_model.setColumnFilter(0, t))
            self.match_filter_2_edit.textChanged.connect(lambda t: self.match_proxy_model.setColumnFilter(1, t))

        else:
            # 기본 모드
            self.match_filter_1_edit.setVisible(True)
            self.match_filter_2_edit.setVisible(False)
            self.match_filter_3_edit.setVisible(False)

            self.match_filter_1_edit.setPlaceholderText(AppStrings.MATCH_FILTER_CONTENT_PLACEHOLDER)

            self.match_filter_1_edit.textChanged.connect(lambda t: self.match_proxy_model.setColumnFilter(1, t))
            # 2번 필터는 사용하지 않지만 만약 보여진다면 1번 컬럼 필터링으로 동작하도록 (혹은 비활성)
            # 여기서는 숨김 처리하므로 연결 불필요

    def _restore_column_widths(self, table_name):
        """저장된 설정에서 컬럼 너비를 불러와 테이블에 적용합니다."""
        widths = self.config_manager.get_column_widths(table_name)
        if not widths:
            return

        view = self.result_view if table_name == "result" else self.match_view
        header = view.horizontalHeader()

        # 시그널 잠시 차단 (복원 중 저장 방지)
        header.blockSignals(True)

        # 테이블 타입별 특수 레이아웃 적용 (가로 스크롤 방지용 Stretch 설정이 있는 경우 복원 제한)
        if table_name == Constants.VIEW_RESULT:
            header.setResizeContentsPrecision(100)
            header.setSectionResizeMode(0, QHeaderView.Fixed)
            view.setColumnWidth(0, 50)
            header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
            header.setSectionResizeMode(2, QHeaderView.Stretch)
        elif table_name == "match":
            # 매칭 테이블은 동적으로 컬럼수가 변하므로 여기서 처리하지 않고 표시 시점에 처리
            pass
        else:
            for i, width in enumerate(widths):
                if i < header.count():
                    header.setSectionResizeMode(i, QHeaderView.ResizeToContents)
                    view.setColumnWidth(i, width)
        header.blockSignals(False)

    def _save_column_widths(self, table_name):
        """현재 테이블의 컬럼 너비를 설정에 저장합니다."""
        view = self.result_view if table_name == "result" else self.match_view
        header = view.horizontalHeader()
        widths = [view.columnWidth(i) for i in range(header.count())]
        self.config_manager.save_column_widths(table_name, widths)

    @staticmethod
    def get_highlighted_html(escaped_content, search_text, is_xml, is_json, is_exact):
        """
        주어진 HTML 이스케이프된 내용에 대해 검색어 강조 HTML을 반환합니다.
        유닛 테스트를 위해 UI와 분리된 정적 메서드로 구현되었습니다.
        """
        if not search_text:
            return escaped_content

        # 검색 패턴 생성: 전체 일치 모드일 경우 엄격한 경계(Boundary) 처리
        pattern_str = re.escape(search_text)
        if is_exact:
            if is_xml or is_json:
                # XML/JSON 특수 모드: 값이나 텍스트를 감싸는 구조적 기호(" ' > <)를 경계로 강제
                pattern_str = rf'(?<=["\'>]){pattern_str}(?=["\'<])'
            else:
                # 일반 모드: 식별자의 일부가 될 수 있는 특수문자(._:-)를 포함한 네거티브 룩어라운드 적용
                pattern_str = rf"(?<![a-zA-Z0-9._:-]){pattern_str}(?![a-zA-Z0-9._:-])"

        try:
            search_pattern = re.compile(pattern_str, re.IGNORECASE)
        except re.error:
            return escaped_content

        if is_json and ":" in escaped_content:
            # JSON: 첫 번째 콜론(:) 이후의 값 부분에서만 검색 및 강조
            parts = escaped_content.split(":", 1)
            key_part = parts[0]
            val_part = parts[1]
            highlighted_val = search_pattern.sub(
                lambda m: f"<span style='color: #ff9900; font-weight: bold;'>{m.group()}</span>", val_part
            )
            return f"{key_part}:{highlighted_val}"
        elif is_xml:
            # XML: 태그 기호 사이의 이름 제외 강조
            highlighted = search_pattern.sub(
                lambda m: f"<span style='color: #ff9900; font-weight: bold;'>{m.group()}</span>", escaped_content
            )
            # 태그 시작 부분(<Tag) 오탐 보정
            from html import escape

            tag_token = escape(search_text)
            bad_highlight = f"<{tag_token}"
            if bad_highlight in highlighted:
                highlighted = highlighted.replace(
                    f"<span style='color: #ff9900; font-weight: bold;'>{tag_token}</span>", tag_token
                )
            return highlighted
        else:
            # 일반 모드: 전체 강조
            return search_pattern.sub(
                lambda m: f"<span style='color: #ff9900; font-weight: bold;'>{m.group()}</span>", escaped_content
            )
