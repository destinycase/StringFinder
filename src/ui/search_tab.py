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
    QAbstractItemView,
    QApplication,
    QFileIconProvider,
    QTableView,
    QTabWidget,
)
from PySide6.QtCore import Qt, QThread, QByteArray, Signal, QSortFilterProxyModel
from PySide6.QtGui import QFont
from core.worker import SearchWorker
from core.search_engine import SearchEngine, FileScanner
from utils.logger import logger
from utils.app_strings import AppStrings
from ui.widgets import HistoryComboBox
from ui.models import SearchResultModel, MatchDetailModel
import os
import sys
import time
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu


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
        self.delete_btn.setStyleSheet(f"QPushButton {{ {AppStrings.STYLE_DANGER_TEXT} }}")  # 삭제 동작 강조를 위한 스타일
        if on_delete:
            self.delete_btn.clicked.connect(on_delete)

        layout.addWidget(self.checkbox)
        layout.addStretch()
        layout.addWidget(self.delete_btn)

    def text(self):
        return self.checkbox.text()

    def isChecked(self):
        return self.checkbox.isChecked()


class SearchTab(QWidget):
    """
    개별 검색 세션을 담당하는 탭 위젯입니다.
    검색어 입력, 필터 설정, 결과 표시 및 미리보기 기능을 통합 제공합니다.
    """

    status_message_requested = Signal(str)  # 상태줄 업데이트용 시그널
    progress_update_requested = Signal(int, int, bool)  # UX 개선: 진행률 업데이트 시그널 (current, total, visible)

    def __init__(self, config_manager):
        """검색 탭 UI를 구성하고 초기 상태를 설정합니다."""
        super().__init__()
        self.config_manager = config_manager
        self.search_engine = SearchEngine()
        self.worker = None
        self.thread = None
        self.icon_provider = QFileIconProvider()

        self.total_matches = 0
        self.total_files = 0
        self.scanned_count = 0  # 검색 프로세스 전반에서 다룰 총 스캔 대상 파일 수
        self._init_ui()
        # _init_ui 내부의 _load_histories 호출로 대체 가능하므로 생성자에서는 제거해도 됨
        # (이미 _init_ui 끝에 추가함)

    def _init_ui(self):
        """검색 탭의 복잡한 레이아웃을 초기화합니다. (검색창, 필터 리스트, 결과 테이블 등)"""
        # 메인 수직 레이아웃 설정
        main_v_layout = QVBoxLayout(self)
        main_v_layout.setContentsMargins(10, 10, 10, 10)
        main_v_layout.setSpacing(10)

        # 1. 상단 고정 영역: 검색어 및 파일명 필터 입력부
        search_input_layout = QHBoxLayout()
        search_label = QLabel(AppStrings.SEARCH_LABEL)
        self.search_combo = HistoryComboBox()
        self.search_combo.setPlaceholderText(AppStrings.SEARCH_EDIT_PLACEHOLDER)
        self.search_combo.setToolTip(AppStrings.SEARCH_EDIT_PLACEHOLDER)
        self.search_combo.lineEdit().returnPressed.connect(self.start_search)
        self.search_combo.history_item_deleted.connect(lambda t: self._remove_history_item(t, "search"))
        self.search_combo.history_cleared.connect(lambda: self._clear_history("search"))



        self.search_btn = QPushButton(AppStrings.SEARCH_BTN)
        self.search_btn.clicked.connect(self.start_search)

        search_input_layout.addWidget(search_label)
        search_input_layout.addWidget(self.search_combo, 1)
        search_input_layout.addWidget(self.search_btn)

        filename_layout = QHBoxLayout()
        filename_label = QLabel(AppStrings.FILENAME_FILTER_LABEL)
        self.filename_combo = HistoryComboBox()
        self.filename_combo.setPlaceholderText(AppStrings.FILENAME_EDIT_PLACEHOLDER)
        self.filename_combo.lineEdit().returnPressed.connect(self.start_search)
        self.filename_combo.history_item_deleted.connect(lambda t: self._remove_history_item(t, "filename"))
        self.filename_combo.history_cleared.connect(lambda: self._clear_history("filename"))

        filename_layout.addWidget(filename_label)
        filename_layout.addWidget(self.filename_combo, 1)
        self.filename_combo.setPlaceholderText(AppStrings.FILENAME_EDIT_PLACEHOLDER)
        self.filename_combo.setToolTip(AppStrings.LOG_FILENAME_FILTER_GUIDE)

        main_v_layout.addLayout(search_input_layout)
        main_v_layout.addLayout(filename_layout)

        # 2. 하단 가변 영역: Splitter를 적용하여 공간 활용 최적화
        self.main_h_splitter = QSplitter(Qt.Vertical)
        self.main_h_splitter.setHandleWidth(8)

        # [A] 필터 설정 영역 (폴더 리스트 및 확장자 리스트)
        self.filter_splitter = QSplitter(Qt.Horizontal)
        self.filter_splitter.setHandleWidth(6)

        # 폴더 필터 그룹 설정
        folder_group = QGroupBox(AppStrings.FOLDER_GROUP)
        folder_vbox = QVBoxLayout(folder_group)
        folder_vbox.setContentsMargins(10, 15, 10, 10)
        self.folder_list = QListWidget()
        filters = self.config_manager.get_filters()
        for folder in filters.get("folders", []):
            self._add_folder_item(folder)

        folder_btn_layout = QHBoxLayout()
        add_folder_btn = QPushButton(AppStrings.ADD_FOLDER_BTN)
        add_folder_btn.clicked.connect(self._add_folder)
        
        # 필터 일괄 제어 버튼 (모두 선택/해제)
        self.folder_select_all_btn = QPushButton(AppStrings.SELECT_ALL_BTN)
        self.folder_select_all_btn.setFixedWidth(80)
        self.folder_select_all_btn.clicked.connect(lambda: self._toggle_all_filters("folder", True))
        self.folder_deselect_all_btn = QPushButton(AppStrings.DESELECT_ALL_BTN)
        self.folder_deselect_all_btn.setFixedWidth(80)
        self.folder_deselect_all_btn.clicked.connect(lambda: self._toggle_all_filters("folder", False))
        
        folder_btn_layout.addWidget(add_folder_btn)
        folder_btn_layout.addWidget(self.folder_select_all_btn)
        folder_btn_layout.addWidget(self.folder_deselect_all_btn)
        folder_vbox.addWidget(self.folder_list)
        folder_vbox.addLayout(folder_btn_layout)

        # 확장자 필터 그룹 설정
        ext_group = QGroupBox(AppStrings.EXT_GROUP)
        ext_vbox = QVBoxLayout(ext_group)
        ext_vbox.setContentsMargins(10, 15, 10, 10)
        self.ext_list = QListWidget()
        for ext in filters.get("extensions", []):
            self._add_ext_item(ext)

        ext_input_layout = QHBoxLayout()
        self.ext_edit = QLineEdit()
        self.ext_edit.setPlaceholderText(AppStrings.EXT_EDIT_PLACEHOLDER)
        self.ext_edit.returnPressed.connect(self._add_ext)
        add_ext_btn = QPushButton(AppStrings.ADD_EXT_BTN)
        add_ext_btn.setFixedWidth(50)
        add_ext_btn.clicked.connect(self._add_ext)
        ext_input_layout.addWidget(self.ext_edit)
        ext_input_layout.addWidget(add_ext_btn)
        
        ext_toggle_layout = QHBoxLayout()
        self.ext_select_all_btn = QPushButton(AppStrings.SELECT_ALL_BTN)
        self.ext_select_all_btn.clicked.connect(lambda: self._toggle_all_filters("ext", True))
        self.ext_deselect_all_btn = QPushButton(AppStrings.DESELECT_ALL_BTN)
        self.ext_deselect_all_btn.clicked.connect(lambda: self._toggle_all_filters("ext", False))
        ext_toggle_layout.addWidget(self.ext_select_all_btn)
        ext_toggle_layout.addWidget(self.ext_deselect_all_btn)
        
        ext_vbox.addWidget(self.ext_list)
        ext_vbox.addLayout(ext_input_layout)
        ext_vbox.addLayout(ext_toggle_layout)

        self.filter_splitter.addWidget(folder_group)
        self.filter_splitter.addWidget(ext_group)
        self.filter_splitter.setStretchFactor(0, 2)
        self.filter_splitter.setStretchFactor(1, 1)

        # [B] 결과 표시 영역 (검색 결과 테이블 / 로그 패널)
        self.result_group = QGroupBox(AppStrings.RESULT_GROUP_TITLE)
        result_area_layout = QVBoxLayout(self.result_group)
        result_area_layout.setContentsMargins(10, 15, 10, 10)

        self.tab_widget = QTabWidget()

        # [Tab 1] 검색 결과 세부 구성
        self.results_tab = QWidget()
        results_tab_layout = QVBoxLayout(self.results_tab)
        results_tab_layout.setContentsMargins(0, 5, 0, 0)

        # 상단 파일 리스트 / 하단 매칭 상세 및 미리보기를 구분하는 수직 스플리터
        self.result_splitter = QSplitter(Qt.Vertical)
        self.result_splitter.setHandleWidth(6)

        # 검색된 파일 리스트 테이블 뷰 및 모델 설정
        self.result_view = QTableView()
        self.result_model = SearchResultModel(self.icon_provider)
        
        # 결과 필터링을 위한 Proxy Model 설정
        self.proxy_model = QSortFilterProxyModel()
        self.proxy_model.setSourceModel(self.result_model)
        self.proxy_model.setFilterKeyColumn(1)  # 파일 경로 컬럼 기준 필터링
        self.proxy_model.setFilterCaseSensitivity(Qt.CaseInsensitive)
        
        self.result_view.setModel(self.proxy_model)

        # 결과 내 필터 입력란 추가
        self.result_filter_edit = QLineEdit()
        self.result_filter_edit.setPlaceholderText(AppStrings.RESULT_FILTER_PLACEHOLDER)
        self.result_filter_edit.textChanged.connect(self.proxy_model.setFilterFixedString)
        
        self.result_view.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.result_view.setColumnWidth(0, 80)
        self.result_view.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.result_view.setAlternatingRowColors(True)
        self.result_view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.result_view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.result_view.verticalHeader().hide()
        self.result_view.clicked.connect(self._show_matches_from_view)
        self.result_view.doubleClicked.connect(self._open_file_from_view)
        self.result_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.result_view.customContextMenuRequested.connect(self._show_result_context_menu)
        self.result_view.setSortingEnabled(True)

        # 하단 매칭 리스트 / 미리보기를 구분하는 수평 스플리터
        self.bottom_splitter = QSplitter(Qt.Horizontal)
        self.bottom_splitter.setHandleWidth(6)

        # 파일 내 구체적인 매칭 지점들을 보여주는 테이블 뷰
        self.match_view = QTableView()
        self.match_model = MatchDetailModel()
        self.match_view.setModel(self.match_model)

        self.match_view.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.match_view.setColumnWidth(0, 80)
        self.match_view.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.match_view.setAlternatingRowColors(True)
        self.match_view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.match_view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.match_view.verticalHeader().hide()
        self.match_view.clicked.connect(self._on_view_clicked)
        self.match_view.doubleClicked.connect(self._open_file_from_match_view)
        self.match_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.match_view.customContextMenuRequested.connect(self._show_match_context_menu)

        # 선택된 라인 주변 코드를 보여주는 미리보기 패널
        self.preview_group = QGroupBox(AppStrings.RESULT_PREVIEW_TITLE)
        preview_layout = QVBoxLayout(self.preview_group)
        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_text.setLineWrapMode(QTextEdit.NoWrap)

        # 고정폭 폰트 설정 (코드 표시 가독성 향상)
        font_family = AppStrings.FONT_PREVIEW_WIN
        if sys.platform == "darwin":
            font_family = AppStrings.FONT_PREVIEW_MAC
        font = QFont(font_family, 10)
        self.preview_text.setFont(font)
        preview_layout.addWidget(self.preview_text)

        # 스플리터 계층 조립
        self.bottom_splitter.addWidget(self.match_view)
        self.bottom_splitter.addWidget(self.preview_group)
        self.bottom_splitter.setStretchFactor(0, 1)
        self.bottom_splitter.setStretchFactor(1, 1)

        self.result_splitter.addWidget(self.result_view)
        self.result_splitter.addWidget(self.bottom_splitter)
        self.result_splitter.setStretchFactor(0, 1)
        self.result_splitter.setStretchFactor(1, 2)

        # 온보딩 메시지 (결과 없을 때)
        self.empty_label = QLabel(AppStrings.RESULT_EMPTY_MSG)
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet(AppStrings.STYLE_SELECTION_INFO)

        results_tab_layout.addWidget(self.empty_label)
        # 결과 탭 메인 레이아웃 구성 (필터 입력란 + 스플리터)
        results_tab_layout.addWidget(self.result_filter_edit)
        results_tab_layout.addWidget(self.result_splitter)

        # [Tab 2] 검색 로그
        self.logs_tab = QWidget()
        logs_tab_layout = QVBoxLayout(self.logs_tab)
        logs_tab_layout.setContentsMargins(0, 5, 0, 0)

        self.logs_output = QTextEdit()
        self.logs_output.setReadOnly(True)
        self.logs_output.setFont(font)  # 위에서 선언한 고정폭 글꼴 사용
        logs_tab_layout.addWidget(self.logs_output)

        # 실시간 로그 연결
        from utils.logger import qt_log_handler

        qt_log_handler.message_logged.connect(self.logs_output.append)

        self.tab_widget.addTab(self.results_tab, AppStrings.TAB_RESULTS)
        self.tab_widget.addTab(self.logs_tab, AppStrings.TAB_LOGS)

        result_area_layout.addWidget(self.tab_widget)

        # 초기 가시성 설정
        self.result_filter_edit.setVisible(False)
        self.result_splitter.setVisible(False)
        self.empty_label.setVisible(True)

        # 메인 스플리터에 조립
        self.main_h_splitter.addWidget(self.filter_splitter)
        self.main_h_splitter.addWidget(self.result_group)
        self.main_h_splitter.setStretchFactor(0, 1)
        self.main_h_splitter.setStretchFactor(1, 3)

        main_v_layout.addWidget(self.main_h_splitter)

        # 검색 창(검색어, 파일명 필터)의 이전 히스토리 데이터를 로드합니다.
        self._load_histories()

        # 각 스플리터(Splitter)의 크기 및 위치 상태를 이전 실행 상태로 복원합니다.
        main_state, result_state, filter_state = self.config_manager.get_splitter_states()
        if main_state:
            self.main_h_splitter.restoreState(QByteArray.fromHex(main_state.encode()))
        if result_state:
            self.result_splitter.restoreState(QByteArray.fromHex(result_state.encode()))
        if filter_state:
            self.filter_splitter.restoreState(QByteArray.fromHex(filter_state.encode()))

        self.search_combo.setFocus()

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
        if history_type == "search":
            self.config_manager.remove_history_item(text)
        else:
            self.config_manager.remove_filename_history_item(text)
        self._load_histories()

    def _clear_history(self, history_type):
        """전체 히스토리 내역을 삭제합니다."""
        if history_type == "search":
            self.config_manager.clear_history()
        else:
            self.config_manager.clear_filename_history()
        self._load_histories()

    def save_splitter_states(self):
        """현재 UI 스플리터들의 위치 상태를 설정 관리자에 저장합니다."""
        self.config_manager.set_splitter_states(
            self.main_h_splitter.saveState(), self.result_splitter.saveState(), self.filter_splitter.saveState()
        )

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

        self.config_manager.update_filters(folders, extensions)

    def _toggle_all_filters(self, filter_type, select_all):
        """모든 폴더 또는 확장자 필터의 체크 상태를 일괄 변경합니다."""
        if filter_type == "folder":
            for i in range(self.folder_list.count()):
                widget = self.folder_list.itemWidget(self.folder_list.item(i))
                if widget:
                    widget.checkbox.setChecked(select_all)
        elif filter_type == "ext":
            for i in range(self.ext_list.count()):
                widget = self.ext_list.itemWidget(self.ext_list.item(i))
                if widget:
                    widget.checkbox.setChecked(select_all)
        self._sync_filters_to_config()

    def _stop_existing_search(self):
        """현재 실행 중인 모든 검색 관련 객체들을 중단시키고 자원을 정리합니다."""
        if self.worker:
            # 새로운 검색 시작 시 이전 결과가 섞이지 않도록 시그널 연결을 선제적으로 해제합니다.
            try:
                self.worker.progress_updated.disconnect(self._on_progress)
                self.worker.results_found.disconnect(self._on_results_found)
                self.worker.search_finished.disconnect(self._on_finished)
                self.worker.search_error.disconnect(self._on_error)
            except RuntimeError:
                pass
            self.worker.stop()

        if self.thread and self.thread.isRunning():
            self.thread.quit()
            # 워커가 안전하게 멈추기를 기다린 후 종료되지 않으면 강제 중단합니다.
            if not self.thread.wait(1000):
                self.thread.terminate()
                self.thread.wait()
        
        self.worker = None
        self.thread = None

    def start_search(self):
        """사용자 입력을 검증하고 병렬 검색 워커 스레드를 시작합니다."""
        # 1. 진행 중인 검색이 있다면 즉시 중단합니다.
        self._stop_existing_search()

        search_text = self.search_combo.currentText().strip()
        if not search_text:
            self.status_bar.showMessage(AppStrings.LOG_EMPTY_SEARCH_ABORTED, 5000)
            return

        filename_filter = self.filename_combo.currentText().strip()
        if not filename_filter:
            self.status_bar.showMessage(AppStrings.LOG_SEARCH_ALL_FILES_GUIDE, 3000)
        else:
            self.status_bar.showMessage(AppStrings.LOG_FILENAME_FILTER_GUIDE, 3000)

        # 검색 시작과 동시에 불필요한 입력 UI 팝업을 닫습니다.
        self.search_combo.hidePopup()
        self.filename_combo.hidePopup()

        # 로그 확인이 용이하도록 로그 탭으로 즉시 전환하고 기존 출력을 비웁니다.
        self.logs_output.clear()
        self.tab_widget.setCurrentIndex(1)

        logger.info(AppStrings.LOG_SEARCH_STARTED.format(search_text, filename_filter))

        # 현재 검색 조건을 히스토리에 저장하고 UI를 동기화합니다.
        self.config_manager.add_history(search_text)
        if filename_filter:
            self.config_manager.add_filename_history(filename_filter)
        self._load_histories()

        # 활성화된 필터 조건(체크된 폴더 및 확장자)을 수집합니다.
        selected_folders = []
        for i in range(self.folder_list.count()):
            widget = self.folder_list.itemWidget(self.folder_list.item(i))
            if widget and widget.isChecked():
                selected_folders.append(widget.text())

        selected_exts = []
        for i in range(self.ext_list.count()):
            widget = self.ext_list.itemWidget(self.ext_list.item(i))
            if widget and widget.isChecked():
                selected_exts.append(widget.text())

        # 검색 결과를 담을 모델과 UI 상태를 전역적으로 초기화합니다.
        self.total_matches = 0
        self.total_files = 0
        self.result_view.setSortingEnabled(False)
        self.result_model.clear()
        self.match_model.clear()
        self.preview_text.clear()
        self.result_group.setTitle(AppStrings.RESULT_GROUP_TITLE)
        self.result_filter_edit.clear() # 필터 입력란 초기화

        self.empty_label.setVisible(False)
        self.result_splitter.setVisible(False)
        self.result_filter_edit.setVisible(False) # 필터 입력란 숨김

        # 프로그레스 바를 노출하고 검색 버튼의 역할을 '중단'으로 변경하여 시각적 피드백을 제공합니다.
        self.progress_update_requested.emit(0, 100, True)
        self.search_btn.setText(AppStrings.SEARCH_BTN_STOP)
        self.search_btn.setStyleSheet(AppStrings.STYLE_STOP_BTN_ACTIVE)
        self.search_btn.clicked.disconnect()
        self.search_btn.clicked.connect(self._stop_existing_search)
        
        self.start_timer = time.time()

        # 2. 파일 목록 스캔 단계 (패턴 매칭 및 시스템 콜 최소화를 통한 고속 스캐닝)
        scan_start_time = time.time()
        scanner = FileScanner(selected_folders, selected_exts, filename_filter)
        file_list = scanner.scan()
        scan_duration = time.time() - scan_start_time
        logger.info(AppStrings.LOG_SCAN_COMPLETED.format(len(file_list), scan_duration))

        self.scanned_count = len(file_list)
        self.search_stage_start = time.time()

        # 스캔된 파일이 없는 경우 작업을 종료하고 안내 메시지를 표시합니다.
        if not file_list:
            logger.info(AppStrings.LOG_NO_FILES_TO_SEARCH)
            self.progress_update_requested.emit(0, 100, False)
            if not selected_folders:
                self.empty_label.setText(AppStrings.RESULT_EMPTY_NO_FOLDER)
            else:
                self.empty_label.setText(AppStrings.RESULT_EMPTY_NO_MATCH.format(search_text))
            self.empty_label.setVisible(True)
            
            # 검색 버튼 원상 복구
            self._restore_search_button()
            return

        # 3. 실제 문자열 검색 단계 (백그라운드 워커를 통한 비동기 병렬 처리)
        logger.info(AppStrings.LOG_BACKGROUND_WORKER_INIT)
        self.thread = QThread()
        self.worker = SearchWorker(self.search_engine, file_list, search_text)
        self.worker.moveToThread(self.thread)

        # 스레드와 워커의 시그널들을 연결하여 실시간 피드백을 구현합니다.
        self.thread.started.connect(self.worker.run)
        self.worker.progress_updated.connect(self._on_progress)
        self.worker.results_found.connect(self._on_results_found)
        self.worker.search_finished.connect(self._on_finished)
        self.worker.search_error.connect(self._on_error)

        # 작업 완료 후 객체가 자동으로 소멸되도록 연결합니다.
        self.thread.finished.connect(self.thread.deleteLater)
        self.worker.finished.connect(self.worker.deleteLater)

        self.thread.start()

    def _restore_search_button(self):
        """검색 버튼의 상태를 초기 '검색' 모드로 복구합니다."""
        self.search_btn.setText(AppStrings.SEARCH_BTN)
        self.search_btn.setStyleSheet("")
        self.search_btn.clicked.disconnect()
        self.search_btn.clicked.connect(self.start_search)
        self.search_btn.setEnabled(True)

    def _on_progress(self, current, total):
        """워커 작업 진행률 정보를 수신하여 UI에 반영합니다."""
        self.progress_update_requested.emit(current, total, True)
        self.status_message_requested.emit(AppStrings.STATUS_SEARCH_PROGRESS.format(current, total))

    def _on_results_found(self, results):
        """워커로부터 전달받은 검색 결과 배치(batch)를 모델에 추가하고 요약을 갱신합니다."""
        # 대량의 결과가 한꺼번에 유입될 때 UI 프리징을 최소화하기 위해 강제 렌더링을 시도합니다.
        if len(results) > 1000:
            logger.info(AppStrings.LOG_UI_DISPLAYING_RESULTS)
            QApplication.processEvents()

        # 첫 번째 유효한 결과가 도착하면 테이블 뷰를 활성화합니다.
        if not self.result_splitter.isVisible():
            self.result_splitter.setVisible(True)
            self.empty_label.setVisible(False)
            self.result_filter_edit.setVisible(True) # 필터 입력란 표시

        self.result_model.add_results(results)

        for _, count, _ in results:
            self.total_matches += count
            self.total_files += 1

        self.result_group.setTitle(AppStrings.RESULT_GROUP_TITLE)

    def _show_matches_from_view(self, index):
        """파일 리스트에서 특정 항목이 클릭되면 해당 파일의 모든 매칭 지점을 상세 뷰에 표시합니다."""
        # Proxy Model 인덱스를 원본 모델 인덱스로 변환합니다.
        source_index = self.proxy_model.mapToSource(index)
        file_path, matches = self.result_model.get_full_data(source_index.row())
        if file_path:
            self.match_model.set_matches(file_path, matches)

    def _on_view_clicked(self, index):
        """상세 매칭 리스트에서 특정 행이 클릭되면 미리보기 패널에 해당 라인 주변 코드를 노출합니다."""
        line_no = self.match_model.get_line_no(index.row())
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
            # 엑셀과 같은 이진 파일 포맷은 미리보기를 지원하지 않습니다.
            ext = os.path.splitext(file_path)[1].lower()
            if ext in [".xlsx", ".xls", ".xlsm", ".xlsb"]:
                self.preview_text.setPlainText(AppStrings.RESULT_PREVIEW_ERROR)
                return

            if not os.path.exists(file_path):
                self.preview_text.setPlainText(AppStrings.RESULT_PREVIEW_ERROR)
                return

            # 인코딩 오류를 방지하기 위해 utf-8 ignore 모드로 파일을 순차적으로 읽습니다.
            # 대용량 파일에서 메모리 문제를 피하려면 개선 여지가 있습니다.
            lines = []
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()

            total = len(lines)
            # 매칭 라인을 중심으로 상하 5줄 정도의 문맥을 확보합니다.
            start = max(0, target_line - 1 - 5)
            end = min(total, target_line - 1 + 6)

            preview_content = ""
            for i in range(start, end):
                ln = i + 1
                content = lines[i].rstrip()
                if ln == target_line:
                    # 일치하는 라인은 시각적으로 강조 기호를 덧붙입니다.
                    preview_content += f"> {ln:4}: {content}\n"
                else:
                    preview_content += f"  {ln:4}: {content}\n"

            self.preview_text.setPlainText(preview_content)
        except Exception as e:
            logger.error(f"Preview error: {e}")
            self.preview_text.setPlainText(AppStrings.RESULT_PREVIEW_ERROR)

    def _on_finished(self, total_found):
        """검색 작업이 전체 종료되면 버튼 상태를 복구하고 요약 정보를 출력합니다."""
        self.search_btn.setEnabled(True)
        self.progress_update_requested.emit(0, 100, False)

        # 자원 정리를 시도합니다. 스레드가 살아있다면 안전하게 종료되기를 기다립니다.
        if self.thread:
            self.thread.quit()
            self.thread.wait()
            self.thread = None
        self.worker = None

        # 결과가 존재하는 경우 로그 탭 대신 결과 탭으로 자동 전환하여 즉시 결과 창을 보여줍니다.
        if total_found > 0:
            self.tab_widget.setCurrentIndex(0)
            self.result_filter_edit.setVisible(True) # 필터 입력란 표시
        else:
            self.result_filter_edit.setVisible(False) # 필터 입력란 숨김


        # 결과 테이블 정렬을 활성화하고 빈도가 높은 순으로 기본 정렬합니다.
        self.result_view.setSortingEnabled(True)
        self.result_view.sortByColumn(0, Qt.DescendingOrder)

        # 최종 성능 데이터 및 요약 메시지를 생성합니다.
        elapsed = time.time() - self.start_timer
        search_stage_duration = time.time() - self.search_stage_start
        logger.info(AppStrings.LOG_SEARCH_COMPLETED_STEP.format(search_stage_duration))

        self.result_group.setTitle(AppStrings.RESULT_GROUP_TITLE)

        status_msg = AppStrings.STATUS_SEARCH_COMPLETED.format(
            self.scanned_count, self.total_files, self.total_matches, elapsed
        )
        self.status_message_requested.emit(status_msg)

        # 중단 모드였던 검색 버튼을 다시 '검색' 모드로 복원합니다.
        self._restore_search_button()

    def _on_error(self, error_msg):
        """작업 도중 발생한 치명적 오류를 처리하고 사용자에게 알립니다."""
        formatted_error = AppStrings.ERROR_SEARCH_LOG.format(error_msg)
        logger.error(formatted_error)
        self.status_message_requested.emit(formatted_error)
        self.search_btn.setEnabled(True)
        self._stop_existing_search()
        self._restore_search_button()

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
        file_path, _ = self.result_model.get_full_data(index.row())
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
            logger.error(AppStrings.ERROR_EXPORT.format(str(e)))

    def _export_to_excel(self, file_path):
        """openpyxl을 사용하여 검색 결과 및 세부 매칭 정보를 Excel 통합본으로 저장합니다."""
        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = AppStrings.EXCEL_SHEET_TITLE

        # 테이블 헤더 구성
        headers = [AppStrings.RESULT_HEADER_COUNT, AppStrings.RESULT_HEADER_FILE, AppStrings.EXCEL_MATCH_DETAIL]
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
            f.write(f"{AppStrings.EXPORT_SUMMARY_PREFIX}{self.result_group.title()}\n\n")

            for row in range(self.result_model.rowCount()):
                path, matches = self.result_model.get_full_data(row)
                count = len(matches)

                f.write(f"[{count}] {path}\n")
                for line_no, content in matches:
                    f.write(AppStrings.EXPORT_TEXT_LINE_PREFIX.format(line_no, content))
                f.write(AppStrings.EXPORT_TEXT_SEPARATOR)
