from PySide6.QtWidgets import QComboBox, QStyledItemDelegate, QStyle, QCompleter, QSizePolicy, QLineEdit
from PySide6.QtCore import Qt, QRect, QEvent, Signal
from PySide6.QtGui import QColor, QCursor, QPalette
from utils.app_strings import AppStrings


class HistoryItemDelegate(QStyledItemDelegate):
    """
    콤보박스 히스토리 항목 우측에 삭제(×) 버튼을 표시하고 마우스 오버 이벤트를 처리하는 델리게이트입니다.
    """

    # 항목 삭제 요청 시 발생하는 시그널
    item_delete_requested = Signal(str)

    def paint(self, painter, option, index):
        super().paint(painter, option, index)

        # 항목이 선택된(마우스 오버) 상태일 때만 삭제 버튼 표시
        if not (option.state & QStyle.State.State_Selected):
            return

        # 특수 항목(전체 삭제)에는 버튼 미표시
        if index.data(Qt.ItemDataRole.UserRole) == AppStrings.HISTORY_ACTION_CLEAR:
            return

        # 우측 끝에 '×' 표시 영역 계산
        margin = 10
        btn_width = option.rect.height()
        x_rect = QRect(
            option.rect.right() - btn_width - margin,
            option.rect.top(),
            btn_width,
            btn_width,
        )

        painter.save()
        # 마우스 위치에 따른 강조
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

    def eventFilter(self, source, event):
        """삭제 버튼 클릭 이벤트 감지 및 처리"""
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

                if x_rect.contains(event.position().toPoint()):
                    if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                        text = self.itemText(index.row())
                        self.history_item_deleted.emit(text)
                        # removeItem은 외부 동기화 후 _load_histories 등에 의해 처리되거나 여기서 직접 해도 됨
                        # 여기서는 즉각적인 체감을 위해 직접 삭제
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
        """저장된 히스토리 목록으로 항목 초기화"""
        self.blockSignals(True)
        self.clear()
        if items:
            self.addItems(items)
            # 전체 삭제 항목 추가
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
            Qt.Key.Key_Control: "ctrl",
            Qt.Key.Key_Shift: "shift",
            Qt.Key.Key_Alt: "alt",
            Qt.Key.Key_Meta: "meta",  # Windows Key
            Qt.Key.Key_Space: "space",
            Qt.Key.Key_Escape: "esc",
            Qt.Key.Key_Delete: "delete",
            Qt.Key.Key_Backspace: "backspace",
            Qt.Key.Key_Enter: "enter",
            Qt.Key.Key_Return: "enter",
            Qt.Key.Key_Tab: "tab",
            Qt.Key.Key_Up: "up",
            Qt.Key.Key_Down: "down",
            Qt.Key.Key_Left: "left",
            Qt.Key.Key_Right: "right",
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
        key = event.key()

        # ESC나 Backspace는 초기화 용도로 사용
        if key in (Qt.Key.Key_Escape, Qt.Key.Key_Backspace):
            self.clear()
            self.hotkey_changed.emit("")
            return

        # 수정자(Modifier) 키만 눌린 경우 텍스트만 갱신하고 대기
        modifiers = event.modifiers()
        parts = []

        if modifiers & Qt.KeyboardModifier.ControlModifier:
            parts.append("ctrl")
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            parts.append("shift")
        if modifiers & Qt.KeyboardModifier.AltModifier:
            parts.append("alt")
        if modifiers & Qt.KeyboardModifier.MetaModifier:
            parts.append("meta")

        # 실제 키 값 추출 (수정자 키 자체인 경우 제외)
        if key not in (Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta):
            key_name = self._key_map.get(key)
            if not key_name:
                key_name = event.text().lower()
                if not key_name:  # 텍스트가 없는 특수키의 경우
                    key_name = event.keyCombination().key().name.lower()

            if key_name:
                parts.append(key_name)
                hotkey_str = "+".join(parts)
                self.setText(hotkey_str)
                self.hotkey_changed.emit(hotkey_str)
                self.clearFocus()  # 입력 완료 후 포커스 해제
        else:
            # 수정자 키만 눌린 상태 표시
            if parts:
                self.setText("+".join(parts) + "+...")
            else:
                self.setText(AppStrings.HOTKEY_RECORDING)

    def focusInEvent(self, event):
        self.setText(AppStrings.HOTKEY_RECORDING)
        # 기록 중임을 시각적으로 표현 (스타일 시트 활용 가능)
        self.setStyleSheet("QLineEdit { border: 2px solid #3498db; background-color: #2c3e50; }")
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
