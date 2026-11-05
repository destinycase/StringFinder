from typing import Any, Dict, List, Union, cast

from PySide6.QtCore import QEvent, QPoint, QRect, QRegularExpression, QSignalBlocker, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QBrush,
    QIcon,
    QPainter,
    QPalette,
    QPolygon,
    QRegularExpressionValidator,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QMenu,
    QPushButton,
    QStyle,
    QSizePolicy,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants
from core.search_query import validate_search_query
from ui.styles import UIStyles


class SearchInputComboBox(QComboBox):
    """Editable input; only accepted searches are inserted into shared history."""

    def __init__(self, parent=None, history_provider=None):
        super().__init__(parent)
        self.history_provider = history_provider
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)

    def load_history(self, history):
        current_text = self.currentText()
        items = []
        for text in history:
            if not isinstance(text, str) or not text.strip() or text in items:
                continue
            try:
                validate_search_query(text)
            except ValueError:
                continue
            items.append(text)
            if len(items) == 20:
                break
        # Reloading items must not clear the user's draft or trigger a search.
        with QSignalBlocker(self):
            self.clear()
            self.addItems(items)
            self.setEditText(current_text)

    def showPopup(self):
        if self.history_provider is not None:
            self.load_history(self.history_provider())
        super().showPopup()

    def set_current_text(self, text: str):
        self.setCurrentText(text)


class SearchSettingsButton(QPushButton):
    """검색 설정 선택 버튼입니다. 글자는 왼쪽에, 눌러서 고르는 버튼임을 알리는 ▼는 오른쪽 끝에 그립니다."""

    ARROW_WIDTH = 8
    ARROW_HEIGHT = 5
    ARROW_MARGIN = 9
    # 글자가 ▼와 겹치지 않도록 오른쪽에 비워 두는 폭입니다.
    RESERVED_RIGHT = ARROW_WIDTH + ARROW_MARGIN * 2

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        group = QPalette.ColorGroup.Active if self.isEnabled() else QPalette.ColorGroup.Disabled
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(self.palette().color(group, QPalette.ColorRole.ButtonText)))
        right = self.width() - self.ARROW_MARGIN
        top = (self.height() - self.ARROW_HEIGHT) // 2
        painter.drawPolygon(QPolygon([
            QPoint(right - self.ARROW_WIDTH, top),
            QPoint(right, top),
            QPoint(right - self.ARROW_WIDTH // 2, top + self.ARROW_HEIGHT),
        ]))
        painter.end()


class DragHandle(QWidget):
    """목록 항목을 끌어서 옮길 수 있음을 알리는 좁은 점 무늬 손잡이입니다.

    마우스 입력은 받지 않고 목록으로 넘겨, 목록이 끌어서 옮기기를 처리합니다.
    """

    WIDTH = 6

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(self.WIDTH)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip(AppStrings.FILTER_ITEM_REORDER_TOOLTIP)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(Qt.PenStyle.NoPen)
        group = QPalette.ColorGroup.Active if self.isEnabled() else QPalette.ColorGroup.Disabled
        painter.setBrush(QBrush(self.palette().color(group, QPalette.ColorRole.PlaceholderText)))
        # 2열 × 3행의 작은 점으로 손잡이를 표시합니다.
        top = (self.height() - 10) // 2
        for row in range(3):
            for column in range(2):
                painter.drawRect(column * 4, top + row * 4, 2, 2)
        painter.end()


class FilterItemWidget(QWidget):
    """
    리스트 위젯 내에서 개별 필터 항목(폴더 또는 확장자)을 표시하는 커스텀 위젯입니다.
    선택 상태(체크박스)와 제거 버튼 기능을 포함합니다.
    """

    def __init__(self, text, checked=True, on_delete=None, on_change=None):
        super().__init__()
        # 순서를 옮길 때 같은 동작의 위젯을 다시 만들 수 있도록 생성 인자를 보관합니다.
        self._on_delete = on_delete
        self._on_change = on_change
        self._compact = None
        self._indent = 0
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 4, 2)
        layout.setSpacing(8)
        # 손잡이와 빈 공간의 마우스 입력은 목록으로 전달되어 끌어서 옮기기를 시작합니다.
        self.drag_handle = DragHandle()
        layout.addWidget(self.drag_handle)
        # 손잡이와 체크박스 사이는 다른 간격보다 좁게 둡니다.
        layout.addSpacing(3 - layout.spacing())
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

    def set_compact(self, compact):
        self._compact = compact
        layout = self.layout()
        if layout is not None:
            layout.setContentsMargins(2 + self._indent, 0 if compact else 2, 4, 0 if compact else 2)
            self.setMinimumHeight(20 if compact else 24)

    def set_indent(self, indent: int):
        """그룹 안 항목처럼 왼쪽을 들여 써서 소속을 보여줍니다."""
        if indent == self._indent:
            return
        self._indent = indent
        layout = self.layout()
        if layout is not None:
            margins = layout.contentsMargins()
            layout.setContentsMargins(2 + indent, margins.top(), margins.right(), margins.bottom())

    def text(self):
        return self.checkbox.text()

    def isChecked(self):
        return self.checkbox.isChecked()

    def clone(self):
        """순서를 옮길 때 다시 붙일, 이름·체크·밀도·콜백이 같은 새 위젯을 만듭니다."""
        widget = FilterItemWidget(self.text(), self.isChecked(), on_delete=self._on_delete, on_change=self._on_change)
        if self._compact is not None:
            widget.set_compact(self._compact)
        return widget


class FolderGroupHeader(FilterItemWidget):
    """검색 폴더 그룹의 머리글 행입니다.

    그룹 안 폴더를 한꺼번에 선택·해제하는 체크(일부만 선택되면 부분 표시), 선택/전체 개수, 펼치기/접기 단추를 보여줍니다.
    체크가 일반 폴더 행의 체크와 같은 세로줄에 오도록 펼치기 단추는 개수 오른쪽에 둡니다.
    """

    def __init__(self, name, collapsed=False, on_delete=None, on_toggle=None, on_check=None):
        super().__init__(name, False, on_delete=on_delete, on_change=None)
        self._on_toggle = on_toggle
        self._on_check = on_check
        self.collapsed = collapsed
        layout = cast(QHBoxLayout, self.layout())
        self.toggle_btn = QToolButton()
        self.toggle_btn.setAutoRaise(True)
        self.toggle_btn.setFixedSize(16, 16)
        self.toggle_btn.setToolTip(AppStrings.FOLDER_GROUP_TOGGLE_TOOLTIP)
        self.toggle_btn.clicked.connect(lambda: self._on_toggle(self) if self._on_toggle else None)
        font = self.checkbox.font()
        font.setBold(True)
        self.checkbox.setFont(font)
        self.checkbox.setTristate(True)
        self.checkbox.setToolTip(AppStrings.FOLDER_GROUP_CHECK_TOOLTIP)
        # Qt의 세 단계 순환 대신 패널이 "모두 선택/모두 해제"로 정합니다.
        self.checkbox.clicked.connect(lambda _checked=False: self._on_check(self) if self._on_check else None)
        self.count_label = QLabel()
        self.count_label.setStyleSheet("QLabel { color: #888; }")
        layout.insertWidget(layout.indexOf(self.checkbox) + 1, self.count_label)
        layout.insertWidget(layout.indexOf(self.count_label) + 1, self.toggle_btn)
        self.set_collapsed(collapsed)

    def isChecked(self):
        return self.checkbox.checkState() == Qt.CheckState.Checked

    def set_collapsed(self, collapsed: bool):
        self.collapsed = collapsed
        self.toggle_btn.setArrowType(Qt.ArrowType.RightArrow if collapsed else Qt.ArrowType.DownArrow)

    def set_counts(self, checked: int, total: int):
        self.count_label.setText(f"({checked}/{total})")

    def clone(self):
        widget = FolderGroupHeader(self.text(), self.collapsed, on_delete=self._on_delete,
                                   on_toggle=self._on_toggle, on_check=self._on_check)
        if self._compact is not None:
            widget.set_compact(self._compact)
        return widget


class FilterListWidget(QListWidget):
    """Scroll filter text normally, but keep row actions outside the viewport.

    항목은 손잡이나 빈 공간을 끌어서, 또는 Alt+↑/↓로 순서를 옮길 수 있습니다.
    Qt 기본 드래그는 항목을 지웠다 다시 넣으면서 행 위젯을 잃으므로 이동을 직접 처리합니다.
    """

    ACTION_WIDTH = 28
    AUTO_SCROLL_MARGIN = 12
    items_reordered = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._actions = []
        self._position_pending = False
        self._drag_row = -1
        self._drag_origin = QPoint()
        self._dragging = False
        self._drop_indicator = QFrame(self.viewport())
        self._drop_indicator.setFrameShape(QFrame.Shape.HLine)
        self._drop_indicator.setStyleSheet("QFrame { background-color: palette(highlight); border: none; }")
        self._drop_indicator.setFixedHeight(2)
        self._drop_indicator.hide()
        # 함께 옮길 행 수와 놓을 수 있는 자리를 목록을 쓰는 패널이 바꿀 수 있습니다(예: 폴더 그룹).
        self.block_size = lambda row: 1
        self.snap_gap = lambda row, gap: gap
        self._action_column = QWidget(self)
        self._action_column.setAutoFillBackground(True)
        self._action_column.setBackgroundRole(QPalette.ColorRole.Base)
        self.setViewportMargins(0, 0, self.ACTION_WIDTH, 0)
        self.viewport().installEventFilter(self)
        # 빈 목록 안내 상자입니다. 패널이 목록 바로 위 레이아웃에 넣어, 다른 안내 상자와 같은 폭으로 위쪽에 붙습니다.
        # 목록은 비었는지에 따라 보이기만 정합니다.
        self.empty_hint = QLabel(self)
        self.empty_hint.setObjectName("filterEmptyHint")
        self.empty_hint.setWordWrap(True)
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.empty_hint.setContentsMargins(*UIStyles.GUIDE_BOX_PADDING)
        self.empty_hint.setStyleSheet(UIStyles.STYLE_GUIDE_BOX)
        # 값만 검색처럼 다른 안내가 우선할 때는 빈 목록 안내를 감춥니다.
        self.empty_hint_suppressed = False

    def setItemWidget(self, item, widget):
        super().setItemWidget(item, widget)
        if isinstance(widget, FilterItemWidget):
            button = widget.delete_btn
            item_layout = cast(QHBoxLayout, widget.layout())  # FilterItemWidget creates this layout.
            item_layout.removeWidget(button)
            button.setParent(self._action_column)
            button.hide()
            self._actions.append((item, button))
            # Removing the action must not remove the original row-height floor.
            widget.setMinimumHeight(20 + item_layout.contentsMargins().top()
                                    + item_layout.contentsMargins().bottom())
            item.setSizeHint(widget.sizeHint())
            # Coalesce inserts: measuring every existing row after each insert
            # makes a restored list quadratic in size.
            if not self._position_pending:
                self._position_pending = True
                QTimer.singleShot(0, self, self._position_actions)

    def refresh_empty_hint(self):
        self.empty_hint.setVisible(self.count() == 0 and not self.empty_hint_suppressed)

    def _position_actions(self):
        self._position_pending = False
        if not hasattr(self, "_action_column"):
            return
        viewport = self.viewport().geometry()
        self.refresh_empty_hint()
        self._action_column.setGeometry(viewport.right() + 1, viewport.top(), self.ACTION_WIDTH, viewport.height())
        for item, button in self._actions:
            rect = self.visualItemRect(item)
            visible = rect.isValid() and rect.bottom() >= 0 and rect.top() < viewport.height()
            button.setVisible(visible)
            if visible:
                button.move((self.ACTION_WIDTH - button.width()) // 2,
                            rect.top() + (rect.height() - button.height()) // 2)
        self._action_column.raise_()

    def eventFilter(self, watched, event):
        if watched is self.viewport() and event.type() in (QEvent.Type.Resize, QEvent.Type.Paint):
            self._position_actions()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_actions()

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        self._position_actions()

    def doItemsLayout(self):
        super().doItemsLayout()
        self._position_actions()

    def takeItem(self, row):
        item = self.item(row)
        retained = []
        for action_item, button in self._actions:
            if action_item is item:
                button.hide()
                button.deleteLater()
            else:
                retained.append((action_item, button))
        self._actions = retained
        taken = super().takeItem(row)
        self._position_actions()
        return taken

    def clear(self):
        for _, button in self._actions:
            button.hide()
            button.deleteLater()
        self._actions.clear()
        super().clear()
        self._position_actions()

    def drop_gap_at(self, y: int) -> int:
        """뷰포트 y 위치에 놓았을 때 들어갈 자리(0~행 수)를 돌려줍니다."""
        for row in range(self.count()):
            if self.item(row).isHidden():
                continue
            if y < self.visualItemRect(self.item(row)).center().y():
                return row
        return self.count()

    def move_row(self, source: int, target_gap: int) -> bool:
        """``source`` 행(과 함께 옮길 행들)을 ``target_gap`` 자리(그 행 앞)로 옮기고 각 행의 상태를 유지합니다."""
        count = self.count()
        if not self.isEnabled() or not 0 <= source < count:
            return False
        size = max(1, min(self.block_size(source), count - source))
        target_gap = self.snap_gap(source, max(0, min(target_gap, count)))
        if source <= target_gap <= source + size:
            return False
        destination = target_gap - size if target_gap > source else target_gap
        widgets = [self.itemWidget(self.item(row)) for row in range(source, source + size)]
        if not all(isinstance(widget, FilterItemWidget) for widget in widgets):
            return False
        # 행을 빼면 Qt가 행 위젯을 지우므로 먼저 같은 상태의 위젯을 만들어 둡니다.
        clones = [cast(FilterItemWidget, widget).clone() for widget in widgets]
        # 같은 항목 객체를 다시 넣으므로 패널의 삭제 콜백이 가리키는 항목도 그대로 유효합니다.
        items = [self.takeItem(source) for _ in range(size)]
        for offset, (item, widget) in enumerate(zip(items, clones)):
            self.insertItem(destination + offset, item)
            item.setSizeHint(widget.sizeHint())
            self.setItemWidget(item, widget)
        self.setCurrentRow(destination)
        self.items_reordered.emit()
        return True

    def _show_drop_indicator(self, gap: int):
        if gap < self.count():
            y = self.visualItemRect(self.item(gap)).top()
        elif self.count():
            y = self.visualItemRect(self.item(self.count() - 1)).bottom() + 1
        else:
            y = 0
        self._drop_indicator.setGeometry(0, max(0, y - 1), self.viewport().width(), 2)
        self._drop_indicator.show()
        self._drop_indicator.raise_()

    def _finish_drag(self):
        self._drag_row = -1
        self._dragging = False
        self._drop_indicator.hide()
        self.viewport().unsetCursor()

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            item = self.itemAt(event.position().toPoint())
            self._drag_row = self.row(item) if item is not None else -1
            self._drag_origin = event.position().toPoint()
            self._dragging = False

    def mouseMoveEvent(self, event):
        if self._drag_row < 0 or not event.buttons() & Qt.MouseButton.LeftButton:
            super().mouseMoveEvent(event)
            return
        position = event.position().toPoint()
        if not self._dragging:
            if (position - self._drag_origin).manhattanLength() < QApplication.startDragDistance():
                return
            self._dragging = True
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
        # 목록 위·아래 가장자리에서는 한 칸씩 스크롤해 보이지 않는 위치로도 옮길 수 있게 합니다.
        scroll = self.verticalScrollBar()
        if position.y() < self.AUTO_SCROLL_MARGIN:
            scroll.setValue(scroll.value() - scroll.singleStep())
        elif position.y() > self.viewport().height() - self.AUTO_SCROLL_MARGIN:
            scroll.setValue(scroll.value() + scroll.singleStep())
        self._show_drop_indicator(self.drop_gap_at(position.y()))

    def mouseReleaseEvent(self, event):
        if self._dragging and event.button() == Qt.MouseButton.LeftButton:
            source = self._drag_row
            gap = self.drop_gap_at(event.position().toPoint().y())
            self._finish_drag()
            self.move_row(source, gap)
            return
        self._finish_drag()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.modifiers() == Qt.KeyboardModifier.AltModifier and event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            row = self.currentRow()
            if row >= 0:
                # 아래로 한 칸은 다음 행(또는 다음 묶음) 뒤의 자리에 넣는 것과 같습니다.
                below = row + self.block_size(row) + 1
                self.move_row(row, row - 1 if event.key() == Qt.Key.Key_Up else below)
            event.accept()
            return
        super().keyPressEvent(event)


def normalize_special_search_mode(value: Any) -> str:
    """Keep saved mode labels independent of menu order and UI language."""
    if not isinstance(value, str):
        return AppStrings.SPECIAL_SEARCH_OFF
    value = value.strip()
    if value in AppStrings.SPECIAL_SEARCH_ITEMS:
        return cast(str, value)
    normalized = value.casefold()
    exact = "정확" in value or "exact" in normalized
    if "xml" in normalized:
        index = 2 if exact else 1
    elif "json" in normalized:
        index = 4 if exact else 3
    elif "excel" in normalized:
        index = 6 if exact else 5
    elif Constants.MODE_ARCHIVE in normalized:
        index = 8 if exact else 7
    else:
        index = 0
    return AppStrings.SPECIAL_SEARCH_ITEMS[index]


class SearchProfileMenu(QMenu):
    """글자 변형 검색 항목에만 사용 안내를 표시합니다."""

    def sizeHint(self):
        # Reserve a trailing icon column without using Qt's checkmark column.
        return super().sizeHint() + QSize(24, 0)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        try:
            for action in self.actions():
                if action.data() is True and action.isVisible():
                    row = self.actionGeometry(action)
                    icon_rect = QRect(self.width() - 24, row.center().y() - 8, 16, 16)
                    icon = self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning)
                    icon.paint(painter, icon_rect, Qt.AlignmentFlag.AlignCenter,
                               QIcon.Mode.Normal if action.isEnabled() else QIcon.Mode.Disabled)
        finally:
            painter.end()

    def event(self, event):
        if event.type() == QEvent.Type.ToolTip:
            action = self.actionAt(event.pos())
            if action is not None and action.data() is True:
                QToolTip.showText(event.globalPos(), AppStrings.COMPLEX_SEARCH_TOOLTIP, self)
            else:
                QToolTip.hideText()
            return True
        return super().event(event)


class SearchOptionsPanel(QWidget):
    search_started = Signal()
    stop_requested = Signal()
    special_mode_changed = Signal(str)
    settings_changed = Signal()

    def __init__(self, parent=None, config_manager=None):
        super().__init__(parent)
        self.config_manager = config_manager
        self._use_complex_search = False
        self._special_mode = AppStrings.SPECIAL_SEARCH_OFF
        self._exclude_hidden = True
        self._toolbar_mode = None
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        input_layout = QGridLayout()
        self.input_layout = input_layout
        self.search_settings_label = QLabel(AppStrings.SEARCH_SETTINGS_LABEL)
        self.search_settings_button = SearchSettingsButton()
        self.search_settings_label.setBuddy(self.search_settings_button)
        self.search_settings_button.setStyleSheet(
            f"QPushButton {{ text-align: left; padding-left: 8px; "
            f"padding-right: {SearchSettingsButton.RESERVED_RIGHT}px; }}"
        )
        self.search_settings_menu = QMenu(self.search_settings_button)
        self.profile_menu = SearchProfileMenu(AppStrings.SEARCH_PROFILE_LABEL, self.search_settings_menu)
        self.profile_menu.aboutToHide.connect(QToolTip.hideText)
        self.search_settings_menu.addMenu(self.profile_menu)
        self.profile_action_group = QActionGroup(self)
        self.profile_action_group.setExclusive(True)
        self.profile_actions = []
        for label_text, precise in (
            (AppStrings.SEARCH_PROFILE_NORMAL, False),
            (AppStrings.SEARCH_PROFILE_PRECISE, True),
        ):
            action = QAction(label_text, self)
            action.setCheckable(True)
            action.setData(precise)
            if precise:
                action.setToolTip(AppStrings.COMPLEX_SEARCH_TOOLTIP)
            self.profile_action_group.addAction(action)
            self.profile_menu.addAction(action)
            self.profile_actions.append(action)
        self.profile_action_group.triggered.connect(lambda action: self.set_complex_search(action.data()))
        self.value_menu = self.search_settings_menu.addMenu(AppStrings.SPECIAL_SEARCH_LABEL)
        self.value_action_group = QActionGroup(self)
        self.value_action_group.setExclusive(True)
        self.special_mode_actions = {}
        # Preserve stored mode labels while presenting JSON before XML.
        for index in (0, 3, 4, 1, 2, 5, 6, 7, 8):
            label_text = AppStrings.SPECIAL_SEARCH_ITEMS[index]
            # Presentation changes must not change saved modes or engine inputs.
            action = QAction(AppStrings.SPECIAL_SEARCH_MENU_ITEMS[index], self)
            action.setCheckable(True)
            action.setData(label_text)
            self.value_action_group.addAction(action)
            self.value_menu.addAction(action)
            self.special_mode_actions[label_text] = action
        self.value_action_group.triggered.connect(self._on_special_action_triggered)
        self.search_settings_button.clicked.connect(self._open_search_settings)
        self._refresh_search_settings()
        self.search_label = QLabel(AppStrings.SEARCH_LABEL)
        self.search_combo = SearchInputComboBox(
            history_provider=self.config_manager.get_history if self.config_manager is not None else None
        )
        self.search_combo.setEditable(True)
        self.search_combo.setMinimumWidth(100)
        self.search_combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.search_combo.setPlaceholderText(AppStrings.SEARCH_EDIT_PLACEHOLDER)
        le = self.search_combo.lineEdit()
        if le:
            le.returnPressed.connect(self.search_started.emit)
        input_layout.addWidget(self.search_settings_button, 0, 0)
        input_layout.addWidget(self.search_label, 0, 1)
        input_layout.addWidget(self.search_combo, 0, 2)
        layout.addLayout(input_layout)
        self.query_length_notice = QLabel()
        self.query_length_notice.setObjectName("queryLengthNotice")
        self.query_length_notice.setWordWrap(True)
        self.query_length_notice.setStyleSheet("color: #d99030;")
        self.query_length_notice.hide()
        layout.addWidget(self.query_length_notice)
        self.search_combo.currentTextChanged.connect(self._update_query_length_notice)
        self.search_btn = QPushButton(AppStrings.SEARCH_BTN)
        self.search_btn.setStyleSheet(UIStyles.STYLE_SEARCH_BTN_PRIMARY)
        self.search_btn.clicked.connect(self.search_started.emit)
        input_layout.addWidget(self.search_btn, 0, 3)
        self.stop_btn = QPushButton(AppStrings.SEARCH_BTN_STOP)
        self.stop_btn.setStyleSheet(UIStyles.STYLE_STOP_BTN_ACTIVE)  # 사용자가 쉽게 인지할 수 있도록 중지 버튼에 강조 색상을 적용합니다.
        self.stop_btn.clicked.connect(self.stop_requested.emit)
        self.stop_btn.setVisible(False)
        input_layout.addWidget(self.stop_btn, 0, 3)
        self._layout_search_controls()

    def _open_search_settings(self):
        self.search_settings_menu.popup(
            self.search_settings_button.mapToGlobal(QPoint(0, self.search_settings_button.height()))
        )

    def _layout_search_controls(self):
        if not hasattr(self, "stop_btn"):
            return
        controls = (self.search_combo, self.search_settings_button, self.search_btn, self.stop_btn)
        for control in controls:
            control.ensurePolished()
        height = max(32, *(control.sizeHint().height() for control in controls))
        for control in controls:
            control.setFixedHeight(height)
        metrics = self.search_settings_button.fontMetrics()
        # Reserve the longest selection and warning icon, independent of the current choice.
        selections = [
            profile if not value else AppStrings.SEARCH_SETTINGS_COMPACT_SELECTION.format(profile, value)
            for profile in (AppStrings.SEARCH_PROFILE_NORMAL_COMPACT, AppStrings.SEARCH_PROFILE_PRECISE_COMPACT)
            for value in AppStrings.SPECIAL_SEARCH_COMPACT_ITEMS
        ]
        self.search_settings_button.setFixedWidth(
            max(metrics.horizontalAdvance(text) for text in selections) + 48 + SearchSettingsButton.RESERVED_RIGHT
        )
        labels = (self.search_label, self.search_settings_label)
        # Clear narrow-layout constraints before measuring the natural widths.
        for label in labels:
            label.setMinimumWidth(0)
            label.setMaximumWidth(16777215)  # Qt's default maximum widget size.
        label_widths = [label.sizeHint().width() for label in labels]
        margins = cast(QVBoxLayout, self.layout()).contentsMargins()  # Installed in _init_ui.
        available = self.width() - margins.left() - margins.right()
        spacing = max(0, self.input_layout.horizontalSpacing())
        action_width = max(self.search_btn.sizeHint().width(), self.stop_btn.sizeHint().width())
        settings_width = self.search_settings_button.width()
        full_width = sum(label_widths) + 140 + settings_width + action_width + 4 * spacing
        mode = 1 if available >= full_width else 2
        if mode == 2:
            for label in labels:
                label.setFixedWidth(max(label_widths))
        if mode == self._toolbar_mode:
            return
        self._toolbar_mode = mode
        for widget in (self.search_settings_label, self.search_settings_button, self.search_label,
                       self.search_combo, self.search_btn, self.stop_btn):
            self.input_layout.removeWidget(widget)
        for column in range(5):
            self.input_layout.setColumnStretch(column, 0)
        self.input_layout.addWidget(self.search_settings_label, 0, 0)
        self.input_layout.addWidget(self.search_settings_button, 0, 1, 1, 1 if mode == 1 else 2,
                                    Qt.AlignmentFlag.AlignLeft)
        query_row = 0 if mode == 1 else 1
        self.input_layout.addWidget(self.search_label, query_row, 2 if mode == 1 else 0)
        self.input_layout.addWidget(self.search_combo, query_row, 3 if mode == 1 else 1)
        self.input_layout.addWidget(self.search_btn, query_row, 4 if mode == 1 else 2)
        self.input_layout.addWidget(self.stop_btn, query_row, 4 if mode == 1 else 2)
        self.input_layout.setColumnStretch(3 if mode == 1 else 1, 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_search_controls()

    def _refresh_search_settings(self):
        for action in self.profile_actions:
            action.setChecked(action.data() is self._use_complex_search)
        for label, action in self.special_mode_actions.items():
            action.setChecked(label == self._special_mode)
        profile = AppStrings.SEARCH_PROFILE_PRECISE if self._use_complex_search else AppStrings.SEARCH_PROFILE_NORMAL
        self.search_settings_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning)
            if self._use_complex_search else QIcon()
        )
        value = AppStrings.SPECIAL_SEARCH_MENU_ITEMS[AppStrings.SPECIAL_SEARCH_ITEMS.index(self._special_mode)]
        text = AppStrings.SEARCH_SETTINGS_SELECTION.format(profile, value)
        self.search_settings_button.setAccessibleName(text)
        compact_profile = (AppStrings.SEARCH_PROFILE_PRECISE_COMPACT if self._use_complex_search
                           else AppStrings.SEARCH_PROFILE_NORMAL_COMPACT)
        compact_value = AppStrings.SPECIAL_SEARCH_COMPACT_ITEMS[AppStrings.SPECIAL_SEARCH_ITEMS.index(self._special_mode)]
        self.search_settings_button.setText(
            AppStrings.SEARCH_SETTINGS_COMPACT_SELECTION.format(compact_profile, compact_value)
            if compact_value else compact_profile
        )
        self.search_settings_button.setToolTip(AppStrings.COMPLEX_SEARCH_TOOLTIP if self._use_complex_search else "")
        self._layout_search_controls()

    def set_complex_search(self, precise: bool):
        changed = self._use_complex_search != (precise is True)
        self._use_complex_search = precise is True
        self._refresh_search_settings()
        if changed:
            self.settings_changed.emit()

    def set_special_mode(self, mode: str):
        normalized = normalize_special_search_mode(mode)
        changed = self._special_mode != normalized
        self._special_mode = normalized
        self._refresh_search_settings()
        if changed:
            self.settings_changed.emit()

    def _on_special_action_triggered(self, action):
        self.set_special_mode(action.data())
        self.special_mode_changed.emit(self._special_mode)

    def _update_query_length_notice(self, text: str):
        # Do not use QLineEdit.maxLength: it silently truncates pasted input.
        try:
            validate_search_query(text)
        except ValueError as error:
            self.query_length_notice.setText(str(error))
            self.query_length_notice.show()
        else:
            self.query_length_notice.clear()
            self.query_length_notice.hide()

    def set_searching(self, searching: bool):
        self.search_btn.setVisible(not searching)
        self.stop_btn.setVisible(searching)
        self.search_combo.setEnabled(not searching)
        self.search_settings_button.setEnabled(not searching)
        self.profile_action_group.setEnabled(not searching)
        self.value_action_group.setEnabled(not searching)
        if searching:
            self.search_settings_menu.close()
            self.profile_menu.close()
            self.value_menu.close()
        self.search_btn.setEnabled(True)
        self.search_btn.setText(AppStrings.SEARCH_BTN)
        self.search_btn.setStyleSheet(UIStyles.STYLE_SEARCH_BTN_PRIMARY)
        self.stop_btn.setEnabled(True)
        self.stop_btn.setText(AppStrings.SEARCH_BTN_STOP)
        self.stop_btn.setStyleSheet(UIStyles.STYLE_STOP_BTN_ACTIVE)
        self._layout_search_controls()

    def set_stopping_state(self):
        """검색 중단 중임을 나타내도록 버튼 상태와 텍스트를 변경합니다."""
        self.stop_btn.setEnabled(False)
        self.stop_btn.setText(AppStrings.SEARCH_BTN_STOPPING)
        self.stop_btn.setStyleSheet(UIStyles.STYLE_STOP_BTN_WAIT)  # 중단 절차 진행 중에는 버튼 색상을 변경하여 상태를 시각화합니다.
        self.search_btn.setEnabled(False)
        self.search_btn.setText(AppStrings.SEARCH_BTN_STOPPING)
        self.search_btn.setStyleSheet(UIStyles.STYLE_STOP_BTN_WAIT)
        self._layout_search_controls()

    def get_search_text(self) -> str:
        return self.search_combo.currentText().strip()

    def is_complex_search(self) -> bool:
        return self._use_complex_search

    def is_exclude_hidden(self) -> bool:
        if self.config_manager is not None:
            return bool(self.config_manager.get(Constants.CONFIG_KEY_EXCLUDE_HIDDEN, True))
        return self._exclude_hidden

    def get_state(self) -> dict:
        return {
            Constants.STATE_KEY_SEARCH: self.search_combo.currentText(),
            Constants.PAYLOAD_USE_COMPLEX_SEARCH: self.is_complex_search(),
            Constants.PAYLOAD_SPECIAL_MODE: self._special_mode,
            Constants.PAYLOAD_EXCLUDE_HIDDEN: self.is_exclude_hidden(),
        }

    def load_state(self, state: dict):
        state = state if isinstance(state, dict) else {}
        search_text = state.get(Constants.STATE_KEY_SEARCH, "")
        self.search_combo.set_current_text(search_text if isinstance(search_text, str) else "")
        # Invalid JSON types must not enable a different search policy through truthiness.
        self.set_complex_search(state.get(Constants.PAYLOAD_USE_COMPLEX_SEARCH) is True)
        if Constants.PAYLOAD_SPECIAL_MODE in state:
            self.set_special_mode(state[Constants.PAYLOAD_SPECIAL_MODE])
        exclude_hidden = state.get(Constants.PAYLOAD_EXCLUDE_HIDDEN, True)
        self._exclude_hidden = exclude_hidden if isinstance(exclude_hidden, bool) else True


class DenseFilterPanel(QWidget):
    """공통 목록 밀도와 이후 추가되는 항목의 표시 상태를 관리합니다."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._compact_rows = True

    def apply_display_density(self, compact):
        self._compact_rows = compact
        margins = (6, 6, 6, 6) if compact else (10, 15, 10, 10)
        layout = self.layout()
        if layout is not None:
            layout.setContentsMargins(*margins)
        for listing in self.findChildren(QListWidget):
            for index in range(listing.count()):
                item = listing.item(index)
                widget = listing.itemWidget(item)
                if isinstance(widget, FilterItemWidget):
                    widget.set_compact(compact)
                    item.setSizeHint(widget.sizeHint())


class FolderFilterPanel(DenseFilterPanel):
    """검색 폴더 목록입니다. 폴더를 그룹으로 묶어 한꺼번에 선택·해제하고 접을 수 있습니다.

    목록은 한 줄로 이어진 행이며, 그룹 머리글 행 아래부터 다음 머리글 전까지가 그 그룹의 폴더입니다.
    첫 머리글 위의 폴더는 그룹에 속하지 않습니다. 검색은 체크된 폴더를 목록 순서(그룹 순서 → 그룹 안 순서)대로 합니다.
    """

    filter_changed = Signal()
    # 그룹 안 폴더의 들여쓰기 폭입니다. 머리글의 펼치기 단추 너비와 비슷하게 맞춥니다.
    GROUP_INDENT = 18

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 15, 10, 10)
        self.folder_list = FilterListWidget()
        self.folder_list.block_size = self._block_size
        self.folder_list.snap_gap = self._snap_gap
        self.folder_list.items_reordered.connect(self._after_reorder)
        self.folder_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.folder_list.customContextMenuRequested.connect(self._show_context_menu)
        self.usage_label = self.folder_list.empty_hint
        self.usage_label.setText(AppStrings.FOLDER_FILTER_USAGE)
        # 폴더 목록의 가독성을 위해 적절한 최소 크기를 보장합니다.
        self.folder_list.setMinimumHeight(50)
        main_layout.addWidget(self.usage_label)
        main_layout.addWidget(self.folder_list, 1)  # 1: Stretch 부여

        btn_layout = QHBoxLayout()
        add_btn = QPushButton(AppStrings.ADD_FOLDER_BTN)
        add_btn.clicked.connect(self._on_add_clicked)
        self.add_group_btn = QPushButton(AppStrings.ADD_FOLDER_GROUP_BTN)
        self.add_group_btn.clicked.connect(self._on_add_group_clicked)
        sel_all_btn = QPushButton(AppStrings.SELECT_ALL_BTN)
        sel_all_btn.setFixedWidth(80)
        sel_all_btn.clicked.connect(lambda: self.toggle_all(True))
        desel_all_btn = QPushButton(AppStrings.DESELECT_ALL_BTN)
        desel_all_btn.setFixedWidth(80)
        desel_all_btn.clicked.connect(lambda: self.toggle_all(False))
        btn_layout.addWidget(add_btn)
        btn_layout.addWidget(self.add_group_btn)
        btn_layout.addWidget(sel_all_btn)
        btn_layout.addWidget(desel_all_btn)
        main_layout.addLayout(btn_layout)
        self.status_label = QLabel(self)
        self.status_label.setWordWrap(True)
        self.status_label.hide()
        self.filter_changed.connect(self._refresh_status)
        self._refresh_status()

    # ----- 행 구조 -----
    def _widget(self, row: int):
        return self.folder_list.itemWidget(self.folder_list.item(row))

    def _is_header_row(self, row: int) -> bool:
        return isinstance(self._widget(row), FolderGroupHeader)

    def _header_rows(self) -> List[int]:
        return [row for row in range(self.folder_list.count()) if self._is_header_row(row)]

    def _folder_rows(self) -> List[int]:
        return [
            row for row in range(self.folder_list.count())
            if isinstance(self._widget(row), FilterItemWidget) and not self._is_header_row(row)
        ]

    def _group_end(self, header_row: int) -> int:
        """그룹 머리글부터 그룹 마지막 폴더 다음 행까지의 끝(제외)입니다."""
        row = header_row + 1
        while row < self.folder_list.count() and not self._is_header_row(row):
            row += 1
        return row

    def _ungrouped_end(self) -> int:
        headers = self._header_rows()
        return headers[0] if headers else self.folder_list.count()

    def _group_header_of(self, row: int) -> int:
        """행이 속한 그룹 머리글의 행 번호이며, 그룹이 없으면 -1입니다."""
        while row >= 0:
            if self._is_header_row(row):
                return row
            row -= 1
        return -1

    def _row_of_widget(self, widget) -> int:
        for row in range(self.folder_list.count()):
            if self._widget(row) is widget:
                return row
        return -1

    def _block_size(self, row: int) -> int:
        # 그룹 머리글을 옮기면 그 그룹의 폴더도 함께 옮깁니다.
        return self._group_end(row) - row if self._is_header_row(row) else 1

    def _snap_gap(self, row: int, gap: int) -> int:
        """그룹 머리글은 다른 그룹 사이(또는 목록 끝)에만 놓여 다른 그룹을 쪼개지 않게 합니다."""
        if not self._is_header_row(row):
            return gap
        boundaries = self._header_rows() + [self.folder_list.count()]
        if gap > row:
            after = [boundary for boundary in boundaries if boundary >= gap]
            return min(after) if after else self.folder_list.count()
        before = [boundary for boundary in boundaries if boundary <= gap]
        return max(before) if before else row

    # ----- 행 만들기 -----
    def _insert_folder_row(self, row: int, folder: str, checked: bool):
        item = QListWidgetItem()
        self.folder_list.insertItem(row, item)
        widget = FilterItemWidget(
            folder, checked, on_delete=lambda: self._delete_item(item), on_change=lambda _: self._on_folder_toggled()
        )
        widget.set_compact(self._compact_rows)
        item.setSizeHint(widget.sizeHint())
        self.folder_list.setItemWidget(item, widget)

    def _insert_header_row(self, row: int, name: str, collapsed: bool = False):
        item = QListWidgetItem()
        self.folder_list.insertItem(row, item)
        widget = FolderGroupHeader(
            name, collapsed, on_delete=lambda: self._delete_item(item),
            on_toggle=self._on_group_toggled, on_check=self._on_group_checked,
        )
        widget.set_compact(self._compact_rows)
        item.setSizeHint(widget.sizeHint())
        self.folder_list.setItemWidget(item, widget)

    def _refresh_groups(self):
        """그룹 머리글의 체크(전체·일부·없음)·개수와 접힌 그룹의 폴더 숨김을 다시 맞춥니다."""
        for header_row in self._header_rows():
            header = cast(FolderGroupHeader, self._widget(header_row))
            children = range(header_row + 1, self._group_end(header_row))
            checked = sum(1 for row in children if cast(FilterItemWidget, self._widget(row)).isChecked())
            total = len(children)
            if total and checked == total:
                state = Qt.CheckState.Checked
            elif checked:
                state = Qt.CheckState.PartiallyChecked
            else:
                state = Qt.CheckState.Unchecked
            with QSignalBlocker(header.checkbox):
                header.checkbox.setCheckState(state)
            header.set_counts(checked, total)
            for row in children:
                self.folder_list.item(row).setHidden(header.collapsed)
                cast(FilterItemWidget, self._widget(row)).set_indent(self.GROUP_INDENT)
        for row in range(self._ungrouped_end()):
            self.folder_list.item(row).setHidden(False)
            cast(FilterItemWidget, self._widget(row)).set_indent(0)
        self.folder_list.doItemsLayout()

    def _refresh_status(self):
        count = len(self.get_selected_folders())
        self.status_label.setText(
            AppStrings.FOLDER_FILTER_SELECTED.format(count) if count else AppStrings.FOLDER_FILTER_EMPTY
        )

    # ----- 사용자 동작 -----
    def _on_add_clicked(self):
        folder = QFileDialog.getExistingDirectory(self, AppStrings.SELECT_FOLDER_TITLE)
        if folder:
            self.add_folder(folder)

    def _on_add_group_clicked(self):
        default = AppStrings.FOLDER_GROUP_DEFAULT_NAME.format(len(self._header_rows()) + 1)
        name, accepted = QInputDialog.getText(
            self, AppStrings.FOLDER_GROUP_NAME_TITLE, AppStrings.FOLDER_GROUP_NAME_PROMPT, text=default
        )
        if accepted:
            self.add_group(name)

    def _on_folder_toggled(self):
        self._refresh_groups()
        self.filter_changed.emit()

    def _on_group_checked(self, header):
        """그룹 체크를 누르면 그룹 안 폴더를 모두 선택하고, 이미 모두 선택돼 있으면 모두 해제합니다."""
        header_row = self._row_of_widget(header)
        if header_row < 0:
            return
        children = [cast(FilterItemWidget, self._widget(row)) for row in range(header_row + 1, self._group_end(header_row))]
        target = not (children and all(child.isChecked() for child in children))
        for child in children:
            with QSignalBlocker(child.checkbox):
                child.checkbox.setChecked(target)
        self._refresh_groups()
        self.filter_changed.emit()

    def _on_group_toggled(self, header):
        header.set_collapsed(not header.collapsed)
        self._refresh_groups()
        self.filter_changed.emit()

    def _after_reorder(self):
        self._refresh_groups()
        self.filter_changed.emit()

    def _show_context_menu(self, position: QPoint):
        if not self.folder_list.isEnabled():
            return
        item = self.folder_list.itemAt(position)
        row = self.folder_list.row(item) if item is not None else -1
        menu = QMenu(self)
        if row >= 0 and self._is_header_row(row):
            menu.addAction(AppStrings.FOLDER_GROUP_RENAME, lambda: self._prompt_rename(row))
            menu.addAction(AppStrings.FOLDER_GROUP_DELETE, lambda: self._delete_item(self.folder_list.item(row)))
        elif row >= 0:
            move_menu = menu.addMenu(AppStrings.FOLDER_GROUP_MOVE_TO)
            current = self._group_header_of(row)
            targets = [(-1, AppStrings.FOLDER_GROUP_NONE)] + [
                (header_row, cast(FolderGroupHeader, self._widget(header_row)).text())
                for header_row in self._header_rows()
            ]
            for header_row, name in targets:
                action = move_menu.addAction(name, lambda target=header_row: self.move_folder_to_group(row, target))
                action.setEnabled(header_row != current)
        menu.addAction(AppStrings.ADD_FOLDER_GROUP_BTN, self._on_add_group_clicked)
        menu.exec(self.folder_list.viewport().mapToGlobal(position))

    def _prompt_rename(self, header_row: int):
        header = cast(FolderGroupHeader, self._widget(header_row))
        name, accepted = QInputDialog.getText(
            self, AppStrings.FOLDER_GROUP_NAME_TITLE, AppStrings.FOLDER_GROUP_NAME_PROMPT, text=header.text()
        )
        if accepted:
            self.rename_group(header_row, name)

    # ----- 공개 동작 -----
    def add_folder(self, folder: str, checked: bool = True):
        """폴더를 추가합니다. 그룹 안의 행을 선택한 상태면 그 그룹 끝에, 아니면 그룹 없는 영역 끝에 넣습니다."""
        if not isinstance(folder, str) or not folder.strip() or not isinstance(checked, bool):
            return
        if any(cast(FilterItemWidget, self._widget(row)).text() == folder for row in self._folder_rows()):
            return
        header_row = self._group_header_of(self.folder_list.currentRow()) if self.folder_list.currentRow() >= 0 else -1
        if header_row >= 0:
            header = cast(FolderGroupHeader, self._widget(header_row))
            header.set_collapsed(False)
            row = self._group_end(header_row)
        else:
            row = self._ungrouped_end()
        self._insert_folder_row(row, folder, checked)
        self._refresh_groups()
        self.filter_changed.emit()

    def add_group(self, name: str, collapsed: bool = False) -> bool:
        """빈 그룹을 목록 끝에 추가합니다."""
        if not isinstance(name, str) or not name.strip():
            return False
        self._insert_header_row(self.folder_list.count(), name.strip(), collapsed)
        self._refresh_groups()
        self.filter_changed.emit()
        return True

    def rename_group(self, header_row: int, name: str) -> bool:
        if not self._is_header_row(header_row) or not isinstance(name, str) or not name.strip():
            return False
        header = cast(FolderGroupHeader, self._widget(header_row))
        header.checkbox.setText(name.strip())
        self.filter_changed.emit()
        return True

    def move_folder_to_group(self, row: int, header_row: int) -> bool:
        """폴더를 다른 그룹 끝으로(``header_row``가 -1이면 그룹 없는 영역 끝으로) 옮깁니다."""
        if row < 0 or self._is_header_row(row) or row >= self.folder_list.count():
            return False
        gap = self._group_end(header_row) if header_row >= 0 else self._ungrouped_end()
        if header_row >= 0:
            cast(FolderGroupHeader, self._widget(header_row)).set_collapsed(False)
        moved = self.folder_list.move_row(row, gap)
        if not moved:
            self._refresh_groups()
        return moved

    def _delete_item(self, item):
        row = self.folder_list.row(item)
        if row < 0:
            return
        if self._is_header_row(row):
            # 그룹을 지워도 폴더는 지우지 않고 그룹 없는 영역 끝으로 옮깁니다.
            children = [
                (cast(FilterItemWidget, self._widget(child)).text(), cast(FilterItemWidget, self._widget(child)).isChecked())
                for child in range(row + 1, self._group_end(row))
            ]
            for _ in range(len(children)):
                self.folder_list.takeItem(row + 1)
            self.folder_list.takeItem(row)
            insert_at = self._ungrouped_end()
            for offset, (folder, checked) in enumerate(children):
                self._insert_folder_row(insert_at + offset, folder, checked)
        else:
            self.folder_list.takeItem(row)
        self._refresh_groups()
        self.filter_changed.emit()

    def toggle_all(self, checked: bool):
        for row in self._folder_rows():
            widget = cast(FilterItemWidget, self._widget(row))
            with QSignalBlocker(widget.checkbox):
                widget.checkbox.setChecked(checked)
        self._refresh_groups()
        self.filter_changed.emit()

    def get_selected_folders(self) -> List[str]:
        # 접힌 그룹의 폴더도 체크돼 있으면 검색합니다. 접기는 표시만 바꿉니다.
        return [
            cast(FilterItemWidget, self._widget(row)).text()
            for row in self._folder_rows()
            if cast(FilterItemWidget, self._widget(row)).isChecked()
        ]

    def get_state(self) -> dict:
        """폴더 경로 → 체크 여부를 목록 순서대로 돌려줍니다. 그룹 구조는 :meth:`get_group_state`에 따로 담습니다."""
        return {
            cast(FilterItemWidget, self._widget(row)).text(): cast(FilterItemWidget, self._widget(row)).isChecked()
            for row in self._folder_rows()
        }

    def get_group_state(self) -> List[Dict[str, Any]]:
        groups: List[Dict[str, Any]] = []
        for header_row in self._header_rows():
            header = cast(FolderGroupHeader, self._widget(header_row))
            groups.append({
                "name": header.text(),
                "collapsed": header.collapsed,
                "folders": [
                    cast(FilterItemWidget, self._widget(row)).text()
                    for row in range(header_row + 1, self._group_end(header_row))
                ],
            })
        return groups

    def restore_state(self, data: Union[List[str], Dict[str, bool]], groups: Any = None):
        """폴더 상태와 그룹 구조를 복원합니다. 그룹 정보가 없거나 잘못되면 모두 그룹 없는 폴더로 둡니다."""
        self.folder_list.clear()
        folders: Dict[str, bool] = {}
        if isinstance(data, list):
            folders = {folder: True for folder in data if isinstance(folder, str) and folder.strip()}
        elif isinstance(data, dict):
            folders = {
                folder: checked for folder, checked in data.items()
                if isinstance(folder, str) and folder.strip() and isinstance(checked, bool)
            }
        valid_groups = []
        placed: set = set()
        for group in groups if isinstance(groups, list) else []:
            if not isinstance(group, dict) or not isinstance(group.get("name"), str) or not group["name"].strip():
                continue
            members = []
            listed = group.get("folders")
            for folder in listed if isinstance(listed, list) else []:
                if isinstance(folder, str) and folder in folders and folder not in placed:
                    members.append(folder)
                    placed.add(folder)
            valid_groups.append((group["name"].strip(), group.get("collapsed") is True, members))
        for folder, checked in folders.items():
            if folder not in placed:
                self._insert_folder_row(self.folder_list.count(), folder, checked)
        for name, collapsed, members in valid_groups:
            self._insert_header_row(self.folder_list.count(), name, collapsed)
            for folder in members:
                self._insert_folder_row(self.folder_list.count(), folder, folders[folder])
        self._refresh_groups()
        self._refresh_status()

    def load_state(self, state: dict, groups: Any = None):
        self.restore_state(state, groups)


class ExtensionFilterPanel(DenseFilterPanel):
    filter_changed = Signal()
    special_mode_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._special_mode = AppStrings.SPECIAL_SEARCH_OFF
        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 15, 10, 10)
        self.ext_list = FilterListWidget()
        self.ext_list.items_reordered.connect(self.filter_changed.emit)
        self.usage_label = self.ext_list.empty_hint
        self.usage_label.setText(AppStrings.EXTENSION_FILTER_USAGE)
        self.ext_list.setMinimumHeight(50)
        # 값만 검색을 쓰는 동안 확장자 목록이 왜 잠겼는지 알려 줍니다.
        self.values_only_notice = QLabel(AppStrings.EXTENSION_FILTER_VALUES_ONLY_NOTICE)
        self.values_only_notice.setWordWrap(True)
        self.values_only_notice.setContentsMargins(*UIStyles.GUIDE_BOX_PADDING)
        self.values_only_notice.setStyleSheet(UIStyles.STYLE_NOTICE_BOX)
        self.values_only_notice.hide()
        main_layout.addWidget(self.values_only_notice)
        main_layout.addWidget(self.usage_label)
        main_layout.addWidget(self.ext_list, 1)  # 확장자 목록이 가용한 영역을 최대한 활용하도록 설정합니다.

        input_layout = QHBoxLayout()
        self.ext_edit = QLineEdit()
        self.ext_edit.setPlaceholderText(AppStrings.EXT_EDIT_PLACEHOLDER)
        self.ext_edit.setValidator(
            QRegularExpressionValidator(QRegularExpression(r"[^*?\[\]\\]*"), self.ext_edit)
        )
        self.ext_edit.inputRejected.connect(self._show_rejected_extension_input)
        self.ext_edit.returnPressed.connect(self._on_add_clicked)
        self.add_btn = QPushButton(AppStrings.ADD_EXT_BTN)
        self.add_btn.setFixedWidth(50)
        self.add_btn.clicked.connect(self._on_add_clicked)
        input_layout.addWidget(self.ext_edit)
        input_layout.addWidget(self.add_btn)
        main_layout.addLayout(input_layout)

        self.extension_input_notice = QLabel(AppStrings.EXTENSION_FILTER_WILDCARD_NOT_ALLOWED)
        self.extension_input_notice.setWordWrap(True)
        self.extension_input_notice.hide()
        main_layout.addWidget(self.extension_input_notice)

        toggle_layout = QHBoxLayout()
        self.sel_all_btn = QPushButton(AppStrings.SELECT_ALL_BTN)
        self.sel_all_btn.clicked.connect(lambda: self.toggle_all(True))
        self.desel_all_btn = QPushButton(AppStrings.DESELECT_ALL_BTN)
        self.desel_all_btn.clicked.connect(lambda: self.toggle_all(False))
        toggle_layout.addWidget(self.sel_all_btn)
        toggle_layout.addWidget(self.desel_all_btn)
        main_layout.addLayout(toggle_layout)
        self.status_label = QLabel(self)
        self.status_label.setWordWrap(True)
        self.status_label.hide()
        self.filter_changed.connect(self._refresh_status)
        self._refresh_status()

    def _refresh_status(self):
        selected = self.get_selected_extensions()
        self.status_label.setText(
            AppStrings.EXTENSION_FILTER_SELECTED.format(len(selected), ", ".join(ext.lstrip(".") for ext in selected))
            if selected else AppStrings.EXTENSION_FILTER_ALL
        )

    def _on_special_changed(self, text):
        is_off = text == AppStrings.SPECIAL_SEARCH_OFF
        self.ext_list.setEnabled(is_off)
        self.ext_edit.setEnabled(is_off)
        self.add_btn.setEnabled(is_off)
        self.sel_all_btn.setEnabled(is_off)
        self.desel_all_btn.setEnabled(is_off)
        self.usage_label.setEnabled(is_off)
        # 값만 검색 안내가 보일 때는 같은 자리의 빈 목록 안내를 겹쳐 띄우지 않습니다.
        self.ext_list.empty_hint_suppressed = not is_off
        self.ext_list.refresh_empty_hint()
        self.values_only_notice.setVisible(not is_off)
        self._refresh_status()
        self.special_mode_changed.emit(text)
        self.filter_changed.emit()

    def set_special_mode(self, value):
        mode = normalize_special_search_mode(value)
        if mode != self._special_mode:
            self._special_mode = mode
            self._on_special_changed(mode)

    def _show_rejected_extension_input(self):
        self.extension_input_notice.setText(AppStrings.EXTENSION_FILTER_WILDCARD_NOT_ALLOWED)
        self.extension_input_notice.show()

    def _on_add_clicked(self):
        ext = self.ext_edit.text().strip()
        if ext:
            if not self.add_extension(ext):
                return
            self.ext_edit.clear()
            self.extension_input_notice.hide()

    def add_extension(self, ext: str, checked: bool = True) -> bool:
        if not isinstance(ext, str) or not ext.strip() or not isinstance(checked, bool):
            return False
        if any(char in ext for char in "*?[]\\"):
            self._show_rejected_extension_input()
            return False
        extensions = list(dict.fromkeys(part.strip().lower().lstrip(".") for part in ext.split(",")))
        if not all(extensions):
            self.extension_input_notice.setText(AppStrings.EXTENSION_FILTER_INVALID_GROUP)
            self.extension_input_notice.show()
            return False
        ext = ",".join(extensions)
        for i in range(self.ext_list.count()):
            widget = self.ext_list.itemWidget(self.ext_list.item(i))
            if isinstance(widget, FilterItemWidget) and widget.text() == ext:
                return True
        item = QListWidgetItem(self.ext_list)
        widget = FilterItemWidget(
            ext, checked, on_delete=lambda: self._delete_item(item), on_change=lambda _: self.filter_changed.emit()
        )
        widget.set_compact(self._compact_rows)
        item.setSizeHint(widget.sizeHint())
        self.ext_list.addItem(item)
        self.ext_list.setItemWidget(item, widget)
        self.filter_changed.emit()
        return True

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
        special_mode = self._special_mode
        if special_mode == AppStrings.SPECIAL_SEARCH_OFF:
            return self.get_checked_extensions()
        else:
            # 선택된 특수 검색 모드에 따라 처리 대상을 결정합니다.
            if Constants.MODE_XML in special_mode:
                return [Constants.EXT_XML]
            elif Constants.MODE_JSON in special_mode:
                return [Constants.EXT_JSON]
            elif Constants.MODE_EXCEL in special_mode:
                return list(Constants.EXT_EXCEL)
            elif Constants.MODE_ARCHIVE in special_mode:
                return [Constants.EXT_ARCHIVE]
            else:
                return [special_mode.lower()]
    def get_checked_extensions(self) -> List[str]:
        extensions: List[str] = []
        for index in range(self.ext_list.count()):
            widget = self.ext_list.itemWidget(self.ext_list.item(index))
            if isinstance(widget, FilterItemWidget) and widget.isChecked():
                extensions.extend(part.strip().lstrip(".").lower() for part in widget.text().split(",") if part.strip())
        return list(dict.fromkeys(extensions))

    def get_special_mode(self) -> str:
        return self._special_mode

    def restore_state(self, data: Union[List[str], Dict[str, Any]]):
        self.ext_list.clear()
        self.extension_input_notice.hide()
        if isinstance(data, dict):
            if Constants.PAYLOAD_SPECIAL_MODE in data:
                self.set_special_mode(data[Constants.PAYLOAD_SPECIAL_MODE])
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
        self._refresh_status()

    def get_state(self) -> dict:
        ext_states = {}
        for i in range(self.ext_list.count()):
            item = self.ext_list.item(i)
            widget = self.ext_list.itemWidget(item)
            if isinstance(widget, FilterItemWidget):
                ext_states[widget.text()] = widget.checkbox.isChecked()
        return {
            Constants.PAYLOAD_SPECIAL_MODE: self._special_mode,
            Constants.CONFIG_KEY_EXTENSIONS: ext_states,
        }

    def load_state(self, state: dict):
        if Constants.CONFIG_KEY_EXTENSIONS in state:
            self.set_special_mode(state.get(Constants.PAYLOAD_SPECIAL_MODE, AppStrings.SPECIAL_SEARCH_OFF))
            self.restore_state(state)
        elif Constants.PAYLOAD_SPECIAL_MODE in state:
            self.set_special_mode(state[Constants.PAYLOAD_SPECIAL_MODE])


class FilenameFilterPanel(DenseFilterPanel):
    filter_changed = Signal()
    search_triggered = Signal()  # 입력창에서 Enter로 검색 트리거

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 15, 10, 10)
        self.filename_list = FilterListWidget()
        self.filename_list.items_reordered.connect(self.filter_changed.emit)
        self.usage_label = self.filename_list.empty_hint
        self.usage_label.setText(AppStrings.FILENAME_FILTER_USAGE)
        self.filename_list.setMinimumHeight(50)
        main_layout.addWidget(self.usage_label)
        main_layout.addWidget(self.filename_list, 1)

        add_layout = QHBoxLayout()
        self.add_edit = QLineEdit()
        self.add_edit.setPlaceholderText(AppStrings.FILENAME_LIST_PLACEHOLDER)
        self.add_edit.setValidator(
            QRegularExpressionValidator(
                QRegularExpression(r"[^*?\[\]\\]*"), self.add_edit
            )
        )
        self.add_edit.inputRejected.connect(self._show_rejected_filename_input)
        self.add_edit.returnPressed.connect(self._on_add_clicked)
        add_btn = QPushButton(AppStrings.ADD_EXT_BTN)
        add_btn.setFixedWidth(50)
        add_btn.clicked.connect(self._on_add_clicked)
        add_layout.addWidget(self.add_edit)
        add_layout.addWidget(add_btn)
        main_layout.addLayout(add_layout)

        self.filename_input_notice = QLabel(AppStrings.FILENAME_FILTER_WILDCARD_NOT_ALLOWED)
        self.filename_input_notice.setWordWrap(True)
        self.filename_input_notice.hide()
        main_layout.addWidget(self.filename_input_notice)

        toggle_layout = QHBoxLayout()
        sel_all = QPushButton(AppStrings.SELECT_ALL_BTN)
        sel_all.clicked.connect(lambda: self.toggle_all(True))
        desel_all = QPushButton(AppStrings.DESELECT_ALL_BTN)
        desel_all.clicked.connect(lambda: self.toggle_all(False))
        toggle_layout.addWidget(sel_all)
        toggle_layout.addWidget(desel_all)
        main_layout.addLayout(toggle_layout)
        self.status_label = QLabel(self)
        self.status_label.setWordWrap(True)
        self.status_label.hide()
        self.filter_changed.connect(self._refresh_status)
        self._refresh_status()

    def _refresh_status(self):
        selected = self.get_selected_filenames()
        self.status_label.setText(
            AppStrings.FILENAME_FILTER_SELECTED.format(len(selected), ", ".join(selected))
            if selected else AppStrings.FILENAME_FILTER_ALL
        )

    def _show_rejected_filename_input(self):
        # Inline feedback avoids modal warnings for each rejected keystroke.
        self.filename_input_notice.setText(AppStrings.FILENAME_FILTER_WILDCARD_NOT_ALLOWED)
        self.filename_input_notice.show()

    def _on_add_clicked(self):
        fn = self.add_edit.text().strip()
        if fn:
            if not self.add_filename(fn):
                QMessageBox.warning(self, AppStrings.ERROR_TITLE, self.filename_input_notice.text())
                return
            self.add_edit.clear()
            self.filename_input_notice.hide()

    def add_filename(self, fn: str, checked: bool = True) -> bool:
        if not isinstance(fn, str) or not fn.strip() or not isinstance(checked, bool):
            return False
        if any(char in fn for char in "*?[]\\"):
            self._show_rejected_filename_input()
            return False
        terms = list(dict.fromkeys(part.strip() for part in fn.split(",")))
        if not all(terms):
            self.filename_input_notice.setText(AppStrings.FILENAME_FILTER_INVALID_GROUP)
            self.filename_input_notice.show()
            return False
        fn = ",".join(terms)
        for i in range(self.filename_list.count()):
            widget = self.filename_list.itemWidget(self.filename_list.item(i))
            if isinstance(widget, FilterItemWidget) and widget.text() == fn:
                return True
        item = QListWidgetItem(self.filename_list)
        widget = FilterItemWidget(
            fn, checked, on_delete=lambda: self._delete_item(item), on_change=lambda _: self.filter_changed.emit()
        )
        widget.set_compact(self._compact_rows)
        item.setSizeHint(widget.sizeHint())
        self.filename_list.addItem(item)
        self.filename_list.setItemWidget(item, widget)
        self.filter_changed.emit()
        return True

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


    def get_selected_filenames(self) -> List[str]:
        filenames: List[str] = []
        for i in range(self.filename_list.count()):
            widget = self.filename_list.itemWidget(self.filename_list.item(i))
            if isinstance(widget, FilterItemWidget) and widget.isChecked():
                filenames.extend(part.strip() for part in widget.text().split(",") if part.strip())
        # Search is case-insensitive; overlapping checked groups form a union.
        unique: Dict[str, str] = {}
        for value in filenames:
            unique.setdefault(value.casefold(), value)
        return list(unique.values())

    def restore_state(self, data: Union[List[str], Dict[str, Any]]):
        self.filename_list.clear()
        target_data = data
        if isinstance(data, dict) and Constants.CONFIG_KEY_FILENAMES in data:
            target_data = data.get(Constants.CONFIG_KEY_FILENAMES, {})
            legacy_text = data.get(Constants.PAYLOAD_FILENAME_FILTER, "")
            if not isinstance(legacy_text, str):
                legacy_text = ""
            if legacy_text.strip():
                for fn in (part.strip() for part in legacy_text.split(",")):
                    if fn and not (isinstance(target_data, dict) and fn in target_data):
                        self.add_filename(fn, checked=True)
        if isinstance(target_data, list):
            for fn in target_data:
                self.add_filename(fn, checked=True)
        elif isinstance(target_data, dict):
            for fn, checked in target_data.items():
                self.add_filename(fn, checked=checked)
        self._refresh_status()

    def get_state(self) -> dict:
        filename_states = {}
        for i in range(self.filename_list.count()):
            item = self.filename_list.item(i)
            widget = self.filename_list.itemWidget(item)
            if isinstance(widget, FilterItemWidget):
                filename_states[widget.text()] = widget.checkbox.isChecked()
        return {
            Constants.PAYLOAD_FILENAME_FILTER: "",
            Constants.CONFIG_KEY_FILENAMES: filename_states,
        }

    def load_state(self, state: dict):
        if (Constants.CONFIG_KEY_FILENAMES in state
                or Constants.PAYLOAD_FILENAME_FILTER in state):
            self.restore_state({
                Constants.CONFIG_KEY_FILENAMES: state.get(Constants.CONFIG_KEY_FILENAMES, {}),
                Constants.PAYLOAD_FILENAME_FILTER: state.get(Constants.PAYLOAD_FILENAME_FILTER, ""),
            })
