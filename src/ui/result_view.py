import os
import subprocess
import sys

from PySide6.QtCore import QByteArray, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants
from sf_utils.logger import logger
from ui.models import MatchDetailModel, SearchResultModel
from ui.proxies import MatchProxyModel, ResultProxyModel
from ui.styles import UIStyles
from ui.widgets import HtmlDelegate


class ResultView(QWidget):
    """
    검색 결과 테이블, 상세 매치 테이블, 미리보기 패널을 관리하는 복합 뷰입니다.
    페이지네이션 기능도 포함합니다.
    """

    file_double_clicked = Signal(str, int)  # 파일 경로, 줄 번호
    match_double_clicked = Signal(str, int)  # 파일 경로, 줄 번호
    status_message_requested = Signal(str, int)  # 메시지, 표시 시간(ms)

    def __init__(self, icon_provider, config_manager, parent=None):
        """__init__ 함수."""
        super().__init__(parent)
        self.icon_provider = icon_provider
        self.config_manager = config_manager
        self.current_filename_filters = []
        self.search_text = ""
        self.search_mode = Constants.MODE_NORMAL
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(200)  # [REQ 2.2] 크기 변경 멈춘 후 200ms 뒤 실행
        self._resize_timer.timeout.connect(self._adjust_column_widths)
        self._init_ui()
        self._restore_states()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        # [UI/UX] 사용자 요청에 따라 레이아웃 밀도를 높이기 위해 간격을 5px로 재조정합니다.
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
        self.summary_label = QLabel()
        self.summary_label.setStyleSheet(
            "font-weight: bold; color: #555; padding: 5px; background: #f0f0f0; border-radius: 4px; margin-bottom: 5px;"
        )
        self.summary_label.setVisible(False)
        self.result_view = QTableView()
        self.result_view.setStyleSheet(UIStyles.STYLE_TABLE_VIEW)
        self.result_model = SearchResultModel(self.icon_provider)
        self.proxy_model = ResultProxyModel()
        self.proxy_model.setSourceModel(self.result_model)
        self.proxy_model.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.result_view.setModel(self.proxy_model)
        self.result_view.setItemDelegate(HtmlDelegate(self.result_view))
        self.result_file_filter_edit.textChanged.connect(self.proxy_model.setFileFilter)
        self.result_folder_filter_edit.textChanged.connect(self.proxy_model.setFolderFilter)
        self.result_view.setAlternatingRowColors(True)
        self.result_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.result_view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.result_view.verticalHeader().hide()
        header = self.result_view.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setStretchLastSection(True)
        from PySide6.QtWidgets import QHeaderView

        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)  # 매치 수
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)  # 파일명
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)  # 폴더
        header.sectionResized.connect(lambda i, o, n: self._save_column_widths(Constants.VIEW_RESULT))
        self.result_view.setSortingEnabled(True)
        self.result_view.customContextMenuRequested.connect(self._show_result_context_menu)
        self.result_view.clicked.connect(self._on_result_clicked)
        # [UX] 키보드 위/아래 이동 시에도 즉시 데이터 로드
        self.result_view.selectionModel().currentRowChanged.connect(lambda curr, prev: self._on_result_clicked(curr))
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
        self.match_filter_1_edit.setPlaceholderText(AppStrings.MATCH_FILTER_LINE_PLACEHOLDER)
        self.match_filter_2_edit.setPlaceholderText(AppStrings.MATCH_FILTER_CONTENT_PLACEHOLDER)
        self.match_filter_layout.addWidget(self.match_filter_1_edit)
        self.match_filter_layout.addWidget(self.match_filter_2_edit)
        self.match_filter_layout.addWidget(self.match_filter_3_edit)
        self.match_filter_layout.addWidget(self.match_filter_4_edit)
        self.match_view = QTableView()
        self.match_view.setStyleSheet(UIStyles.STYLE_TABLE_VIEW)
        self.match_model = MatchDetailModel()
        self.match_proxy_model = MatchProxyModel()
        self.match_proxy_model.setSourceModel(self.match_model)
        self.match_view.setModel(self.match_proxy_model)
        self.match_view.setItemDelegate(HtmlDelegate(self.match_view))
        self.match_view.setFrameShape(QFrame.Shape.NoFrame)  # [UI] 불필요한 테두리 제거
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
        m_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)  # 라인/위치 (내용에 맞춤)
        m_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)  # 내용 (남는 공간 채움)
        # 나머지 extra 컬럼들 (JSON/XML Key, Value 등)
        for i in range(2, 9):
            m_header.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        m_header.setStretchLastSection(True)
        m_header.sectionResized.connect(self._on_match_column_resized)
        self.match_view.clicked.connect(self._on_match_clicked)
        self.match_view.selectionModel().currentRowChanged.connect(lambda curr, prev: self._on_match_clicked(curr))
        self.match_view.doubleClicked.connect(self._on_match_double_clicked)
        self.match_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.match_view.customContextMenuRequested.connect(self._show_match_context_menu)
        match_area_layout.setContentsMargins(5, 5, 5, 5)
        match_area_layout.setSpacing(5)
        match_area_layout.addLayout(self.match_filter_layout)
        match_area_layout.addWidget(self.match_view)
        # [UI/UX] 네비게이션 위젯을 테이블 하단으로 이동 (사용자 요청)
        self.match_pagination_widget = self._create_match_pagination_widget()
        match_area_layout.addWidget(self.match_pagination_widget)

        self.result_splitter = QSplitter(Qt.Orientation.Vertical)
        self.result_splitter.setHandleWidth(6)
        self.result_splitter.addWidget(self.result_list_container)
        self.result_splitter.addWidget(self.match_area_widget)
        self.result_splitter.setStretchFactor(0, 1)
        self.result_splitter.setStretchFactor(1, 1)
        main_layout.addWidget(self.summary_label)
        main_layout.addLayout(self.result_filter_layout)
        main_layout.addWidget(self.result_splitter)
        self.empty_label = QLabel(AppStrings.RESULT_EMPTY_MSG)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setStyleSheet(UIStyles.STYLE_SELECTION_INFO)
        main_layout.insertWidget(0, self.empty_label)
        self.result_splitter.setVisible(False)
        for i in range(self.result_filter_layout.count()):
            item = self.result_filter_layout.itemAt(i)
            if item:
                w = item.widget()
                if w:
                    w.setVisible(False)
        self._setup_copy_shortcuts()

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
        pagination_widget.setContentsMargins(0, 0, 10, 0)  # [UI] 우측 여백 확보
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
        # [신규] 페이지가 2개 이상일 때만 표시 (요구사항 5.1 반영)
        self.match_pagination_widget.setVisible(total > 1)

        # [UI/UX] 페이지 이동 시 컬럼 폭 재조정
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
        """_update_pagination_ui 함수."""
        self.result_view.setUpdatesEnabled(False)
        try:
            curr = self.result_model._current_page
            total = self.result_model.get_total_pages()
            self.current_page_edit.setText(str(curr))
            self.total_pages_label.setText(f"{AppStrings.PAGINATION_OF} {total}")
            self.prev_page_btn.setEnabled(curr > 1)
            self.next_page_btn.setEnabled(curr < total)
            # [신규] 페이지가 2개 이상일 때만 표시 (요구사항 5.1 반영)
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
            self.match_model.set_matches(path, matches, self.search_text, self.search_mode)
            self.update_match_filter_visibility(self.search_mode)
            self._update_match_pagination_ui()

            # [UI/UX] 파일 선택 시 컬럼 폭 최적화 호출 (타이밍 이슈 해결을 위해 100ms 지연)
            QTimer.singleShot(100, self._adjust_match_column_widths)

            # [UI/UX] 첫 번째 매치 자동 선택 (상세 및 미리보기 즉시 반영)
            if self.match_model.match_count > 0:
                QTimer.singleShot(0, lambda: self.match_view.selectRow(0))

    def _on_match_column_resized(self, index, old_width, new_width):
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
            return
        # [UI/UX] 미리보기 기능 제거로 인해 클릭 시 상태 업데이트 등만 수행
        pass

        pass

    def _on_match_double_clicked(self, index):
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
        self.result_model.add_results(results)

    def sort_results(self):
        """검색 종료 시 호출되어 전체 결과를 정렬합니다."""
        self.result_model.sort_globally()
        self._update_pagination_ui()
        self.update_ui_visibility()
        self._update_pagination_ui()

    def set_summary_info(self, file_count, match_count, duration, skip_count=0):
        """상단 요약 레이블에 검색 결과를 업데이트합니다."""
        if file_count > 0 or skip_count > 0:
            summary_text = AppStrings.RESULT_SUMMARY_FORMAT.format(
                file_count=file_count,
                match_count=match_count,
                skip_count=skip_count,
                duration=f"{duration:.2f}",
            )
            self.summary_label.setText(summary_text)
            self.summary_label.setVisible(True)
        else:
            self.summary_label.setText("")
            self.summary_label.setVisible(False)

    def clear(self):
        # [UI/UX] 검색 시작 시 이전 검색의 요약 정보를 즉시 제거하여 잔상이 남지 않도록 합니다.
        self.summary_label.setText("")
        self.summary_label.setVisible(False)
        self.result_model.clear()
        self.match_model.clear()
        self.update_ui_visibility()

    def update_ui_visibility(self):
        has_results = self.result_model.rowCount() > 0
        self.empty_label.setVisible(not has_results)
        self.result_splitter.setVisible(has_results)
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

    def set_search_context(self, search_text, search_mode):
        self.search_text = search_text
        # UI 콤보박스 값("끄기")을 내부 상수(Constants.MODE_NORMAL)로 정규화
        if search_mode == AppStrings.SPECIAL_SEARCH_OFF:
            self.search_mode = Constants.MODE_NORMAL
        else:
            self.search_mode = search_mode

    def set_results(self, results):
        """세션 로드 등 대량의 결과를 한꺼번에 설정할 때 사용합니다."""
        self.result_model.clear()
        if results:
            self.result_model.add_results(results)
        self.update_ui_visibility()
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

    def update_match_filter_visibility(self, mode):
        # UI 콤보박스 값("끄기") 정규화
        mode = Constants.MODE_NORMAL if mode == AppStrings.SPECIAL_SEARCH_OFF else (mode or Constants.MODE_NORMAL)

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
            self.match_filter_4_edit.setVisible(True)
            self.match_filter_1_edit.setPlaceholderText(AppStrings.MATCH_FILTER_ARCHIVE_NS_PLACEHOLDER)
            self.match_filter_2_edit.setPlaceholderText(AppStrings.MATCH_FILTER_ARCHIVE_KEY_PLACEHOLDER)
            self.match_filter_3_edit.setPlaceholderText(AppStrings.MATCH_FILTER_ARCHIVE_SOURCE_PLACEHOLDER)
            self.match_filter_4_edit.setPlaceholderText(AppStrings.MATCH_FILTER_ARCHIVE_TRANS_PLACEHOLDER)
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
            self.match_filter_1_edit.setPlaceholderText(AppStrings.MATCH_FILTER_CONTENT_PLACEHOLDER)

    def _on_match_filter_1_changed(self, text):
        """첫 번째 필터 입력 시 호출됩니다."""
        mode = str(self.search_mode or Constants.MODE_NORMAL)
        if mode == Constants.MODE_NORMAL:
            self.match_proxy_model.setColumnFilter(0, text)
        elif Constants.MODE_EXCEL in mode:
            self.match_proxy_model.setColumnFilter(0, text)  # 시트
        else:
            # Archive(NS), XML(Name), JSON(Key) -> Col 1
            self.match_proxy_model.setColumnFilter(1, text)

    def _on_match_filter_2_changed(self, text):
        """두 번째 필터 입력 시 호출됩니다."""
        mode = str(self.search_mode or Constants.MODE_NORMAL)
        if mode == Constants.MODE_NORMAL:
            return

        if Constants.MODE_EXCEL in mode:
            self.match_proxy_model.setColumnFilter(1, text)  # 셀
        elif Constants.MODE_ARCHIVE in mode:
            self.match_proxy_model.setColumnFilter(2, text)  # 키
        else:
            # XML(Value), JSON(Value) -> Col 2
            self.match_proxy_model.setColumnFilter(2, text)

    def _on_match_filter_3_changed(self, text):
        """세 번째 필터 입력 시 호출됩니다."""
        mode = str(self.search_mode or Constants.MODE_NORMAL)
        if mode == Constants.MODE_NORMAL:
            return

        if Constants.MODE_EXCEL in mode:
            self.match_proxy_model.setColumnFilter(2, text)  # 값
        elif Constants.MODE_ARCHIVE in mode:
            self.match_proxy_model.setColumnFilter(3, text)  # 소스
        else:
            self.match_proxy_model.setColumnFilter(3, text)

    def _on_match_filter_4_changed(self, text):
        """네 번째 필터 입력 시 호출됩니다."""
        mode = str(self.search_mode or Constants.MODE_NORMAL)
        if mode == Constants.MODE_NORMAL:
            return

        if Constants.MODE_ARCHIVE in mode:
            self.match_proxy_model.setColumnFilter(4, text)  # 번역
        else:
            self.match_proxy_model.setColumnFilter(4, text)

    def cleanup(self):
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
            copy_label = (
                AppStrings.COPY_CONTENT if len(selection) == 1 else f"{AppStrings.COPY_CONTENT} ({len(selection)})"
            )
            copy_action = QAction(copy_label, self)
            copy_action.triggered.connect(self._copy_selected_match_content)
            menu.addAction(copy_action)
            menu.addSeparator()
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
        ws = wb.active
        ws.title = AppStrings.EXCEL_SHEET_TITLE
        headers = [AppStrings.HEADER_COUNT, AppStrings.HEADER_FILE, AppStrings.EXCEL_MATCH_DETAIL]
        ws.append(headers)
        all_results = self.result_model.get_all_results()
        for i, (count, path, folder, full_path, matches) in enumerate(all_results):
            matches_str = "\n".join([f"[{m[0]}] {m[1]}" for m in matches])
            ws.append([count, full_path, matches_str])
            ws.cell(i + 2, 3).alignment = openpyxl.styles.Alignment(wrapText=True, vertical="top")
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
        """[REQ 2.1] 모든 테이블의 컬럼 폭을 내용에 맞춰 조정하고 마지막 컬럼을 확장합니다."""
        for view in [self.result_view, self.match_view]:
            header = view.horizontalHeader()
            # 1. 일단 자동 조절
            view.resizeColumnsToContents()
            # 2. 마지막 섹션 확장 설정
            header.setStretchLastSection(True)
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

        # 뷰가 가시 상태가 아니더라도 폭 계산이 필요한 경우가 있으므로 체크 완화
        # 단, 초기화 중이거나 위젯이 완전히 생성되지 않은 상태에서의 오류 방지
        if self.match_view.width() <= 0:
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
                self.match_view.resizeColumnsToContents()

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
            logger.debug(f"Adjust match columns fail: {e}")
        finally:
            header.blockSignals(False)
