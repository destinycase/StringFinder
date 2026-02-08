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
)
from PySide6.QtCore import Qt, QThread, QByteArray, Signal
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
    체크박스와 제거 버튼이 포함된 커스텀 리스트 항목 위젯
    """

    def __init__(self, text, checked=True, on_delete=None, on_change=None):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 2, 5, 2)

        self.checkbox = QCheckBox(text)
        self.checkbox.setChecked(checked)
        if on_change:
            self.checkbox.stateChanged.connect(on_change)

        self.delete_btn = QPushButton(AppStrings.DELETE_BTN)
        self.delete_btn.setFixedWidth(50)
        self.delete_btn.setStyleSheet("QPushButton { color: #ff5555; }")  # 빨간색 텍스트로 강조
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
        super().__init__()
        self.config_manager = config_manager
        self.search_engine = SearchEngine()
        self.worker = None
        self.thread = None
        self.icon_provider = QFileIconProvider()

        self.total_matches = 0
        self.total_files = 0
        self.scanned_count = 0  # 검색 대상 총 파일 수
        self._init_ui()
        # _init_ui 내부의 _load_histories 호출로 대체 가능하므로 생성자에서는 제거해도 됨
        # (이미 _init_ui 끝에 추가함)

    def _init_ui(self):
        # 메인 레이아웃 (수직) - 여백 확대
        main_v_layout = QVBoxLayout(self)
        main_v_layout.setContentsMargins(10, 10, 10, 10)
        main_v_layout.setSpacing(10)

        # 1. 고정 검색 영역 (상단)
        # 검색어 입력 라인
        search_input_layout = QHBoxLayout()
        search_label = QLabel(AppStrings.SEARCH_LABEL)
        self.search_combo = HistoryComboBox()
        self.search_combo.setPlaceholderText(AppStrings.SEARCH_EDIT_PLACEHOLDER)
        self.search_combo.lineEdit().returnPressed.connect(self.start_search)
        self.search_combo.history_item_deleted.connect(lambda t: self._remove_history_item(t, "search"))
        self.search_combo.history_cleared.connect(lambda: self._clear_history("search"))

        self.search_btn = QPushButton(AppStrings.SEARCH_BTN)
        self.search_btn.clicked.connect(self.start_search)

        search_input_layout.addWidget(search_label)
        search_input_layout.addWidget(self.search_combo, 1)
        search_input_layout.addWidget(self.search_btn)

        # 파일명 필터 라인
        filename_layout = QHBoxLayout()
        filename_label = QLabel(AppStrings.FILENAME_FILTER_LABEL)
        self.filename_combo = HistoryComboBox()
        self.filename_combo.setPlaceholderText(AppStrings.FILENAME_EDIT_PLACEHOLDER)
        self.filename_combo.lineEdit().returnPressed.connect(self.start_search)
        self.filename_combo.history_item_deleted.connect(lambda t: self._remove_history_item(t, "filename"))
        self.filename_combo.history_cleared.connect(lambda: self._clear_history("filename"))

        filename_layout.addWidget(filename_label)
        filename_layout.addWidget(self.filename_combo, 1)

        # 메인 레이아웃 상단에 추가
        main_v_layout.addLayout(search_input_layout)
        main_v_layout.addLayout(filename_layout)

        # ---------------------------------------------------------
        # 2. 하단 조절 가능한 영역 (Main Splitter)
        # ---------------------------------------------------------
        self.main_h_splitter = QSplitter(Qt.Vertical)
        self.main_h_splitter.setHandleWidth(8)  # 메인 핸들 폭 조절

        # [A] 필터 설정 영역 (폴더, 확장자) - Splitter 적용
        self.filter_splitter = QSplitter(Qt.Horizontal)
        self.filter_splitter.setHandleWidth(6)  # 조작 편의를 위해 핸들 폭 강화

        # 폴더 필터 그룹
        folder_group = QGroupBox(AppStrings.FOLDER_GROUP)
        folder_vbox = QVBoxLayout(folder_group)
        folder_vbox.setContentsMargins(10, 15, 10, 10)  # 상단 타이틀 고려 여백 조절
        self.folder_list = QListWidget()
        filters = self.config_manager.get_filters()
        for folder in filters.get("folders", []):
            self._add_folder_item(folder)

        folder_btn_layout = QHBoxLayout()
        add_folder_btn = QPushButton(AppStrings.ADD_FOLDER_BTN)
        add_folder_btn.clicked.connect(self._add_folder)
        # UX 개선: 폴더 필터 토글 버튼
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

        # 확장자 필터 그룹
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
        # UX 개선: 확장자 필터 토글 버튼
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

        # [A] 결과 영역 (요약 + 내보내기 + 결과 스플리터)
        self.result_group = QGroupBox(AppStrings.RESULT_GROUP_TITLE)
        result_area_layout = QVBoxLayout(self.result_group)
        result_area_layout.setContentsMargins(10, 15, 10, 10)

        # 결과 분할 구조 (상하 스플리터: 파일 목록 / 상세 영역)
        self.result_splitter = QSplitter(Qt.Vertical)
        self.result_splitter.setHandleWidth(6)

        # 파일 리스트 view 및 모델
        self.result_view = QTableView()
        self.result_model = SearchResultModel(self.icon_provider)
        self.result_view.setModel(self.result_model)

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

        # [NEW] 하단 상세 영역용 좌우 스플리터 (상세 목록 / 미리보기)
        self.bottom_splitter = QSplitter(Qt.Horizontal)
        self.bottom_splitter.setHandleWidth(6)

        # 매칭 상세 view 및 모델
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

        # 상세 미리보기 패널
        self.preview_group = QGroupBox(AppStrings.RESULT_PREVIEW_TITLE)
        preview_layout = QVBoxLayout(self.preview_group)
        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_text.setLineWrapMode(QTextEdit.NoWrap)

        # OS별 고정폭 글꼴 설정 (Consolas/Menlo)
        font_family = AppStrings.FONT_PREVIEW_WIN
        if sys.platform == "darwin":
            font_family = AppStrings.FONT_PREVIEW_MAC
        font = QFont(font_family, 10)
        self.preview_text.setFont(font)
        preview_layout.addWidget(self.preview_text)

        # 구조 조립
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
        self.empty_label.setStyleSheet("color: #888; font-size: 14px; margin: 20px;")

        result_area_layout.addWidget(self.empty_label)
        result_area_layout.addWidget(self.result_splitter)

        result_area_layout.addWidget(self.empty_label)
        result_area_layout.addWidget(self.result_splitter)

        # 초기 가시성 설정
        self.result_splitter.setVisible(False)
        self.empty_label.setVisible(True)

        # 메인 스플리터에 조립
        self.main_h_splitter.addWidget(self.filter_splitter)
        self.main_h_splitter.addWidget(self.result_group)
        self.main_h_splitter.setStretchFactor(0, 1)
        self.main_h_splitter.setStretchFactor(1, 3)

        main_v_layout.addWidget(self.main_h_splitter)

        # 초기 데이터 로드
        self._load_histories()

        # 상태 복원
        main_state, result_state, filter_state = self.config_manager.get_splitter_states()
        if main_state:
            self.main_h_splitter.restoreState(QByteArray.fromHex(main_state.encode()))
        if result_state:
            self.result_splitter.restoreState(QByteArray.fromHex(result_state.encode()))
        if filter_state:
            self.filter_splitter.restoreState(QByteArray.fromHex(filter_state.encode()))

        # 초기 포커스를 검색창으로 설정
        self.search_combo.setFocus()

    def _load_histories(self):
        """히스토리 데이터를 콤보박스에 로드 (현재 입력값 유지)"""
        # 현재 입력값 백업 (수동 편집 중일 수 있음)
        current_search = self.search_combo.currentText()
        current_filename = self.filename_combo.currentText()

        self.search_combo.set_history(self.config_manager.get_history())
        self.filename_combo.set_history(self.config_manager.get_filename_history())

        # 입력값 복원
        self.search_combo.setEditText(current_search)
        self.filename_combo.setEditText(current_filename)

    def _remove_history_item(self, text, history_type):
        if history_type == "search":
            self.config_manager.remove_history_item(text)
        else:
            self.config_manager.remove_filename_history_item(text)
        self._load_histories()

    def _clear_history(self, history_type):
        if history_type == "search":
            self.config_manager.clear_history()
        else:
            self.config_manager.clear_filename_history()
        self._load_histories()

    def save_splitter_states(self):
        """슬라이더 상태를 설정 관리자에 저장"""
        self.config_manager.set_splitter_states(
            self.main_h_splitter.saveState(), self.result_splitter.saveState(), self.filter_splitter.saveState()
        )

    def _add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, AppStrings.SELECT_FOLDER_TITLE)
        if folder:
            # 중복 체크
            for i in range(self.folder_list.count()):
                widget = self.folder_list.itemWidget(self.folder_list.item(i))
                if widget and widget.text() == folder:
                    return
            self._add_folder_item(folder)
            self._sync_filters_to_config()

    def _add_folder_item(self, folder, checked=True):
        item = QListWidgetItem(self.folder_list)
        widget = FilterItemWidget(
            folder, checked, on_delete=lambda: self._delete_folder_item(item), on_change=self._sync_filters_to_config
        )
        item.setSizeHint(widget.sizeHint())
        self.folder_list.addItem(item)
        self.folder_list.setItemWidget(item, widget)

    def _delete_folder_item(self, item):
        row = self.folder_list.row(item)
        self.folder_list.takeItem(row)
        self._sync_filters_to_config()

    def _add_ext(self):
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
        item = QListWidgetItem(self.ext_list)
        widget = FilterItemWidget(
            ext, checked, on_delete=lambda: self._delete_ext_item(item), on_change=self._sync_filters_to_config
        )
        item.setSizeHint(widget.sizeHint())
        self.ext_list.addItem(item)
        self.ext_list.setItemWidget(item, widget)

    def _delete_ext_item(self, item):
        row = self.ext_list.row(item)
        self.ext_list.takeItem(row)
        self._sync_filters_to_config()

    def _sync_filters_to_config(self):
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

    # UX 개선: 필터 토글 함수
    def _toggle_all_filters(self, filter_type, select_all):
        """폴더 또는 확장자 필터를 모두 선택/해제합니다."""
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
        """기존 검색 작업을 즉각 중단하고 스레드를 정리함"""
        if self.worker:
            # 시그널 연결 해제하여 병렬 검색 중 발생할 수 있는 데이터 혼선 방지 (중요)
            try:
                self.worker.progress_updated.disconnect(self._on_progress)
                self.worker.results_found.disconnect(self._on_results_found)  # Changed from result_found
                self.worker.search_finished.disconnect(self._on_finished)
                self.worker.search_error.disconnect(self._on_error)
            except RuntimeError:
                pass  # 이미 시그널이 끊겨있는 경우 무시
            self.worker.stop()

        if self.thread and self.thread.isRunning():
            self.thread.quit()
            self.thread.wait(1000)  # 최대 1초 대기
            if self.thread.isRunning():
                self.thread.terminate()  # 강제 종료
                self.thread.wait()  # 종료 대기
        self.worker = None
        self.thread = None

    def start_search(self):
        """검색 프로세스를 시작합니다."""
        # 0. 기존 검색 중단 (중복 실행 방지)
        self._stop_existing_search()

        search_text = self.search_combo.currentText().strip()
        if not search_text:
            logger.debug(AppStrings.LOG_EMPTY_SEARCH_ABORTED)
            return

        filename_filter = self.filename_combo.currentText().strip()
        logger.info(AppStrings.LOG_SEARCH_STARTED.format(search_text, filename_filter))

        # 히스토리 저장 및 데이터베이스 동기화
        self.config_manager.add_history(search_text)
        if filename_filter:
            self.config_manager.add_filename_history(filename_filter)

        # UI 콤보박스 목록 갱신
        self._load_histories()

        # 현재 활성화된 필터 조건(폴더, 확장자) 수집
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

        # 1. 결과 UI 영역 초기화 및 검색 준비
        self.total_matches = 0
        self.total_files = 0
        self.result_view.setSortingEnabled(False)
        self.result_model.clear()
        self.match_model.clear()
        self.preview_text.clear()
        self.result_group.setTitle(AppStrings.RESULT_GROUP_TITLE)

        self.empty_label.setVisible(False)
        self.result_splitter.setVisible(False)

        # UX 개선: 상태 표시줄 프로그레스 바 표시
        self.progress_update_requested.emit(0, 100, True)
        # UX 개선: 검색 중 취소 버튼
        self.search_btn.setText(AppStrings.SEARCH_BTN_STOP)
        self.search_btn.setStyleSheet("QPushButton { background-color: #ff5555; color: white; }")
        self.search_btn.setEnabled(True)  # 검색 중에도 클릭 가능 (중지용)
        self.search_btn.clicked.disconnect()  # 기존 신호 해제
        self.search_btn.clicked.connect(self._stop_existing_search)  # 중지 기능 연결
        self.start_timer = time.time()

        # 파일 스캔 단계 (패턴 매칭 적용 - FileScanner 내부에서 로그 처리됨)
        scanner = FileScanner(selected_folders, selected_exts, filename_filter)
        file_list = scanner.scan()
        self.scanned_count = len(file_list)  # 검색 대상 파일 수 저장

        if not file_list:
            logger.info(AppStrings.LOG_NO_FILES_TO_SEARCH)
            # UX 개선: 상태 표시줄 프로그레스 바 숨김
            self.progress_update_requested.emit(0, 100, False)
            # UX 개선: 빈 결과 안내 개선
            if not selected_folders:
                self.empty_label.setText(AppStrings.RESULT_EMPTY_NO_FOLDER)
            else:
                self.empty_label.setText(AppStrings.RESULT_EMPTY_NO_MATCH.format(search_text))
            self.empty_label.setVisible(True)
            # 검색 버튼 복원
            self.search_btn.setText(AppStrings.SEARCH_BTN)
            self.search_btn.setStyleSheet("")
            self.search_btn.clicked.disconnect()
            self.search_btn.clicked.connect(self.start_search)
            self.search_btn.setEnabled(True)
            return

        # 2. 백그라운드 워커 실행 (병렬 처리)
        logger.info(AppStrings.LOG_BACKGROUND_WORKER_INIT)
        self.thread = QThread()
        self.worker = SearchWorker(self.search_engine, file_list, search_text)
        self.worker.moveToThread(self.thread)

        # 시그널 연결: 워커 이벤트 -> UI 업데이트
        self.thread.started.connect(self.worker.run)  # 스레드 시작 시 워커의 run 메서드 실행
        self.worker.progress_updated.connect(self._on_progress)  # 검색 진행 상황 업데이트
        self.worker.results_found.connect(self._on_results_found)  # 파일에서 일치하는 결과 발견 시 (배치 처리)
        self.worker.search_finished.connect(self._on_finished)  # 검색 작업 완료 시
        self.worker.search_error.connect(self._on_error)  # 검색 중 오류 발생 시

        # 스레드 종료 시 자원 자동 정리 (메모리 누수 방지)
        self.thread.finished.connect(self.thread.deleteLater)  # 스레드 종료 후 QThread 객체 삭제
        self.worker.finished.connect(self.worker.deleteLater)  # 워커 작업 완료 후 QObject 워커 객체 삭제

        self.thread.start()

    def _on_progress(self, current, total):
        # UX 개선: 상태 표시줄 프로그레스 바 업데이트
        self.progress_update_requested.emit(current, total, True)
        # 상태 표시줄에 진행 상황 표시
        self.status_message_requested.emit(AppStrings.STATUS_SEARCH_PROGRESS.format(current, total))

    def _on_results_found(self, results):
        """지연된 UI 업데이트를 위해 배치로 수신된 결과 처리"""
        # 첫 번째 결과가 오면 리스트 보여줌
        if not self.result_splitter.isVisible():
            self.result_splitter.setVisible(True)
            self.empty_label.setVisible(False)

        # 결과 모델에 추가
        self.result_model.add_results(results)

        for _, count, _ in results:
            self.total_matches += count
            self.total_files += 1

        # 요약 정보 갱신 (배치당 한 번만 수행하여 성능 향상)
        # 그룹박스 타이틀은 고정된 값으로 유지
        self.result_group.setTitle(AppStrings.RESULT_GROUP_TITLE)

    def _show_matches_from_view(self, index):
        file_path, matches = self.result_model.get_full_data(index.row())
        if file_path:
            self.match_model.set_matches(file_path, matches)

    def _on_view_clicked(self, index):
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
        """
        선택한 파일의 특정 라인 주변을 미리보기 텍스트 박스에 표시합니다.

        Args:
            file_path (str): 대상 파일 경로
            target_line (int): 강조할 라인 번호
        """
        try:
            # 엑셀 파일 등 바이너리 파일은 미리보기 지원 안 함
            ext = os.path.splitext(file_path)[1].lower()
            if ext in [".xlsx", ".xls", ".xlsm", ".xlsb"]:
                self.preview_text.setPlainText(AppStrings.RESULT_PREVIEW_ERROR)
                return

            if not os.path.exists(file_path):
                self.preview_text.setPlainText(AppStrings.RESULT_PREVIEW_ERROR)
                return

            # 파일 읽기 (인코딩 고려: 검색 시 사용된 것과 동일한 로직이 좋지만 여기서는 간결하게 시도)
            # 대용량 파일의 경우 전체 readlines는 위험하므로 실제 배포 시에는 최적화 대상임
            lines = []
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()

            total = len(lines)
            # 타겟 라인 전후 5줄씩 표시
            start = max(0, target_line - 1 - 5)
            end = min(total, target_line - 1 + 6)

            preview_content = ""
            for i in range(start, end):
                ln = i + 1
                content = lines[i].rstrip()
                if ln == target_line:
                    # 매칭 라인 강조 기호 표시
                    preview_content += f"> {ln:4}: {content}\n"
                else:
                    preview_content += f"  {ln:4}: {content}\n"

            self.preview_text.setPlainText(preview_content)
        except Exception as e:
            logger.error(f"Preview error: {e}")
            self.preview_text.setPlainText(AppStrings.RESULT_PREVIEW_ERROR)

    def _on_finished(self, total_found):
        self.search_btn.setEnabled(True)
        # UX 개선: 상태 표시줄 프로그레스 바 숨김
        self.progress_update_requested.emit(0, 100, False)

        # 스레드 정리 (동적 생성된 경우 대비)
        if self.thread:
            self.thread.quit()
            self.thread.wait()
            self.thread = None
        self.worker = None

        # 정렬 활성화
        self.result_view.setSortingEnabled(True)
        self.result_view.sortByColumn(0, Qt.DescendingOrder)  # 일치 수 많은 순 기본

        # 시간 측정 완료
        elapsed = time.time() - self.start_timer

        # 타이틀 업데이트 - 고정 타이틀 유지
        self.result_group.setTitle(AppStrings.RESULT_GROUP_TITLE)

        # 상태 표시줄에 검색 완료 요약 표시
        status_msg = AppStrings.STATUS_SEARCH_COMPLETED.format(
            self.scanned_count, self.total_files, self.total_matches, elapsed
        )
        self.status_message_requested.emit(status_msg)

        # UX 개선: 검색 버튼 복원
        self.search_btn.setText(AppStrings.SEARCH_BTN)
        self.search_btn.setStyleSheet("")
        self.search_btn.clicked.disconnect()
        self.search_btn.clicked.connect(self.start_search)
        self.search_btn.setEnabled(True)

    def _on_error(self, error_msg):
        logger.error(f"Search error: {error_msg}")
        # 상태 표시줄에 오류 메시지 표시
        self.status_message_requested.emit(f"검색 오류: {error_msg}")
        self.search_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        self._stop_existing_search()

    def _open_file_from_match_view(self, index):
        file_path = self.match_model.current_file_path
        if file_path:
            from utils.file_helper import open_file

            open_file(file_path)

    def _open_file_from_view(self, index):
        file_path, _ = self.result_model.get_full_data(index.row())
        if file_path:
            from utils.file_helper import open_file

            open_file(file_path)

    def _show_result_context_menu(self, pos):
        index = self.result_view.indexAt(pos)
        menu = QMenu(self)

        if index.isValid():
            file_path, _ = self.result_model.get_full_data(index.row())

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

        # 결과가 있는 경우에만 '모두 내보내기' 표시
        if self.result_model.rowCount() > 0:
            export_action = QAction(AppStrings.RESULT_EXPORT_ALL, self)
            export_action.triggered.connect(self._export_results)
            menu.addAction(export_action)

        if not menu.isEmpty():
            menu.exec(self.result_view.viewport().mapToGlobal(pos))

    def _show_match_context_menu(self, pos):
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
        if not os.path.exists(file_path):
            return

        import subprocess

        # Windows Explorer에서 파일을 선택한 상태로 폴더 열기
        if os.name == "nt":
            subprocess.run(["explorer", "/select,", os.path.normpath(file_path)])
        else:
            # macOS/Linux (단순 폴더 열기)
            folder = os.path.dirname(file_path)
            subprocess.run(["open" if sys.platform == "darwin" else "xdg-open", folder])

    def _export_results(self):
        if self.result_table.rowCount() == 0:
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
            logger.error(f"Export error: {str(e)}")

    def _export_to_excel(self, file_path):
        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = AppStrings.EXCEL_SHEET_TITLE

        # 헤더 작성
        headers = [AppStrings.RESULT_HEADER_COUNT, AppStrings.RESULT_HEADER_FILE, AppStrings.EXCEL_MATCH_DETAIL]
        ws.append(headers)

        # 데이터 작성
        for row in range(self.result_table.rowCount()):
            count = self.result_table.item(row, 0).text()
            path = self.result_table.item(row, 1).text()

            # 매칭 상세 정보 문자열화
            data_item = self.result_table.item(row, 0)
            _, matches = data_item.data(Qt.UserRole)
            matches_str = "\n".join([f"[{m[0]}] {m[1]}" for m in matches])

            ws.append([int(count), path, matches_str])

            # 셀 스타일 조절 (자동 줄바꿈 등)
            ws.cell(row + 2, 3).alignment = openpyxl.styles.Alignment(wrapText=True, vertical="top")

        wb.save(file_path)

    def _export_to_text(self, file_path):
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(AppStrings.EXPORT_TEXT_HEADER.format(AppStrings.APP_TITLE) + "\n")
            f.write(f"{AppStrings.EXPORT_SUMMARY_PREFIX}{self.result_group.title()}\n\n")

            for row in range(self.result_table.rowCount()):
                count = self.result_table.item(row, 0).text()
                path = self.result_table.item(row, 1).text()
                _, matches = self.result_table.item(row, 0).data(Qt.UserRole)

                f.write(f"[{count}] {path}\n")
                for line_no, content in matches:
                    f.write(f"   L{line_no}: {content}\n")
                f.write("-" * 50 + "\n")
