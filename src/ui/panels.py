from typing import Any, Dict, List, Union

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants
from ui.styles import UIStyles
from ui.widgets import HistoryComboBox


class FilterItemWidget(QWidget):
    """
    리스트 위젯 내에서 개별 필터 항목(폴더 또는 확장자)을 표시하는 커스텀 위젯입니다.
    선택 상태(체크박스)와 제거 버튼 기능을 포함합니다.
    """

    def __init__(self, text, checked=True, on_delete=None, on_change=None):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(8)
        self.checkbox = QCheckBox(text)
        self.checkbox.setChecked(checked)
        if on_change:
            self.checkbox.stateChanged.connect(on_change)
        self.delete_btn = QToolButton()
        self.delete_btn.setText(Constants.SYMBOL_CLOSE)
        self.delete_btn.setFixedSize(20, 20)
        self.delete_btn.setStyleSheet(
            "QToolButton { border: none; color: #888; font-weight: bold; } QToolButton:hover { color: #ff5555; }"
        )
        self.delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        if on_delete:
            self.delete_btn.clicked.connect(on_delete)
        layout.addWidget(self.checkbox)
        layout.addStretch()
        layout.addWidget(self.delete_btn)

    def text(self):
        return self.checkbox.text()

    def isChecked(self):
        return self.checkbox.isChecked()


class SearchOptionsPanel(QWidget):
    search_started = Signal()
    stop_requested = Signal()
    history_deleted = Signal(str, str)  # 유형, 텍스트
    history_cleared = Signal(str)  # 유형

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        input_layout = QHBoxLayout()
        label = QLabel(AppStrings.SEARCH_LABEL)
        self.search_combo = HistoryComboBox()
        self.search_combo.setPlaceholderText(AppStrings.SEARCH_EDIT_PLACEHOLDER)
        self.search_combo.setToolTip(AppStrings.SEARCH_EDIT_PLACEHOLDER)
        le = self.search_combo.lineEdit()
        if le:
            le.returnPressed.connect(self.search_started.emit)
        self.search_combo.history_item_deleted.connect(lambda t: self.history_deleted.emit(t, Constants.TYPE_SEARCH))
        self.search_combo.history_cleared.connect(lambda: self.history_cleared.emit(Constants.TYPE_SEARCH))
        input_layout.addWidget(label)
        input_layout.addWidget(self.search_combo, 1)
        layout.addLayout(input_layout)
        self.search_btn = QPushButton(AppStrings.SEARCH_BTN)
        self.search_btn.setMinimumHeight(40)
        self.search_btn.setStyleSheet(UIStyles.STYLE_SEARCH_BTN_PRIMARY)
        self.search_btn.clicked.connect(self.search_started.emit)
        layout.addWidget(self.search_btn)
        self.stop_btn = QPushButton(AppStrings.SEARCH_BTN_STOP)
        self.stop_btn.setMinimumHeight(40)
        self.stop_btn.setStyleSheet(UIStyles.STYLE_STOP_BTN_ACTIVE)  # [UX] 중지 버튼은 눈에 띄게 빨간색
        self.stop_btn.clicked.connect(self.stop_requested.emit)
        self.stop_btn.setVisible(False)
        layout.addWidget(self.stop_btn)
        options_layout = QHBoxLayout()
        self.complex_search_check = QCheckBox(Constants.MODE_COMPLEX + AppStrings.COMPLEX_SEARCH_LABEL)
        self.complex_search_check.setToolTip(AppStrings.COMPLEX_SEARCH_TOOLTIP)
        self.exclude_hidden_check = QCheckBox(AppStrings.EXCLUDE_HIDDEN_LABEL)
        self.exclude_hidden_check.setToolTip(AppStrings.EXCLUDE_HIDDEN_TOOLTIP)
        self.exclude_hidden_check.setChecked(True)  # 기본적으로 켜둠 (성능 권장)
        options_layout.addWidget(self.complex_search_check)
        options_layout.addWidget(self.exclude_hidden_check)
        options_layout.addStretch()
        layout.addLayout(options_layout)

    def set_searching(self, searching: bool):
        self.search_btn.setVisible(not searching)
        self.stop_btn.setVisible(searching)
        self.search_combo.setEnabled(not searching)
        self.complex_search_check.setEnabled(not searching)
        self.exclude_hidden_check.setEnabled(not searching)
        self.search_btn.setEnabled(True)
        self.search_btn.setText(AppStrings.SEARCH_BTN)
        self.search_btn.setStyleSheet(UIStyles.STYLE_SEARCH_BTN_PRIMARY)
        self.stop_btn.setEnabled(True)
        self.stop_btn.setText(AppStrings.SEARCH_BTN_STOP)
        self.stop_btn.setStyleSheet(UIStyles.STYLE_STOP_BTN_ACTIVE)

    def set_stopping_state(self):
        """set_stopping_state 함수."""
        self.stop_btn.setEnabled(False)
        self.stop_btn.setText(AppStrings.SEARCH_BTN_STOPPING)
        self.stop_btn.setStyleSheet(UIStyles.STYLE_STOP_BTN_WAIT)  # [UX] 중지 중일 때는 차분한 회색
        self.search_btn.setEnabled(False)
        self.search_btn.setText(AppStrings.SEARCH_BTN_STOPPING)
        self.search_btn.setStyleSheet(UIStyles.STYLE_STOP_BTN_WAIT)

    def get_search_text(self) -> str:
        return self.search_combo.currentText().strip()

    def is_complex_search(self) -> bool:
        return self.complex_search_check.isChecked()

    def is_exclude_hidden(self) -> bool:
        return self.exclude_hidden_check.isChecked()

    def set_search_history(self, items: List[str]):
        self.search_combo.addItems(items)
        self.search_combo.setCurrentIndex(-1)

    def add_history(self, text: str):
        """[Cleanup] 미사용 메서드 본문 제거 (감사 이슈 대응)"""
        pass

    def get_state(self) -> dict:
        return {
            Constants.STATE_KEY_SEARCH: self.search_combo.currentText(),
            Constants.PAYLOAD_USE_COMPLEX_SEARCH: self.complex_search_check.isChecked(),
            Constants.PAYLOAD_EXCLUDE_HIDDEN: self.exclude_hidden_check.isChecked(),
        }

    def load_state(self, state: dict):
        self.search_combo.set_current_text(state.get(Constants.STATE_KEY_SEARCH, ""))
        self.complex_search_check.setChecked(state.get(Constants.PAYLOAD_USE_COMPLEX_SEARCH, False))
        self.exclude_hidden_check.setChecked(state.get(Constants.PAYLOAD_EXCLUDE_HIDDEN, True))


class FolderFilterPanel(QWidget):
    filter_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 15, 10, 10)
        self.folder_list = QListWidget()
        # QListWidget 자체 스크롤을 활용하기 위해 최소 높이 제거하거나 적절히 설정
        self.folder_list.setMinimumHeight(50)
        main_layout.addWidget(self.folder_list, 1)  # 1: Stretch 부여

        btn_layout = QHBoxLayout()
        add_btn = QPushButton(AppStrings.ADD_FOLDER_BTN)
        add_btn.clicked.connect(self._on_add_clicked)
        sel_all_btn = QPushButton(AppStrings.SELECT_ALL_BTN)
        sel_all_btn.setFixedWidth(80)
        sel_all_btn.clicked.connect(lambda: self.toggle_all(True))
        desel_all_btn = QPushButton(AppStrings.DESELECT_ALL_BTN)
        desel_all_btn.setFixedWidth(80)
        desel_all_btn.clicked.connect(lambda: self.toggle_all(False))
        btn_layout.addWidget(add_btn)
        btn_layout.addWidget(sel_all_btn)
        btn_layout.addWidget(desel_all_btn)
        main_layout.addLayout(btn_layout)

    def _on_add_clicked(self):
        folder = QFileDialog.getExistingDirectory(self, AppStrings.SELECT_FOLDER_TITLE)
        if folder:
            self.add_folder(folder)

    def add_folder(self, folder: str, checked: bool = True):
        for i in range(self.folder_list.count()):
            widget = self.folder_list.itemWidget(self.folder_list.item(i))
            if isinstance(widget, FilterItemWidget) and widget.text() == folder:
                return
        item = QListWidgetItem(self.folder_list)
        widget = FilterItemWidget(
            folder, checked, on_delete=lambda: self._delete_item(item), on_change=lambda _: self.filter_changed.emit()
        )
        item.setSizeHint(widget.sizeHint())
        self.folder_list.addItem(item)
        self.folder_list.setItemWidget(item, widget)
        self.filter_changed.emit()

    def _delete_item(self, item):
        row = self.folder_list.row(item)
        self.folder_list.takeItem(row)
        self.filter_changed.emit()

    def toggle_all(self, checked: bool):
        for i in range(self.folder_list.count()):
            widget = self.folder_list.itemWidget(self.folder_list.item(i))
            if isinstance(widget, FilterItemWidget):
                widget.checkbox.setChecked(checked)
        self.filter_changed.emit()

    def get_selected_folders(self) -> List[str]:
        folders = []
        for i in range(self.folder_list.count()):
            widget = self.folder_list.itemWidget(self.folder_list.item(i))
            if isinstance(widget, FilterItemWidget) and widget.isChecked():
                folders.append(widget.text())
        return folders

    def get_all_folders(self) -> List[str]:
        folders = []
        for i in range(self.folder_list.count()):
            widget = self.folder_list.itemWidget(self.folder_list.item(i))
            if isinstance(widget, FilterItemWidget):
                folders.append(widget.text())
        return folders

    def set_items(self, folders: List[str]):
        self.folder_list.clear()
        for f in folders:
            self.add_folder(f, checked=True)

    def restore_state(self, data: Union[List[str], Dict[str, bool]]):
        self.folder_list.clear()
        if isinstance(data, list):
            for f in data:
                self.add_folder(f, checked=True)
        elif isinstance(data, dict):
            for f, checked in data.items():
                self.add_folder(f, checked=checked)

    def get_state(self) -> dict:
        folder_states = {}
        for i in range(self.folder_list.count()):
            item = self.folder_list.item(i)
            widget = self.folder_list.itemWidget(item)
            if isinstance(widget, FilterItemWidget):
                folder_states[widget.text()] = widget.checkbox.isChecked()
        return folder_states

    def load_state(self, state: dict):
        for i in range(self.folder_list.count()):
            item = self.folder_list.item(i)
            widget = self.folder_list.itemWidget(item)
            if isinstance(widget, FilterItemWidget) and widget.text() in state:
                widget.checkbox.setChecked(state[widget.text()])


class ExtensionFilterPanel(QWidget):
    filter_changed = Signal()
    special_mode_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 15, 10, 10)
        special_layout = QHBoxLayout()
        special_label = QLabel(AppStrings.SPECIAL_SEARCH_LABEL)
        self.special_combo = QComboBox()
        self.special_combo.addItems(AppStrings.SPECIAL_SEARCH_ITEMS)
        self.special_combo.currentTextChanged.connect(self._on_special_changed)
        special_layout.addWidget(special_label)
        special_layout.addWidget(self.special_combo, 1)
        main_layout.addLayout(special_layout)

        self.ext_list = QListWidget()
        self.ext_list.setMinimumHeight(50)
        main_layout.addWidget(self.ext_list, 1)  # 목록이 영역을 가득 채우고 스크롤 생성

        input_layout = QHBoxLayout()
        self.ext_edit = QLineEdit()
        self.ext_edit.setPlaceholderText(AppStrings.EXT_EDIT_PLACEHOLDER)
        self.ext_edit.returnPressed.connect(self._on_add_clicked)
        self.add_btn = QPushButton(AppStrings.ADD_EXT_BTN)
        self.add_btn.setFixedWidth(50)
        self.add_btn.clicked.connect(self._on_add_clicked)
        input_layout.addWidget(self.ext_edit)
        input_layout.addWidget(self.add_btn)
        main_layout.addLayout(input_layout)

        toggle_layout = QHBoxLayout()
        self.sel_all_btn = QPushButton(AppStrings.SELECT_ALL_BTN)
        self.sel_all_btn.clicked.connect(lambda: self.toggle_all(True))
        self.desel_all_btn = QPushButton(AppStrings.DESELECT_ALL_BTN)
        self.desel_all_btn.clicked.connect(lambda: self.toggle_all(False))
        toggle_layout.addWidget(self.sel_all_btn)
        toggle_layout.addWidget(self.desel_all_btn)
        main_layout.addLayout(toggle_layout)

    def _on_special_changed(self, text):
        is_off = text == AppStrings.SPECIAL_SEARCH_OFF
        self.ext_list.setEnabled(is_off)
        self.ext_edit.setEnabled(is_off)
        self.add_btn.setEnabled(is_off)
        self.sel_all_btn.setEnabled(is_off)
        self.desel_all_btn.setEnabled(is_off)
        self.special_mode_changed.emit(text)

    def _on_add_clicked(self):
        ext = self.ext_edit.text().strip().lower().replace(".", "")
        if ext:
            self.add_extension(ext)
            self.ext_edit.clear()

    def add_extension(self, ext: str, checked: bool = True):
        for i in range(self.ext_list.count()):
            widget = self.ext_list.itemWidget(self.ext_list.item(i))
            if isinstance(widget, FilterItemWidget) and widget.text() == ext:
                return
        item = QListWidgetItem(self.ext_list)
        widget = FilterItemWidget(
            ext, checked, on_delete=lambda: self._delete_item(item), on_change=lambda _: self.filter_changed.emit()
        )
        item.setSizeHint(widget.sizeHint())
        self.ext_list.addItem(item)
        self.ext_list.setItemWidget(item, widget)
        self.filter_changed.emit()

    def _delete_item(self, item):
        row = self.ext_list.row(item)
        self.ext_list.takeItem(row)
        self.filter_changed.emit()

    def toggle_all(self, checked: bool):
        for i in range(self.ext_list.count()):
            widget = self.ext_list.itemWidget(self.ext_list.item(i))
            if isinstance(widget, FilterItemWidget):
                widget.checkbox.setChecked(checked)
        self.filter_changed.emit()

    def get_selected_extensions(self) -> List[str]:
        exts = []
        special_mode = self.special_combo.currentText()
        if special_mode == AppStrings.SPECIAL_SEARCH_OFF:
            for i in range(self.ext_list.count()):
                widget = self.ext_list.itemWidget(self.ext_list.item(i))
                if isinstance(widget, FilterItemWidget) and widget.isChecked():
                    exts.append(widget.text())
        else:
            # 특수 모드 처리 시점은 여기 또는 컨트롤러 중 한 곳으로 일원화해야 합니다.
            if Constants.MODE_XML in special_mode:
                return [Constants.EXT_XML]
            elif Constants.MODE_JSON in special_mode:
                return [Constants.EXT_JSON]
            elif Constants.MODE_ARCHIVE in special_mode:
                return [Constants.EXT_ARCHIVE]
            elif Constants.MODE_EXCEL in special_mode:
                return list(Constants.EXT_EXCEL)
            else:
                return [special_mode.lower()]
        return exts

    def get_all_extensions(self) -> List[str]:
        exts = []
        for i in range(self.ext_list.count()):
            widget = self.ext_list.itemWidget(self.ext_list.item(i))
            if isinstance(widget, FilterItemWidget):
                exts.append(widget.text())
        return exts

    def get_special_mode(self) -> str:
        return self.special_combo.currentText()

    def set_items(self, exts: List[str]):
        self.ext_list.clear()
        for e in exts:
            self.add_extension(e)

    def restore_state(self, data: Union[List[str], Dict[str, Any]]):
        self.ext_list.clear()
        if isinstance(data, dict):
            if Constants.PAYLOAD_SPECIAL_MODE in data:
                self.special_combo.setCurrentText(data[Constants.PAYLOAD_SPECIAL_MODE])
            exts = data.get(Constants.CONFIG_KEY_EXTENSIONS, {})
            if Constants.CONFIG_KEY_EXTENSIONS not in data and Constants.PAYLOAD_SPECIAL_MODE not in data:
                exts = data
            if isinstance(exts, dict):
                for ext, checked in exts.items():
                    self.add_extension(ext, checked=checked)
            elif isinstance(exts, list):
                for ext in exts:
                    self.add_extension(ext, checked=True)
        elif isinstance(data, list):
            for ext in data:
                self.add_extension(ext, checked=True)

    def get_state(self) -> dict:
        ext_states = {}
        for i in range(self.ext_list.count()):
            item = self.ext_list.item(i)
            widget = self.ext_list.itemWidget(item)
            if isinstance(widget, FilterItemWidget):
                ext_states[widget.text()] = widget.checkbox.isChecked()
        return {
            Constants.PAYLOAD_SPECIAL_MODE: self.special_combo.currentText(),
            Constants.CONFIG_KEY_EXTENSIONS: ext_states,
        }

    def load_state(self, state: dict):
        self.special_combo.setCurrentText(state.get(Constants.PAYLOAD_SPECIAL_MODE, AppStrings.SPECIAL_SEARCH_OFF))
        ext_states = state.get(Constants.CONFIG_KEY_EXTENSIONS, {})
        for i in range(self.ext_list.count()):
            item = self.ext_list.item(i)
            widget = self.ext_list.itemWidget(item)
            if isinstance(widget, FilterItemWidget) and widget.text() in ext_states:
                widget.checkbox.setChecked(ext_states[widget.text()])


class FilenameFilterPanel(QWidget):
    filter_changed = Signal()
    search_triggered = Signal()  # 입력창에서 Enter로 검색 트리거
    history_deleted = Signal(str, str)
    history_cleared = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 15, 10, 10)
        combo_layout = QHBoxLayout()
        label = QLabel(AppStrings.FILENAME_FILTER_LABEL)
        self.filename_combo = HistoryComboBox()
        self.filename_combo.setPlaceholderText(AppStrings.FILENAME_EDIT_PLACEHOLDER)
        le = self.filename_combo.lineEdit()
        if le:
            le.returnPressed.connect(self.search_triggered.emit)
        self.filename_combo.history_item_deleted.connect(
            lambda t: self.history_deleted.emit(t, Constants.TYPE_FILENAME)
        )
        self.filename_combo.history_cleared.connect(lambda: self.history_cleared.emit(Constants.TYPE_FILENAME))
        combo_layout.addWidget(label)
        combo_layout.addWidget(self.filename_combo, 1)
        main_layout.addLayout(combo_layout)

        self.filename_list = QListWidget()
        self.filename_list.setMinimumHeight(50)
        main_layout.addWidget(self.filename_list, 1)

        add_layout = QHBoxLayout()
        self.add_edit = QLineEdit()
        self.add_edit.setPlaceholderText(AppStrings.FILENAME_LIST_PLACEHOLDER)
        self.add_edit.returnPressed.connect(self._on_add_clicked)
        add_btn = QPushButton(AppStrings.ADD_EXT_BTN)
        add_btn.setFixedWidth(50)
        add_btn.clicked.connect(self._on_add_clicked)
        add_layout.addWidget(self.add_edit)
        add_layout.addWidget(add_btn)
        main_layout.addLayout(add_layout)

        toggle_layout = QHBoxLayout()
        sel_all = QPushButton(AppStrings.SELECT_ALL_BTN)
        sel_all.clicked.connect(lambda: self.toggle_all(True))
        desel_all = QPushButton(AppStrings.DESELECT_ALL_BTN)
        desel_all.clicked.connect(lambda: self.toggle_all(False))
        toggle_layout.addWidget(sel_all)
        toggle_layout.addWidget(desel_all)
        main_layout.addLayout(toggle_layout)

    def _on_add_clicked(self):
        fn = self.add_edit.text().strip()
        if fn:
            self.add_filename(fn)
            self.add_edit.clear()

    def add_filename(self, fn: str, checked: bool = True):
        for i in range(self.filename_list.count()):
            widget = self.filename_list.itemWidget(self.filename_list.item(i))
            if isinstance(widget, FilterItemWidget) and widget.text() == fn:
                return
        item = QListWidgetItem(self.filename_list)
        widget = FilterItemWidget(
            fn, checked, on_delete=lambda: self._delete_item(item), on_change=lambda _: self.filter_changed.emit()
        )
        item.setSizeHint(widget.sizeHint())
        self.filename_list.addItem(item)
        self.filename_list.setItemWidget(item, widget)
        self.filter_changed.emit()

    def _delete_item(self, item):
        row = self.filename_list.row(item)
        self.filename_list.takeItem(row)
        self.filter_changed.emit()

    def toggle_all(self, checked: bool):
        for i in range(self.filename_list.count()):
            widget = self.filename_list.itemWidget(self.filename_list.item(i))
            if isinstance(widget, FilterItemWidget):
                widget.checkbox.setChecked(checked)
        self.filter_changed.emit()

    def get_filename_filter_text(self) -> str:
        return self.filename_combo.currentText().strip()

    def get_selected_filenames(self) -> List[str]:
        filenames = []
        combo_text = self.get_filename_filter_text()
        if combo_text:
            splits = [s.strip() for s in combo_text.split(",") if s.strip()]
            filenames.extend(splits)
        for i in range(self.filename_list.count()):
            widget = self.filename_list.itemWidget(self.filename_list.item(i))
            if isinstance(widget, FilterItemWidget) and widget.isChecked():
                filenames.append(widget.text())
        return filenames

    def get_all_list_filenames(self) -> List[str]:
        fns = []
        for i in range(self.filename_list.count()):
            widget = self.filename_list.itemWidget(self.filename_list.item(i))
            if isinstance(widget, FilterItemWidget):
                fns.append(widget.text())
        return fns

    def set_items(self, items: List[str]):
        self.filename_list.clear()
        for i in items:
            self.add_filename(i)

    def restore_state(self, data: Union[List[str], Dict[str, Any]]):
        self.filename_list.clear()
        target_data = data
        if isinstance(data, dict) and Constants.CONFIG_KEY_FILENAMES in data:
            self.filename_combo.setEditText(data.get(Constants.PAYLOAD_FILENAME_FILTER, ""))
            target_data = data.get(Constants.CONFIG_KEY_FILENAMES, {})
        if isinstance(target_data, list):
            for fn in target_data:
                self.add_filename(fn, checked=True)
        elif isinstance(target_data, dict):
            for fn, checked in target_data.items():
                self.add_filename(fn, checked=checked)

    def set_history(self, items: List[str]):
        self.filename_combo.addItems(items)
        self.filename_combo.setCurrentIndex(-1)

    def get_state(self) -> dict:
        filename_states = {}
        for i in range(self.filename_list.count()):
            item = self.filename_list.item(i)
            widget = self.filename_list.itemWidget(item)
            if isinstance(widget, FilterItemWidget):
                filename_states[widget.text()] = widget.checkbox.isChecked()
        return {
            Constants.PAYLOAD_FILENAME_FILTER: self.filename_combo.currentText(),
            Constants.CONFIG_KEY_FILENAMES: filename_states,
        }

    def load_state(self, state: dict):
        self.filename_combo.set_current_text(state.get(Constants.PAYLOAD_FILENAME_FILTER, ""))
        filename_states = state.get(Constants.CONFIG_KEY_FILENAMES, {})
        for i in range(self.filename_list.count()):
            item = self.filename_list.item(i)
            widget = self.filename_list.itemWidget(item)
            if isinstance(widget, FilterItemWidget) and widget.text() in filename_states:
                widget.checkbox.setChecked(filename_states[widget.text()])
