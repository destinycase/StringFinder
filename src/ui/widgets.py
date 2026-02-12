from PySide6.QtWidgets import (
    QComboBox,
    QStyledItemDelegate,
    QStyle,
    QCompleter,
    QSizePolicy,
    QLineEdit,
    QStyleOptionViewItem,
)
from PySide6.QtCore import Qt, QRect, QEvent, Signal, QSize, QRectF
from PySide6.QtGui import QColor, QCursor, QPalette, QTextDocument, QAbstractTextDocumentLayout
from utils.app_strings import AppStrings


class HtmlDelegate(QStyledItemDelegate):
    """
    HTML 텍스트 렌더링을 지원하는 델리게이트입니다.
    QTableView 등의 셀에서 태그를 활용한 부분 강조(색상 등)가 가능하게 합니다.
    """

    def paint(self, painter, option, index):
        options = QStyleOptionViewItem(option)
        self.initStyleOption(options, index)

        painter.save()

        # HTML 문서를 생성하여 텍스트를 설정합니다.
        doc = QTextDocument()
        # 기본 폰트와 색상을 현재 테마와 맞춥니다.
        doc.setDefaultFont(options.font)

        # HTML 렌더링 시 기본 텍스트 색상 설정
        text_color = options.palette.color(QPalette.ColorRole.Text).name()
        html_content = f"<div style='color: {text_color};'>{options.text}</div>"
        doc.setHtml(html_content)

        # 배경색 및 선택 효과 등을 먼저 그립니다.
        options.text = ""
        option.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, options, painter)

        # 텍스트를 그릴 위치로 이동합니다.
        painter.translate(options.rect.left(), options.rect.top())
        clip = QRectF(0, 0, options.rect.width(), options.rect.height())
        painter.setClipRect(clip)

        # 실제 HTML 내용을 그립니다.
        ctx = QAbstractTextDocumentLayout.PaintContext()
        # 선택된 상태일 경우 반전 등을 위해 텍스트 색상을 조절할 수도 있지만,
        # HTML 내부에 이미 색상 지정이 있을 수 있으므로 기본 ctx를 사용합니다.
        doc.documentLayout().draw(painter, ctx)

        painter.restore()

    def sizeHint(self, option, index):
        options = QStyleOptionViewItem(option)
        self.initStyleOption(options, index)
        doc = QTextDocument()
        doc.setDefaultFont(options.font)
        doc.setHtml(options.text)
        return QSize(int(doc.idealWidth()), int(doc.size().height()))


class HistoryItemDelegate(QStyledItemDelegate):
    """
    콤보박스 히스토리 항목 우측에 삭제(×) 버튼을 표시하고 마우스 오버 이벤트를 처리하는 델리게이트입니다.
    """

    # 항목 삭제 요청 시 발생하는 시그널
    item_delete_requested = Signal(str)

    def paint(self, painter, option, index):
        """항목을 렌더링하며 마우스 오버 시 삭제 버튼(×)을 추가로 그립니다."""
        super().paint(painter, option, index)

        # 현재 마우스가 올라가 있는 선택된 항목에만 버튼을 표시합니다.
        if not (option.state & QStyle.State.State_Selected):
            return

        # 히스토리 비우기 등 특수한 동작 항목에는 버튼을 표시하지 않습니다.
        if index.data(Qt.ItemDataRole.UserRole) == AppStrings.HISTORY_ACTION_CLEAR:
            return

        # 우측 끝 부분에 버튼이 그려질 영역을 계산합니다.
        margin = 10
        btn_width = option.rect.height()
        x_rect = QRect(
            option.rect.right() - btn_width - margin,
            option.rect.top(),
            btn_width,
            btn_width,
        )

        painter.save()
        # 마우스 커서가 버튼 위에 있는지 확인하여 색상을 변경합니다.
        mouse_pos = option.widget.mapFromGlobal(QCursor.pos())
        if x_rect.contains(mouse_pos):
            painter.setPen(QColor(AppStrings.COLOR_RED))
        else:
            painter.setPen(option.palette.color(QPalette.ColorRole.Text))

        painter.drawText(x_rect, Qt.AlignmentFlag.AlignCenter, AppStrings.SYMBOL_CLOSE)
        painter.restore()

    def editorEvent(self, event, model, option, index):
        if event.type() == QEvent.Type.MouseMove:
            option.widget.update(index)
        return False


class HistoryComboBox(QComboBox):
    """
    사용자의 검색 및 필터 기록을 관리하며, 리스트 내에서 개별 항목 삭제 기능을 제공하는 커스텀 콤보박스입니다.
    데이터베이스와 연동되어 히스토리를 유지하고 자동 완성 기능을 지원합니다.
    """

    # 개별 항목 삭제 시 발생하는 시그널 (삭제된 텍스트 전달)
    history_item_deleted = Signal(str)
    # 전체 히스토리 삭제 시 발생하는 시그널
    history_cleared = Signal()

    def __init__(self, parent=None):
        """히스토리 관리 기능이 있는 콤보박스를 초기화합니다."""
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.lineEdit().setClearButtonEnabled(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.view().setMouseTracking(True)
        self.setItemDelegate(HistoryItemDelegate(self))
        self.view().viewport().installEventFilter(self)

        self.completer().setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.activated.connect(self._on_activated)

    def setPlaceholderText(self, text):
        """내부 QLineEdit에 플레이스홀더 텍스트를 설정합니다."""
        self.lineEdit().setPlaceholderText(text)

    def eventFilter(self, source, event):
        """콤보박스 목록 내의 특정 항목에서 우측 삭제 버튼 클릭을 감지합니다."""
        if source == self.view().viewport() and event.type() in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease,
        ):
            index = self.view().indexAt(event.position().toPoint())
            if index.isValid() and index.data(Qt.ItemDataRole.UserRole) != AppStrings.HISTORY_ACTION_CLEAR:
                rect = self.view().visualRect(index)
                margin = 10
                btn_size = rect.height()
                x_rect = QRect(rect.right() - btn_size - margin, rect.top(), btn_size, btn_size)

                # 클릭 위치가 버튼 영역 안인지 확인합니다.
                if x_rect.contains(event.position().toPoint()):
                    if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                        text = self.itemText(index.row())
                        self.history_item_deleted.emit(text)

                        # 화면에서 즉시 항목을 제거하여 사용자 응답성을 높입니다.
                        self.removeItem(index.row())
                    return True
        return super().eventFilter(source, event)

    def _on_activated(self, index):
        """'전체 삭제' 항목 선택 시 처리"""
        if self.itemData(index, Qt.ItemDataRole.UserRole) == AppStrings.HISTORY_ACTION_CLEAR:
            self.history_cleared.emit()
            self.clear()
            self.lineEdit().clear()

    def set_history(self, items: list[str]):
        """설정 관리자로부터 받은 데이터 목록을 콤보박스 항목으로 반영합니다."""
        self.blockSignals(True)
        self.clear()
        if items:
            self.addItems(items)
            # 마지막에 '전체 기록 비우기' 특수 항목을 추가합니다.
            self.addItem(AppStrings.HISTORY_CLEAR_ALL)
            self.setItemData(self.count() - 1, AppStrings.HISTORY_ACTION_CLEAR, Qt.ItemDataRole.UserRole)
        self.setCurrentIndex(-1)
        self.blockSignals(False)


class HotkeyLineEdit(QLineEdit):
    """
    사용자의 실제 키보드 입력을 가로채서 단축키 문자열(예: 'alt+shift+space')로 자동 변환해주는 특수 입력 위젯입니다.
    직접 타이핑하는 대신 키를 누르는 것만으로 단축키를 구성할 수 있습니다.
    """

    hotkey_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setPlaceholderText(AppStrings.HOTKEY_EDIT_PLACEHOLDER)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 키 매핑 (Qt.Key -> keyboard library names)
        self._key_map = {
            Qt.Key.Key_Control: AppStrings.KEY_CTRL,
            Qt.Key.Key_Shift: AppStrings.KEY_SHIFT,
            Qt.Key.Key_Alt: AppStrings.KEY_ALT,
            Qt.Key.Key_Meta: AppStrings.KEY_META,
            Qt.Key.Key_Space: AppStrings.KEY_SPACE,
            Qt.Key.Key_Escape: AppStrings.KEY_ESC,
            Qt.Key.Key_Delete: AppStrings.KEY_DELETE,
            Qt.Key.Key_Backspace: AppStrings.KEY_BACKSPACE,
            Qt.Key.Key_Enter: AppStrings.KEY_ENTER,
            Qt.Key.Key_Return: AppStrings.KEY_ENTER,
            Qt.Key.Key_Tab: AppStrings.KEY_TAB,
            Qt.Key.Key_Up: AppStrings.KEY_UP,
            Qt.Key.Key_Down: AppStrings.KEY_DOWN,
            Qt.Key.Key_Left: AppStrings.KEY_LEFT,
            Qt.Key.Key_Right: AppStrings.KEY_RIGHT,
            Qt.Key.Key_F1: "f1",
            Qt.Key.Key_F2: "f2",
            Qt.Key.Key_F3: "f3",
            Qt.Key.Key_F4: "f4",
            Qt.Key.Key_F5: "f5",
            Qt.Key.Key_F6: "f6",
            Qt.Key.Key_F7: "f7",
            Qt.Key.Key_F8: "f8",
            Qt.Key.Key_F9: "f9",
            Qt.Key.Key_F10: "f10",
            Qt.Key.Key_F11: "f11",
            Qt.Key.Key_F12: "f12",
        }

    def keyPressEvent(self, event):
        """키보드 입력을 가로채서 단축키 텍스트 조합을 생성합니다."""
        key = event.key()

        # ESC나 Backspace가 입력되면 설정을 초기화합니다.
        if key in (Qt.Key.Key_Escape, Qt.Key.Key_Backspace):
            self.clear()
            self.hotkey_changed.emit("")
            return

        # 수정자(Modifier) 키들의 조합 상태를 추출합니다.
        modifiers = event.modifiers()
        parts = []

        if modifiers & Qt.KeyboardModifier.ControlModifier:
            parts.append(AppStrings.KEY_CTRL)
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            parts.append(AppStrings.KEY_SHIFT)
        if modifiers & Qt.KeyboardModifier.AltModifier:
            parts.append(AppStrings.KEY_ALT)
        if modifiers & Qt.KeyboardModifier.MetaModifier:
            parts.append(AppStrings.KEY_META)

        # 실제 기능 키가 눌린 경우 최종 단축키 문자열을 확정합니다.
        if key not in (Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta):
            key_name = self._key_map.get(key)
            if not key_name:
                key_name = event.text().lower()
                if not key_name:
                    key_name = event.keyCombination().key().name.lower()

            if key_name:
                parts.append(key_name)
                hotkey_str = "+".join(parts)
                self.setText(hotkey_str)
                self.hotkey_changed.emit(hotkey_str)
                self.clearFocus()  # 입력이 완료되면 포커스를 해제하여 기록 모드를 종료합니다.
        else:
            # 수정자 키(Ctrl, Alt 등)만 눌린 경우 진행 상황을 보여줍니다.
            if parts:
                self.setText("+".join(parts) + "+...")
            else:
                self.setText(AppStrings.HOTKEY_RECORDING)

    def focusInEvent(self, event):
        self.setText(AppStrings.HOTKEY_RECORDING)
        # 기록 중임을 시각적으로 표현 (스타일 시트 활용 가능)
        self.setStyleSheet(AppStrings.STYLE_SETTINGS_RECORDING)
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        # 기록 중이 아니면 원래 스타일로 복구
        self.setStyleSheet("")
        # 입력이 중단된 경우(수정자만 있는 경우 등) 기존 설정값으로 복구
        if "+" in self.text() and self.text().endswith("..."):
            self.setText(self._saved_hotkey)
        elif self.text() == AppStrings.HOTKEY_RECORDING:
            self.setText(self._saved_hotkey)
        super().focusOutEvent(event)

    def setText(self, text):
        super().setText(text)
        if text and not text.endswith("...") and text != AppStrings.HOTKEY_RECORDING:
            self._saved_hotkey = text
