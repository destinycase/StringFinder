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
from typing import Optional, List
from core.worker import SearchWorker, ScanWorker
from core.search_engine import EXCEL_EXTS
from sf_utils.logger import logger
from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants
from ui.styles import UIStyles
from ui.widgets import HistoryComboBox, HtmlDelegate
from ui.models import SearchResultModel, MatchDetailModel
from ui.proxies import ResultProxyModel, MatchProxyModel
import os
import sys
import time
import re
from PySide6.QtGui import QAction
from shiboken6 import isValid


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
        self.delete_btn.setStyleSheet(f"QPushButton {{ {UIStyles.STYLE_DANGER_TEXT} }}")
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
    개별 검색 세션에 해당하는 탭 위젯입니다.
    검색어 입력, 필터 설정, 결과 표시 및 미리보기 기능을 통합 제공합니다.
    """

    status_message_requested = Signal(str, int)
    progress_update_requested = Signal(int, int, bool)
    search_finished_with_data = Signal()
    search_status_changed = Signal(bool)

    def __init__(self, config_manager):
        """검색 탭의 UI를 구성하고 초기 상태를 설정합니다."""
        super().__init__()
        self.config_manager = config_manager

        self.worker = None
        self.scan_worker = None

        self.icon_provider = QFileIconProvider()

        self.total_matches = 0
        self.total_files = 0
        self.scanned_count = 0
        self.last_ui_update_time = 0.0

        self.search_state = Constants.SearchState.IDLE
        self.pending_restart = False
        self.current_filename_filters: List[str] = []

        self._init_ui()

    def _init_ui(self):
        """Qt 도킹(Docking) 시스템을 기반으로 유연한 레이아웃을 구성합니다."""
        self.setDockOptions(
            QMainWindow.DockOption.AllowNestedDocks
            | QMainWindow.DockOption.AllowTabbedDocks
            | QMainWindow.DockOption.AnimatedDocks
        )

        self.search_dock = QDockWidget(AppStrings.DOCK_SEARCH_TITLE, self)
        self.search_dock.setObjectName(Constants.OBJ_NAME_SEARCH_DOCK)
        search_container = QWidget()
        search_v_layout = QVBoxLayout(search_container)

        search_input_layout = QHBoxLayout()
        search_label = QLabel(AppStrings.SEARCH_LABEL)
        self.search_combo = HistoryComboBox()
        self.search_combo.setPlaceholderText(AppStrings.SEARCH_EDIT_PLACEHOLDER)
        self.search_combo.setToolTip(AppStrings.SEARCH_EDIT_PLACEHOLDER)
        le = self.search_combo.lineEdit()
        if le:
            le.returnPressed.connect(self.start_search)
        self.search_combo.history_item_deleted.connect(lambda t: self._remove_history_item(t, Constants.TYPE_SEARCH))
        self.search_combo.history_cleared.connect(lambda: self._clear_history(Constants.TYPE_SEARCH))
        search_input_layout.addWidget(search_label)
        search_input_layout.addWidget(self.search_combo, 1)
        search_v_layout.addLayout(search_input_layout)

        self.search_btn = QPushButton(AppStrings.SEARCH_BTN)
        self.search_btn.setMinimumHeight(40)
        self.search_btn.setStyleSheet(UIStyles.STYLE_SEARCH_BTN_PRIMARY)
        self.search_btn.clicked.connect(self.start_search)
        search_v_layout.addWidget(self.search_btn)

        self.stop_btn = QPushButton(AppStrings.SEARCH_BTN_STOP)

        self.search_dock.setWidget(search_container)
        self.addDockWidget(Qt.DockWidgetArea.TopDockWidgetArea, self.search_dock)

        self.status_message_requested.emit(AppStrings.STATUS_READY, 0)

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
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.folder_dock)

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
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.ext_dock)

        self.filename_dock = QDockWidget(AppStrings.DOCK_FILENAME_TITLE, self)
        self.filename_dock.setObjectName(Constants.OBJ_NAME_FILENAME_DOCK)
        filename_container = QWidget()
        filename_vbox = QVBoxLayout(filename_container)
        filename_vbox.setContentsMargins(10, 15, 10, 10)

        text_filter_layout = QHBoxLayout()
        text_filter_label = QLabel(AppStrings.FILENAME_FILTER_LABEL)
        self.filename_combo = HistoryComboBox()
        self.filename_combo.setPlaceholderText(AppStrings.FILENAME_EDIT_PLACEHOLDER)
        le_fn = self.filename_combo.lineEdit()
        if le_fn:
            le_fn.returnPressed.connect(self.start_search)
        self.filename_combo.history_item_deleted.connect(
            lambda t: self._remove_history_item(t, Constants.TYPE_FILENAME)
        )
        self.filename_combo.history_cleared.connect(lambda: self._clear_history(Constants.TYPE_FILENAME))
        text_filter_layout.addWidget(text_filter_label)
        text_filter_layout.addWidget(self.filename_combo, 1)
        filename_vbox.addLayout(text_filter_layout)

        self.filename_list = QListWidget()
        for fn in filters.get("filenames", []):
            self._add_filename_item(fn)

        filename_add_layout = QHBoxLayout()
        self.filename_list_edit = QLineEdit()
        self.filename_list_edit.setPlaceholderText(AppStrings.FILENAME_LIST_PLACEHOLDER)
        self.filename_list_edit.returnPressed.connect(self._add_filename_list_item)
        self.add_filename_btn = QPushButton(AppStrings.ADD_EXT_BTN)
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
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.filename_dock)

        result_container = QWidget()
        result_area_layout = QVBoxLayout(result_container)
        result_area_layout.setContentsMargins(10, 15, 10, 10)

        self.tab_widget = QTabWidget()
        self.results_tab = QWidget()
        results_tab_layout = QVBoxLayout(self.results_tab)
        results_tab_layout.setContentsMargins(0, 5, 0, 0)

        self.result_splitter = QSplitter(Qt.Orientation.Vertical)
        self.result_splitter.setHandleWidth(6)

        self.result_view = QTableView()
        self.result_model = SearchResultModel(self.icon_provider)
        self.proxy_model = ResultProxyModel()
        self.proxy_model.setSourceModel(self.result_model)
        self.proxy_model.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.result_view.setModel(self.proxy_model)

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
        self.result_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.result_view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.result_view.verticalHeader().hide()
        self.result_view.clicked.connect(self._show_matches_from_view)
        self.result_view.doubleClicked.connect(self._open_file_from_view)
        self.result_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.result_view.customContextMenuRequested.connect(self._show_result_context_menu)
        self.result_view.setSortingEnabled(True)

        self.result_list_container = QWidget()
        result_list_layout = QVBoxLayout(self.result_list_container)
        result_list_layout.setContentsMargins(0, 0, 0, 0)
        result_list_layout.setSpacing(0)

        result_list_layout.addWidget(self.result_view)

        self.pagination_widget = self._create_pagination_widget()
        result_list_layout.addWidget(self.pagination_widget)

        self.result_splitter.addWidget(self.result_list_container)

        self.match_area_widget = QWidget()
        match_area_layout = QVBoxLayout(self.match_area_widget)
        match_area_layout.setContentsMargins(0, 0, 0, 0)

        self.match_filter_layout = QHBoxLayout()
        self.match_filter_1_edit = QLineEdit()
        self.match_filter_2_edit = QLineEdit()
        self.match_filter_3_edit = QLineEdit()
        self.match_filter_1_edit.textChanged.connect(self._on_match_filter_1_changed)
        self.match_filter_2_edit.textChanged.connect(self._on_match_filter_2_changed)
        self.match_filter_3_edit.textChanged.connect(self._on_match_filter_3_changed)
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
        self.match_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.match_view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.match_view.verticalHeader().hide()
        self.match_view.clicked.connect(self._on_view_clicked)
        self.match_view.doubleClicked.connect(self._open_file_from_match_view)
        self.match_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.match_view.customContextMenuRequested.connect(self._show_match_context_menu)

        self.preview_group = QGroupBox(AppStrings.RESULT_PREVIEW_TITLE)
        preview_layout = QVBoxLayout(self.preview_group)
        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_text.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.preview_text.setStyleSheet(UIStyles.STYLE_PREVIEW_EMPTY)
        preview_layout.addWidget(self.preview_text)

        self.result_splitter.addWidget(self.match_area_widget)
        self.result_splitter.addWidget(self.preview_group)

        self.result_splitter.setStretchFactor(0, 1)  # result_list_container
        self.result_splitter.setStretchFactor(1, 1)  # match_area_widget
        self.result_splitter.setStretchFactor(2, 1)  # preview_group

        self.empty_label = QLabel(AppStrings.RESULT_EMPTY_MSG)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
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
        from sf_utils.logger import qt_log_handler

        qt_log_handler.signaler.message_logged.connect(self.logs_output.append)

        self.tab_widget.addTab(self.results_tab, AppStrings.TAB_RESULTS)
        self.tab_widget.addTab(self.logs_tab, AppStrings.TAB_LOGS)
        result_area_layout.addWidget(self.tab_widget)

        self.setCentralWidget(result_container)

        for i in range(self.result_filter_layout.count()):
            item = self.result_filter_layout.itemAt(i)
            if item:
                widget = item.widget()
                if widget:
                    widget.setVisible(False)
        self.result_splitter.setVisible(False)
        self.empty_label.setVisible(True)

        self._load_histories()

        dock_state = self.config_manager.get_dock_state()
        if dock_state:
            self.restoreState(QByteArray.fromHex(dock_state.encode()))
        else:
            default_dock_state = self.config_manager.defaults.get("dock_layout_state")
            if default_dock_state:
                self.restoreState(QByteArray.fromHex(default_dock_state.encode()))

        _, result_state, _ = self.config_manager.get_splitter_states()
        if result_state:
            self.result_splitter.restoreState(QByteArray.fromHex(result_state.encode()))
        else:
            default_result_state = self.config_manager.defaults.get("result_splitter_state")
            if default_result_state:
                self.result_splitter.restoreState(QByteArray.fromHex(default_result_state.encode()))

        self._apply_lock_layout()
        self._setup_copy_shortcuts()
        self.search_combo.setFocus()

    def _create_pagination_widget(self):
        """페이지네이션 UI 생성"""
        from PySide6.QtWidgets import QComboBox

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

        self.current_page_edit.setText(str(current_page))
        self.total_pages_label.setText(f"{AppStrings.PAGINATION_OF} {total_pages}")

        self.prev_page_btn.setEnabled(current_page > 1)
        self.next_page_btn.setEnabled(current_page < total_pages)

        self.pagination_widget.setVisible(total_pages > 1)

    def _load_histories(self):
        """설정 파일에서 검색어 및 파일명 필터 히스토리를 불러와 콤보박스에 로드합니다."""
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
        self._stop_existing_search()

    def cleanup(self):
        """탭을 닫거나 테스트 종료 시 리소스를 정리하여 메모리 누수와 세그폴트를 방지합니다."""
        logger.debug(AppStrings.LOG_RES_CLEANUP_START)
        try:
            self.stop()
            self.result_model.clear()
            self.match_model.clear()
            self.result_model._data = []
            self.match_model._data = []
            self.preview_text.clear()
            self.logs_output.clear()
        except Exception as e:
            logger.warning(AppStrings.LOG_RES_CLEANUP_ERROR.format(e))

        try:
            from sf_utils.logger import qt_log_handler

            # [v4.33.2 Fix] Safety check to prevent RuntimeWarning on redundant disconnect
            sig = qt_log_handler.signaler.message_logged
            try:
                sig.disconnect(self.logs_output.append)
            except (TypeError, RuntimeError, Exception):
                pass
        except Exception:
            pass

        if hasattr(self, "worker") and self.worker:
            # 중지 및 정리 로직 (필요 시 추가)
            pass

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
        self.addDockWidget(Qt.DockWidgetArea.TopDockWidgetArea, self.search_dock)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.folder_dock)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.ext_dock)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.filename_dock)

        for dock in [self.search_dock, self.folder_dock, self.ext_dock, self.filename_dock]:
            dock.show()

        self.save_splitter_states()

    def get_state(self):
        """현재 탭의 모든 상태(입력값, 결과, 로그)를 딕셔너리로 반환합니다."""
        folder_states = {}
        for i in range(self.folder_list.count()):
            item = self.folder_list.item(i)
            widget = self.folder_list.itemWidget(item)
            if isinstance(widget, FilterItemWidget):
                folder_states[widget.text()] = widget.checkbox.isChecked()

        ext_states = {}
        for i in range(self.ext_list.count()):
            item = self.ext_list.item(i)
            widget = self.ext_list.itemWidget(item)
            if isinstance(widget, FilterItemWidget):
                ext_states[widget.text()] = widget.checkbox.isChecked()

        filename_states = {}
        for i in range(self.filename_list.count()):
            item = self.filename_list.item(i)
            widget = self.filename_list.itemWidget(item)
            if isinstance(widget, FilterItemWidget):
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
        """저장된 상태로부터 UI 및 데이터를 복원합니다."""
        if not state:
            return

        inputs = state.get("inputs", {})
        self.search_combo.set_current_text(inputs.get("search", ""))
        self.filename_combo.set_current_text(inputs.get("filename", ""))
        self.special_search_combo.setCurrentText(inputs.get("special_mode", AppStrings.SPECIAL_SEARCH_OFF))

        folder_states = inputs.get("folders", {})
        for i in range(self.folder_list.count()):
            item = self.folder_list.item(i)
            widget = self.folder_list.itemWidget(item)
            if isinstance(widget, FilterItemWidget) and widget.text() in folder_states:
                widget.checkbox.setChecked(folder_states[widget.text()])

        ext_states = inputs.get("extensions", {})
        for i in range(self.ext_list.count()):
            item = self.ext_list.item(i)
            widget = self.ext_list.itemWidget(item)
            if isinstance(widget, FilterItemWidget) and widget.text() in ext_states:
                widget.checkbox.setChecked(ext_states[widget.text()])

        filename_states = inputs.get("filenames", {})
        for i in range(self.filename_list.count()):
            item = self.filename_list.item(i)
            widget = self.filename_list.itemWidget(item)
            if isinstance(widget, FilterItemWidget) and widget.text() in filename_states:
                widget.checkbox.setChecked(filename_states[widget.text()])

        results = state.get("results", [])
        if results:
            self.result_model.beginResetModel()
            self.result_model._data = results
            self.result_model.endResetModel()

            self.empty_label.setVisible(False)
            self.result_splitter.setVisible(True)
            for i in range(self.result_filter_layout.count()):
                layout_item = self.result_filter_layout.itemAt(i)
                if layout_item:
                    w = layout_item.widget()
                    if w:
                        w.setVisible(True)

            self.total_matches = sum(len(row[4]) for row in results)
            self.total_files = len(results)
            self.status_message_requested.emit(
                f"{AppStrings.DOCK_RESULT_TITLE} ({AppStrings.RESULT_SUMMARY_TEMPLATE.format(self.total_files, self.total_matches)})",
                0,
            )

            QTimer.singleShot(100, self._auto_select_first_result)

        # 로그 복원
        logs = state.get("logs", "")
        if logs:
            self.logs_output.setPlainText(logs)

    def _add_folder(self):
        """사용자로부터 폴더를 선택받아 필터 리스트에 추가합니다."""
        folder = QFileDialog.getExistingDirectory(self, AppStrings.SELECT_FOLDER_TITLE)
        if folder:
            for i in range(self.folder_list.count()):
                widget = self.folder_list.itemWidget(self.folder_list.item(i))
                if isinstance(widget, FilterItemWidget) and widget.text() == folder:
                    return
            self._add_folder_item(folder)
            self._sync_filters_to_config()

    def _add_folder_item(self, folder, checked=True):
        """UI 리스트 위젯, 폴더 필터 항목 위젯을 생성하여 삽입합니다."""
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
                if isinstance(widget, FilterItemWidget) and widget.text() == ext:
                    return
            self._add_ext_item(ext)
            self.ext_edit.clear()
            self._sync_filters_to_config()

    def _add_ext_item(self, ext, checked=True):
        """UI 리스트 위젯, 확장자 필터 항목 위젯을 생성하여 삽입합니다."""
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
                if isinstance(widget, FilterItemWidget) and widget.text() == fn:
                    return
            self._add_filename_item(fn)
            self.filename_list_edit.clear()
            self._sync_filters_to_config()

    def _add_filename_item(self, fn, checked=True):
        """UI 리스트 위젯, 파일명 필터 항목 위젯을 생성하여 삽입합니다."""
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
            if isinstance(widget, FilterItemWidget):
                folders.append(widget.text())

        extensions = []
        for i in range(self.ext_list.count()):
            widget = self.ext_list.itemWidget(self.ext_list.item(i))
            if isinstance(widget, FilterItemWidget):
                extensions.append(widget.text())

        filenames = []
        for i in range(self.filename_list.count()):
            widget = self.filename_list.itemWidget(self.filename_list.item(i))
            if isinstance(widget, FilterItemWidget):
                filenames.append(widget.text())

        self.config_manager.update_filters(folders, extensions, filenames)

    def _on_special_search_changed(self, text):
        """특수 검색 모드 변경 시 관련 UI의 활성/비활성 상태를 제어합니다."""
        is_off = text == AppStrings.SPECIAL_SEARCH_OFF
        self.ext_list.setEnabled(is_off)
        self.ext_edit.setEnabled(is_off)
        self.ext_select_all_btn.setEnabled(is_off)
        self.ext_deselect_all_btn.setEnabled(is_off)

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
                if isinstance(widget, FilterItemWidget):
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

        self.search_btn.setText(AppStrings.SEARCH_BTN_STOPPING)
        self.search_btn.setStyleSheet(UIStyles.STYLE_STOP_BTN_WAIT)

        try:
            if getattr(self, "scan_worker", None) is not None:
                if self.scan_worker and isValid(self.scan_worker):
                    self.scan_worker.stop()
                self.scan_worker = None

        except Exception as e:
            logger.debug(AppStrings.LOG_SCH_STOP_THREAD_ERR.format(e))

        try:
            if getattr(self, "worker", None) is not None:
                if self.worker and isValid(self.worker):
                    self.worker.stop()
                    # QRunnable은 setAutoDelete(True)가 기본이거나 이미 설정됨
                self.worker = None

        except Exception as e:
            logger.debug(AppStrings.LOG_SCH_STOP_THREAD_ERR.format(e))

        return True

    def start_search(self):
        """
        사용자 입력을 검증하고 병렬 검색 프로세스를 시작합니다.

        [프로세스 흐름]
        1. 검색 상태 및 입력값 검증 (State Machine)
        2. UI 초기화 및 검색어 히스토리 저장
        3. 필터(폴더, 확장자, 파일명) 수집 및 상세 로그 출력
        4. Rust 최적화 엔진(Fast Path) 또는 일반 검색 모드 결정
        5. QThreadPool을 통한 비동기 프로세스 실행 (ScanWorker -> SearchWorker)
        """
        try:
            if self.search_state != Constants.SearchState.IDLE:
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

            self.result_model.set_filename_filters(self.current_filename_filters)

            self.search_combo.hidePopup()
            self.filename_combo.hidePopup()

            self.logs_output.clear()
            self.tab_widget.setCurrentIndex(1)

            self._set_inputs_enabled(False)
            self.search_status_changed.emit(True)

            self.search_state = Constants.SearchState.SCANNING

            self.scan_start_time = time.time()

            self.config_manager.add_history(search_text)
            if filename_filter:
                self.config_manager.add_filename_history(filename_filter)

            self._load_histories()

            selected_folders = []
            for i in range(self.folder_list.count()):
                item = self.folder_list.item(i)
                widget = self.folder_list.itemWidget(item)
                if isinstance(widget, FilterItemWidget) and widget.isChecked():
                    # Mypy를 위한 타입 체크 또는 명시적 캐스팅
                    selected_folders.append(widget.text())

            selected_exts = []
            special_mode = self.special_search_combo.currentText()
            if special_mode == AppStrings.SPECIAL_SEARCH_OFF:
                for i in range(self.ext_list.count()):
                    item = self.ext_list.item(i)
                    widget = self.ext_list.itemWidget(item)
                    if isinstance(widget, FilterItemWidget) and widget.isChecked():
                        selected_exts.append(widget.text())
            else:
                if Constants.MODE_XML in special_mode:
                    selected_exts = [".xml"]
                elif Constants.MODE_JSON in special_mode:
                    selected_exts = [".json"]
                elif Constants.MODE_ARCHIVE in special_mode:
                    selected_exts = [".archive"]
                elif Constants.MODE_EXCEL in special_mode:
                    selected_exts = list(EXCEL_EXTS)
                else:
                    selected_exts = [special_mode.lower()]

            selected_filenames = []

            if filename_filter:
                # "npc, id" -> ["npc", "id"]로 분리
                splits = [s.strip() for s in filename_filter.split(",") if s.strip()]
                selected_filenames.extend(splits)

            for i in range(self.filename_list.count()):
                item = self.filename_list.item(i)
                widget = self.filename_list.itemWidget(item)
                if isinstance(widget, FilterItemWidget) and widget.isChecked():
                    selected_filenames.append(widget.text())

            self.current_filename_filters = selected_filenames

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

            if not selected_folders or not selected_exts:
                self.search_state = Constants.SearchState.IDLE
                self._set_inputs_enabled(True)
                QMessageBox.warning(self, AppStrings.ERROR_TITLE, AppStrings.ERROR_NO_SELECTION)
                return

            self.results_buffer = []
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
            self.result_file_filter_edit.clear()
            self.result_folder_filter_edit.clear()
            self.match_filter_1_edit.clear()
            self.match_filter_2_edit.clear()

            self.empty_label.setVisible(False)
            self.result_splitter.setVisible(False)
            for i in range(self.result_filter_layout.count()):
                layout_item = self.result_filter_layout.itemAt(i)
                if layout_item:
                    w = layout_item.widget()
                    if w:
                        w.setVisible(False)

            self.progress_update_requested.emit(0, 100, True)
            self.search_btn.setText(AppStrings.SEARCH_BTN_STOP)
            self.search_btn.setStyleSheet(UIStyles.STYLE_STOP_BTN_ACTIVE)
            self.search_btn.clicked.disconnect()
            self.search_btn.clicked.connect(self._stop_existing_search)

            self.start_timer = time.time()
            self.last_ui_update_time = time.time()

            from core.search_engine import HAS_RUST_ENGINE

            use_rust_fast_path = (
                HAS_RUST_ENGINE and special_mode == AppStrings.SPECIAL_SEARCH_OFF and not selected_filenames
            )

            if use_rust_fast_path:
                # scan_duration = time.time() - self.start_timer
                logger.info(AppStrings.LOG_SCH_SCAN_DONE_FAST_PATH)
                self.scan_start_time = self.start_timer
                self.search_stage_start = time.time()
                self.scanned_count = 0

                search_params = {
                    "file_list": [],
                    "search_string": search_text,
                    "special_mode": None,
                    "search_paths": selected_folders,
                    "extensions": selected_exts,
                    "filename_filter": selected_filenames,
                }
                self._setup_search_worker(search_params)
                self.skipped_files_list = []
                if self.worker:
                    QThreadPool.globalInstance().start(self.worker)
                return

            else:
                if HAS_RUST_ENGINE:
                    logger.info(AppStrings.LOG_SCH_RUST_MODE)
                else:
                    logger.info(AppStrings.LOG_SCH_PYTHON_MODE)

            special_mode = self.special_search_combo.currentText()
            self.scan_worker = ScanWorker(
                selected_folders, selected_exts, filename_filter, search_text, special_mode=special_mode
            )

            self.scan_worker.signals.progress_updated.connect(self._on_progress)
            self.scan_worker.signals.scan_finished.connect(self._on_scan_finished)
            self.scan_worker.signals.finished.connect(self._on_scan_thread_finished)
            self.scan_worker.signals.error.connect(self._on_search_error)

            if self.scan_worker:
                QThreadPool.globalInstance().start(self.scan_worker)

        except Exception as e:
            import traceback

            error_msg = AppStrings.ERROR_SEARCH_START_FAIL.format(str(e), traceback.format_exc())
            logger.error(error_msg)

            # UI 복구
            self.search_state = Constants.SearchState.IDLE
            self._set_inputs_enabled(True)
            self.progress_update_requested.emit(0, 0, False)

            if self.search_btn.text() == AppStrings.SEARCH_BTN_STOP:
                self._restore_search_button()

            QMessageBox.critical(
                self, AppStrings.ERROR_SEARCH_CRITICAL_TITLE, AppStrings.ERROR_SEARCH_CRITICAL_MSG.format(e)
            )

    def _on_scan_thread_finished(self):
        """스캔 프로세스가 종료되었을 때 참조를 정리합니다."""
        try:
            if self.worker is None:
                self._restore_search_button()
                self.status_message_requested.emit(AppStrings.STATUS_READY, 0)

                if self.search_state != Constants.SearchState.SEARCHING:
                    self.search_state = Constants.SearchState.IDLE
                    self._check_pending_restart()

            self.scan_worker = None
        except Exception as e:
            import traceback

            logger.error(f"_on_scan_thread_finished 중 오류 발생: {e}\n{traceback.format_exc()}")

    def _on_scan_finished(self, file_list):
        """스캔이 완료되면 실제 문자열 검색 프로세스를 실행합니다."""
        if self.search_state == Constants.SearchState.STOPPING or self.search_state == Constants.SearchState.IDLE:
            logger.info(AppStrings.LOG_SCH_STOPPED_BY_USER)
            return

        if self.scan_worker is None:
            logger.error(AppStrings.LOG_SYS_SCAN_WORKER_BROKEN)
            return

        search_text = self.scan_worker.search_string
        selected_folders = self.scan_worker.selected_folders
        selected_filenames = getattr(self.scan_worker, "filename_filter", None)

        if selected_filenames is None:
            selected_filenames = getattr(self, "current_filename_filters", [])

        self.result_model.set_filename_filters(selected_filenames)

        scan_duration = time.time() - self.scan_start_time
        logger.info(AppStrings.LOG_SCH_SCAN_DONE.format(len(file_list), scan_duration))

        self.scanned_count = len(file_list)
        self.search_stage_start = time.time()

        if file_list:
            self.search_state = Constants.SearchState.SEARCHING

        if not file_list:
            logger.info(AppStrings.LOG_SCH_NO_FILES)
            self.progress_update_requested.emit(0, 100, False)
            if not selected_folders:
                self.empty_label.setText(AppStrings.RESULT_EMPTY_NO_FOLDER)
            else:
                self.empty_label.setText(AppStrings.RESULT_EMPTY_NO_MATCH.format(search_text))

            self.empty_label.setStyleSheet(UIStyles.STYLE_EMPTY_LABEL.format(Constants.COLOR_RED))
            self.empty_label.setVisible(True)
            self._restore_search_button()
            self.status_message_requested.emit(AppStrings.STATUS_READY, 0)

            self.search_state = Constants.SearchState.IDLE
            self._check_pending_restart()
            return

        logger.info(AppStrings.LOG_WKR_INIT)
        special_mode_val = self.special_search_combo.currentText()
        final_special_mode: Optional[str] = special_mode_val
        if final_special_mode == AppStrings.SPECIAL_SEARCH_OFF:
            final_special_mode = None

        search_params = {
            "file_list": file_list,
            "search_string": search_text,
            "special_mode": final_special_mode,
            "filename_filter": selected_filenames,
        }
        self._setup_search_worker(search_params)
        self.skipped_files_list = []
        if self.worker:
            QThreadPool.globalInstance().start(self.worker)

    def _setup_search_worker(self, params):
        """SearchWorker 인스턴스를 생성하고 공통 시그널을 연결합니다."""
        if self.worker is not None:
            self.worker.stop()
            self.worker = None

        self.worker = SearchWorker(params)

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
            QTimer.singleShot(0, self.start_search)

    def _on_worker_finished(self):
        """검색 작업이 완전히 종료(취소나 정리 포함)되었을 때 호출됩니다."""
        if hasattr(self, "results_buffer") and self.results_buffer:
            self.result_model.add_results(self.results_buffer)
            self.total_files = len(self.results_buffer)
            self.results_buffer = []

        if self.total_files > 0 and not self.result_splitter.isVisible():
            self.result_splitter.setVisible(True)
            self.empty_label.setVisible(False)
            for i in range(self.result_filter_layout.count()):
                item = self.result_filter_layout.itemAt(i)
                if item:
                    widget = item.widget()
                    if widget:
                        widget.setVisible(True)

        self._restore_search_button()

    def _restore_search_button(self):
        """검색 버튼의 상태를 초기 '검색' 모드로 복구합니다."""
        self.search_btn.setText(AppStrings.SEARCH_BTN)
        self.search_btn.setStyleSheet(UIStyles.STYLE_SEARCH_BTN_PRIMARY)
        self.search_btn.clicked.disconnect()
        self.search_btn.clicked.connect(self.start_search)
        self.search_btn.setEnabled(True)

        self._set_inputs_enabled(True)
        self.search_status_changed.emit(False)

    def _set_inputs_enabled(self, enabled):
        """
        검색 중 UI 입력 요소들의 활성/비활성 여부를 설정합니다.

        Args:
            enabled (bool): True면 활성화, False면 비활성화
        """
        self.search_combo.setEnabled(enabled)

        self.filename_dock.widget().setEnabled(enabled)

        self.folder_dock.widget().setEnabled(enabled)

        self.ext_dock.widget().setEnabled(enabled)

        if enabled:
            current_mode = self.special_search_combo.currentText()
            self._on_special_search_changed(current_mode)

    def _on_skipped_found(self, file_paths):
        """스킵된 파일 목록을 누적합니다."""
        if not hasattr(self, "skipped_files_list"):
            self.skipped_files_list = []
        self.skipped_files_list.extend(file_paths)

    def _on_progress(self, current, total):
        """검색 작업 진행률 정보를 수신하여 UI에 반영합니다."""
        self.progress_update_requested.emit(current, total, True)
        self.status_message_requested.emit(AppStrings.STATUS_SEARCHING, 0)

    def _on_results_found(self, results):
        """워커로부터 전달받은 검색 결과를 버퍼에 저장합니다.
        UI 반응성 확보를 위해 실제 모델 추가는 검색 종료 후 수행합니다.
        """
        if results:
            self.results_buffer.extend(results)
            for _, count, _ in results:
                self.total_matches += count

            # match_summary = AppStrings.RESULT_SUMMARY_TEMPLATE.format(len(self.results_buffer), self.total_matches)

    def _show_matches_from_view(self, index):
        """파일 리스트에서 특정 항목을 클릭하면 해당 파일의 모든 매치 지점을 상세 뷰에 표시합니다."""
        source_index = self.proxy_model.mapToSource(index)
        file_path, matches = self.result_model.get_full_data(source_index.row())
        if file_path:
            search_text = self.search_combo.currentText()
            special_mode = self.special_search_combo.currentText()
            if special_mode == AppStrings.SPECIAL_SEARCH_OFF:
                special_mode = AppStrings.SPECIAL_SEARCH_OFF
            self.match_model.set_matches(file_path, matches, search_text=search_text, search_mode=special_mode)

            self._restore_column_widths(Constants.VIEW_MATCH)

            self._update_match_filter_ui(special_mode)

            m_header = self.match_view.horizontalHeader()
            m_header.setResizeContentsPrecision(100)
            col_count = self.match_model.columnCount()
            for i in range(col_count - 1):
                m_header.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
            m_header.setSectionResizeMode(col_count - 1, QHeaderView.ResizeMode.Stretch)

    def _on_view_clicked(self, index):
        """상세 매치 리스트에서 특정 행이 클릭되면 미리보기 패널에 해당 라인 주변 코드를 호출합니다."""
        source_index = self.match_proxy_model.mapToSource(index)
        line_no = self.match_model.get_line_no(source_index.row())
        file_path = self.match_model.current_file_path

        if line_no is not None:
            self.preview_text.clear()
            try:
                line_no_int = int(line_no)
                offset, length = self.match_model.get_match_info(source_index.row())
                if file_path and os.path.exists(file_path):
                    self._update_preview(file_path, line_no_int, offset=offset, length=length)
            except (ValueError, TypeError):
                self.preview_text.setPlainText(AppStrings.RESULT_PREVIEW_ERROR)

    def _update_preview(self, file_path, target_line, offset=None, length=None):
        """지정한 파일의 특정 라인 전후 맥락을 읽어 미리보기 패널에 렌더링합니다."""
        try:
            if not os.path.exists(file_path):
                self.preview_text.setPlainText(AppStrings.RESULT_PREVIEW_ERROR)
                return

            from core.search_engine import is_binary_file

            if is_binary_file(file_path):
                self.preview_text.setPlainText(AppStrings.RESULT_PREVIEW_ERROR)
                return

            special_mode = self.special_search_combo.currentText()

            if Constants.MODE_ARCHIVE in special_mode:
                self.preview_text.clear()
                return

            if Constants.MODE_EXCEL in special_mode:
                self.preview_text.setPlainText(AppStrings.RESULT_PREVIEW_ERROR)
                return

            from core.search_engine import detect_encoding_quickly

            encoding = Constants.ENC_UTF8
            try:
                with open(file_path, "rb") as f:
                    head = f.read(1024)
                    encoding = detect_encoding_quickly(head)
            except Exception:
                pass

            import mmap

            # [v4.33.2 Fix] 윈도우에서 발생할 수 있는 잠재적 크래시 및 파일 잠금 문제를 방지하기 위해 테스트 중에는 mmap 비활성화
            use_mmap = "PYTEST_CURRENT_TEST" not in os.environ

            preview_lines_data = []
            context_range = 5

            if use_mmap:
                with open(file_path, "rb") as f:
                    try:
                        f_size = os.path.getsize(file_path)
                        if f_size == 0:
                            self.preview_text.clear()
                            return

                        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                            if offset is not None and length is not None:
                                # [Zero-copy Optimization]
                                # Rust 엔진이 제공한 오프셋을 사용하여 즉시 해당 줄을 찾아냅니다.
                                # 대략적인 줄 번호 보정 필요 시

                                # 실제로는 리터럴 검색 결과의 오프셋이 정확하므로, 그 주변만 읽으면 됩니다.
                                # 하지만 context_range를 위해 주변 줄바꿈을 찾아야 합니다.

                                # [v4.33.11 Fix] Anchor-based Line Tracking
                                # 현재 줄의 시작점과 끝점을 먼저 확정하여 줄 번호 오차를 원천 차단

                                # 1. 현재 줄의 시작점 찾기
                                pos = mm.rfind(b"\n", 0, offset)
                                current_line_start = 0 if pos == -1 else pos + 1

                                # 2. 위로 context_range 만큼 줄바꿈 찾기
                                context_start = current_line_start
                                lines_above = 0
                                for _ in range(context_range):
                                    if context_start <= 0:
                                        break
                                    pos = mm.rfind(b"\n", 0, context_start - 1)
                                    if pos == -1:
                                        context_start = 0
                                        lines_above += 1
                                        break
                                    context_start = pos + 1
                                    lines_above += 1

                                # 3. 현재 줄의 끝점 찾기
                                match_end = offset + (length or 0)
                                pos = mm.find(b"\n", match_end)
                                current_line_end = f_size if pos == -1 else pos + 1

                                # 4. 아래로 context_range 만큼 줄바꿈 찾기
                                context_end = current_line_end
                                lines_below = 0
                                for _ in range(context_range):
                                    if context_end >= f_size:
                                        break
                                    pos = mm.find(b"\n", context_end)
                                    if pos == -1:
                                        context_end = f_size
                                        lines_below += 1
                                        break
                                    context_end = pos + 1
                                    lines_below += 1

                                chunk = mm[context_start:context_end]
                                lines = chunk.decode(encoding, errors="replace").splitlines()

                                # start_line_no: 현재 줄(target_line)에서 순수하게 위로 떨어진 줄 수(lines_above)를 뺌
                                start_line_no = max(1, target_line - lines_above)
                                for i, line in enumerate(lines[: lines_above + lines_below + 1]):
                                    preview_lines_data.append((start_line_no + i, line))
                            else:
                                # 기존 방식: 처음부터 검색 (Slow for large files)
                                current_pos = 0
                                line_offsets = [0]

                                for _ in range(target_line + context_range):
                                    pos = mm.find(b"\n", current_pos)
                                    if pos == -1:
                                        break
                                    current_pos = pos + 1
                                    line_offsets.append(current_pos)

                                start_idx = max(0, target_line - context_range - 1)
                                end_idx = min(len(line_offsets), target_line + context_range)

                                for i in range(start_idx, end_idx):
                                    s_off = line_offsets[i]
                                    e_off = line_offsets[i + 1] if i + 1 < len(line_offsets) else f_size
                                    line_bytes = mm[s_off:e_off]
                                    preview_lines_data.append(
                                        (i + 1, line_bytes.decode(encoding, errors="replace").rstrip())
                                    )
                    except Exception as e:
                        logger.debug(AppStrings.LOG_SCH_MMAP_FAILED.format(e))
                        use_mmap = False

            if not use_mmap:
                with open(file_path, "r", encoding=encoding, errors="replace") as f_text:
                    current_idx = 0
                    for line in f_text:
                        current_idx += 1
                        if current_idx < max(1, target_line - context_range):
                            continue
                        if current_idx > target_line + context_range:
                            break
                        preview_lines_data.append((current_idx, line.rstrip()))

            preview_content = "<div style='font-family: inherit; font-size: inherit; line-height: 1.4;'>"
            search_text = self.search_combo.currentText()
            special_mode = self.special_search_combo.currentText()
            is_json = Constants.MODE_JSON in special_mode
            is_xml = Constants.MODE_XML in special_mode
            is_exact = Constants.MODE_EXACT in special_mode

            for ln, content in preview_lines_data:
                from html import escape

                escaped_content = escape(content)

                highlighted = self.get_highlighted_html(escaped_content, search_text, is_xml, is_json, is_exact)

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
        if self.results_buffer:
            self.result_model.add_results(self.results_buffer)
            self.total_files = len(self.results_buffer)
            self.results_buffer = []

        # duration = time.time() - self.start_timer

        self.progress_update_requested.emit(self.scanned_count, self.scanned_count, False)

        if hasattr(self, "skipped_files_list") and self.skipped_files_list:
            logger.warning(AppStrings.LOG_SCH_SKIP_COUNT.format(len(self.skipped_files_list)))
            for item in self.skipped_files_list:
                if isinstance(item, tuple) and len(item) == 2:
                    f_path, reason = item
                    logger.warning(AppStrings.LOG_SCH_SKIP_REASON.format(f_path, reason))
                else:
                    logger.warning(AppStrings.LOG_SCH_SKIP_SIMPLE.format(item))

        self._restore_search_button()

        self.search_state = Constants.SearchState.IDLE

        self.tab_widget.setCurrentIndex(0)
        has_results = found_count > 0 and self.result_model.rowCount() > 0

        self.empty_label.setVisible(not has_results)
        self.result_splitter.setVisible(has_results)
        self.result_splitter.setVisible(has_results)
        for i in range(self.result_filter_layout.count()):
            item = self.result_filter_layout.itemAt(i)
            if item:
                widget = item.widget()
                if widget:
                    widget.setVisible(has_results)

        if has_results:
            self._update_pagination_ui()

        if has_results:
            QTimer.singleShot(100, self._auto_select_first_result)
        else:
            self.result_file_filter_edit.clear()
            self.result_folder_filter_edit.clear()
            self.match_filter_1_edit.clear()
            self.match_filter_2_edit.clear()

        self.result_view.setSortingEnabled(True)
        self.result_view.sortByColumn(0, Qt.SortOrder.DescendingOrder)

        r_header = self.result_view.horizontalHeader()
        r_header.setResizeContentsPrecision(100)
        r_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.result_view.setColumnWidth(0, 50)
        r_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        r_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

        elapsed = time.time() - self.start_timer
        search_stage_duration = time.time() - self.search_stage_start
        logger.info(AppStrings.LOG_SCH_SEARCH_DONE.format(search_stage_duration))

        # summary = AppStrings.SEARCH_FINISHED_MSG.format(self.total_files, found_count, skipped_count, float(elapsed))

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
        """작업 도중 발생하는 치명적 오류를 처리하고 사용자에게 알립니다."""
        logger.error(AppStrings.LOG_SCH_ERROR.format(error_msg))
        self.status_message_requested.emit(f"{AppStrings.STATUS_ERROR_PREFIX}{error_msg}", 5000)

        self._restore_search_button()

        self.search_state = Constants.SearchState.IDLE

    def _open_file_from_match_view(self, index):
        """매치 상세 뷰 더블클릭 시 해당 파일을 외부 편집기(연결 프로그램)로 엽니다."""
        file_path = self.match_model.current_file_path
        if file_path:
            from sf_utils.file_helper import open_file

            open_file(file_path)

    def _open_file_from_view(self, index):
        """결과 테이블 특정 항목 더블클릭 시 해당 파일을 연결 프로그램으로 실행합니다."""
        source_index = self.proxy_model.mapToSource(index)
        file_path, _ = self.result_model.get_full_data(source_index.row())
        if file_path:
            from sf_utils.file_helper import open_file

            open_file(file_path)

    def _show_result_context_menu(self, pos):
        """결과 리스트 관점에서 마우스 우클릭 시 열기, 복사, 내보내기 등의 컨텍스트 메뉴를 호출합니다."""
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

        if self.result_model.rowCount() > 0:
            export_action = QAction(AppStrings.RESULT_EXPORT_ALL, self)
            export_action.triggered.connect(self._export_results)
            menu.addAction(export_action)

        if not menu.isEmpty():
            menu.exec(self.result_view.viewport().mapToGlobal(pos))

    def _show_match_context_menu(self, pos):
        """매치 상세 뷰에서 개별 텍스트 내용을 복사할 수 있는 컨텍스트 메뉴를 엽니다."""
        index = self.match_view.indexAt(pos)
        if not index.isValid():
            return

        content = self.match_view.model().data(index, Qt.ItemDataRole.EditRole)

        menu = QMenu(self)
        copy_action = QAction(AppStrings.COPY_CONTENT, self)
        copy_action.triggered.connect(lambda: self._copy_to_clipboard(content))

        menu.addAction(copy_action)
        menu.exec(self.match_view.viewport().mapToGlobal(pos))

    def _setup_copy_shortcuts(self):
        """Ctrl+C 단축키를 테이블 뷰에 연결하여 사용자의 정의 복사 동작을 수행합니다."""
        from PySide6.QtGui import QShortcut, QKeySequence

        self.copy_result_shortcut = QShortcut(
            QKeySequence.StandardKey.Copy, self.result_view, context=Qt.ShortcutContext.WidgetShortcut
        )
        self.copy_result_shortcut.activated.connect(self._copy_selected_result_path)

        self.copy_match_shortcut = QShortcut(
            QKeySequence.StandardKey.Copy, self.match_view, context=Qt.ShortcutContext.WidgetShortcut
        )
        self.copy_match_shortcut.activated.connect(self._copy_selected_match_content)

    def _copy_selected_result_path(self):
        index = self.result_view.currentIndex()
        if not index.isValid():
            return
        source_index = self.proxy_model.mapToSource(index)
        file_path, _ = self.result_model.get_full_data(source_index.row())
        self._copy_to_clipboard(file_path)

    def _copy_selected_match_content(self):
        index = self.match_view.currentIndex()
        if not index.isValid():
            return
        content = self.match_view.model().data(index, Qt.ItemDataRole.EditRole)
        self._copy_to_clipboard(content)

    def _copy_to_clipboard(self, text):
        clipboard = QApplication.clipboard()
        clipboard.setText(text)

    def _open_specific_file(self, file_path):
        from sf_utils.file_helper import open_file

        open_file(file_path)

    def _open_file_location(self, file_path):
        """파일이 포함된 폴더를 열고, 가능하다면 해당 파일을 선택(highlight) 처리합니다."""
        if not os.path.exists(file_path):
            return

        import subprocess

        if os.name == "nt":
            subprocess.run(["explorer", "/select,", os.path.normpath(file_path)])
        else:
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
        """openpyxl을 사용하여 검색 결과 및 일부 매치 정보를 Excel 통합본으로 저장합니다."""
        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = AppStrings.EXCEL_SHEET_TITLE

        headers = [AppStrings.HEADER_COUNT, AppStrings.HEADER_FILE, AppStrings.EXCEL_MATCH_DETAIL]
        ws.append(headers)

        for row in range(self.result_model.rowCount()):
            path, matches = self.result_model.get_full_data(row)
            count = len(matches)

            matches_str = "\n".join([f"[{m[0]}] {m[1]}" for m in matches])

            ws.append([count, path, matches_str])
            ws.cell(row + 2, 3).alignment = openpyxl.styles.Alignment(wrapText=True, vertical="top")

        wb.save(file_path)

    def _export_to_text(self, file_path):
        """검색 결과를 일반 텍스트 문서 형식으로 내보냅니다."""
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(AppStrings.EXPORT_TEXT_HEADER.format(AppStrings.APP_TITLE) + "\n")
            f.write(f"{AppStrings.EXPORT_SUMMARY_PREFIX}{AppStrings.DOCK_RESULT_TITLE}\n\n")

            for row in range(self.result_model.rowCount()):
                path, matches = self.result_model.get_full_data(row)
                count = len(matches)

                f.write(f"[{count}] {path}\n")
                for line_no, content in matches:
                    f.write(AppStrings.EXPORT_TEXT_LINE_PREFIX.format(line_no, content))
                f.write(AppStrings.EXPORT_TEXT_SEPARATOR)

    def _auto_select_first_result(self):
        """검색 완료 후 첫 번째 행을 자동으로 선택하고 내용을 호출합니다."""
        if self.proxy_model.rowCount() > 0:
            index = self.proxy_model.index(0, 0)
            if index.isValid():
                self.result_view.selectRow(0)
                self.result_view.setCurrentIndex(index)
                self._show_matches_from_view(index)

    def _update_match_filter_ui(self, mode):
        if Constants.MODE_XML in mode:
            self.match_filter_1_edit.setVisible(True)
            self.match_filter_2_edit.setVisible(True)
            self.match_filter_3_edit.setVisible(False)
            self.match_filter_1_edit.setPlaceholderText(AppStrings.MATCH_FILTER_NAME_PLACEHOLDER)
            self.match_filter_2_edit.setPlaceholderText(AppStrings.MATCH_FILTER_CONTENT_PLACEHOLDER)

        elif Constants.MODE_JSON in mode:
            self.match_filter_1_edit.setVisible(True)
            self.match_filter_2_edit.setVisible(True)
            self.match_filter_3_edit.setVisible(False)
            self.match_filter_1_edit.setPlaceholderText(AppStrings.MATCH_FILTER_KEY_PLACEHOLDER)
            self.match_filter_2_edit.setPlaceholderText(AppStrings.MATCH_FILTER_VALUE_PLACEHOLDER)

        elif Constants.MODE_ARCHIVE in mode:
            self.match_filter_1_edit.setVisible(True)
            self.match_filter_2_edit.setVisible(True)
            self.match_filter_3_edit.setVisible(True)

            self.match_filter_1_edit.setPlaceholderText(AppStrings.MATCH_FILTER_ARCHIVE_NS_PLACEHOLDER)
            self.match_filter_2_edit.setPlaceholderText(AppStrings.MATCH_FILTER_ARCHIVE_SOURCE_PLACEHOLDER)
            self.match_filter_3_edit.setPlaceholderText(AppStrings.MATCH_FILTER_ARCHIVE_TRANS_PLACEHOLDER)

        elif Constants.MODE_EXCEL in mode:
            # Excel: [Position] [Value]
            self.match_filter_1_edit.setVisible(True)
            self.match_filter_2_edit.setVisible(True)
            self.match_filter_3_edit.setVisible(False)

            self.match_filter_1_edit.setPlaceholderText(AppStrings.MATCH_FILTER_EXCEL_POS_PLACEHOLDER)
            self.match_filter_2_edit.setPlaceholderText(AppStrings.MATCH_FILTER_EXCEL_VAL_PLACEHOLDER)

        else:
            # 기본 모드
            self.match_filter_1_edit.setVisible(True)
            self.match_filter_2_edit.setVisible(False)
            self.match_filter_3_edit.setVisible(False)

    def _on_match_filter_1_changed(self, text):
        """첫 번째 필터 입력 변경 시 처리"""
        mode = self.special_search_combo.currentText()
        col = 1
        if Constants.MODE_EXCEL in mode:
            col = 0
        self.match_proxy_model.setColumnFilter(col, text)

    def _on_match_filter_2_changed(self, text):
        """두 번째 필터 입력 변경 시 처리"""
        mode = self.special_search_combo.currentText()
        col = 2
        if Constants.MODE_ARCHIVE in mode:
            col = 3
        elif Constants.MODE_EXCEL in mode:
            col = 1
        self.match_proxy_model.setColumnFilter(col, text)

    def _on_match_filter_3_changed(self, text):
        """세 번째 필터 입력 변경 시 처리 (필터링 열 인덱스: 4)"""
        self.match_proxy_model.setColumnFilter(4, text)

    def _restore_column_widths(self, table_name):
        """저장된 설정에서 컬럼 너비를 불러와 테이블에 적용합니다."""
        widths = self.config_manager.get_column_widths(table_name)
        if not widths:
            return

        view = self.result_view if table_name == "result" else self.match_view
        header = view.horizontalHeader()

        header.blockSignals(True)

        if table_name == Constants.VIEW_RESULT:
            header.setResizeContentsPrecision(100)
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
            view.setColumnWidth(0, 50)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        elif table_name == "match":
            # 매치 테이블은 기본 헤더 설정을 따름
            pass
        else:
            for i, width in enumerate(widths):
                if i < header.count():
                    header.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
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
        주어진 HTML 텍스트 내용 중 검색 단어 강조 HTML을 반환합니다.
        속도 테스트를 위해 UI와 분리된 정적 메서드로 구현되었습니다.
        """
        if not search_text:
            return escaped_content

        pattern_str = re.escape(search_text)
        if is_exact:
            if is_xml or is_json:
                pattern_str = rf'(?<=["\'>]){pattern_str}(?=["\'<])'
            else:
                pattern_str = rf"(?<![a-zA-Z0-9._:-]){pattern_str}(?![a-zA-Z0-9._:-])"

        try:
            search_pattern = re.compile(pattern_str, re.IGNORECASE)
        except re.error:
            return escaped_content

        if is_json and ":" in escaped_content:
            parts = escaped_content.split(":", 1)
            key_part = parts[0]
            val_part = parts[1]
            highlighted_val = search_pattern.sub(
                lambda m: f"<span style='color: #ff9900; font-weight: bold;'>{m.group()}</span>", val_part
            )
            return f"{key_part}:{highlighted_val}"
        elif is_xml:
            highlighted = search_pattern.sub(
                lambda m: f"<span style='color: #ff9900; font-weight: bold;'>{m.group()}</span>", escaped_content
            )
            from html import escape

            tag_token = escape(search_text)
            bad_highlight = f"<{tag_token}"
            if bad_highlight in highlighted:
                highlighted = highlighted.replace(
                    f"<span style='color: #ff9900; font-weight: bold;'>{tag_token}</span>", tag_token
                )
            return highlighted

        return search_pattern.sub(
            lambda m: f"<span style='color: #ff9900; font-weight: bold;'>{m.group()}</span>", escaped_content
        )

    def stop_search(self):
        """검색을 중단합니다 (외부 호출용 wrapper)."""
        self._stop_existing_search()
