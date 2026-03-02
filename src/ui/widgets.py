from PySide6.QtCore import QEvent, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAbstractTextDocumentLayout,
    QColor,
    QCursor,
    QPainter,
    QPalette,
    QPen,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QComboBox,
    QCompleter,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)

from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants


class HtmlDelegate(QStyledItemDelegate):
    """텍스트 내 HTML 태그를 해석하여 하이라이팅된 결과를 렌더링하는 델리게이트입니다."""

    def paint(self, painter, option, index):
        options = QStyleOptionViewItem(option)
        self.initStyleOption(options, index)
        painter.save()
        doc = QTextDocument()
        doc.setDefaultFont(options.font)  # type: ignore

        # 현재 셀이 선택되었는지 확인하여 테마에 맞는 하이라이트 색상을 적용합니다.
        is_selected = bool(options.state & QStyle.StateFlag.State_Selected)  # type: ignore
        if is_selected:
            text_color = options.palette.color(QPalette.ColorRole.HighlightedText).name()  # type: ignore
        else:
            text_color = options.palette.color(QPalette.ColorRole.Text).name()  # type: ignore

        html_content = f"<div style='color: {text_color};'>{options.text}</div>"  # type: ignore
        doc.setHtml(html_content)
        options.text = ""  # type: ignore
        option.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, options, painter)
        painter.translate(options.rect.left(), options.rect.top())  # HTML 렌더링을 위해 좌표계를 셀 위치로 이동합니다.  # type: ignore[attr-defined]
        clip = QRectF(0, 0, options.rect.width(), options.rect.height())  # type: ignore
        painter.setClipRect(clip)
        ctx = QAbstractTextDocumentLayout.PaintContext()
        doc.documentLayout().draw(painter, ctx)
        painter.restore()

    def sizeHint(self, option, index):
        options = QStyleOptionViewItem(option)
        self.initStyleOption(options, index)
        doc = QTextDocument()
        doc.setDefaultFont(options.font)  # type: ignore
        doc.setHtml(options.text)  # type: ignore
        return QSize(int(doc.idealWidth()), int(doc.size().height()))


class HistoryItemDelegate(QStyledItemDelegate):
    """콤보박스 목록 우측에 삭제 버튼(X)을 표시하고 이벤트를 처리하는 델리게이트입니다."""

    item_delete_requested = Signal(str)

    def paint(self, painter, option, index):
        """항목이 선택된 상태일 때 우측에 삭제 버튼을 그립니다."""
        super().paint(painter, option, index)
        if not (option.state & QStyle.StateFlag.State_Selected):
            return
        if index.data(Qt.ItemDataRole.UserRole) == Constants.HISTORY_ACTION_CLEAR:
            return
        margin = 10
        btn_width = option.rect.height()
        x_rect = QRect(
            option.rect.right() - btn_width - margin,
            option.rect.top(),
            btn_width,
            btn_width,
        )
        painter.save()
        mouse_pos = option.widget.mapFromGlobal(QCursor.pos())
        if x_rect.contains(mouse_pos):
            painter.setPen(QColor(Constants.COLOR_RED))
        else:
            painter.setPen(option.palette.color(QPalette.ColorRole.Text))
        painter.drawText(x_rect, Qt.AlignmentFlag.AlignCenter, Constants.SYMBOL_CLOSE)
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

    history_item_deleted = Signal(str)
    history_cleared = Signal()

    def __init__(self, parent=None):
        """히스토리 관리 기능이 있는 콤보박스를 초기화합니다."""
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        line_edit = self.lineEdit()
        if line_edit:
            line_edit.setClearButtonEnabled(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.view().setMouseTracking(True)
        self.setItemDelegate(HistoryItemDelegate(self))
        self.view().viewport().installEventFilter(self)
        completer = self.completer()
        if completer:
            completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.activated.connect(self._on_activated)

    def setPlaceholderText(self, text):
        """콤보박스의 입력 줄에 안내 문구(Placeholder)를 설정합니다."""
        line_edit = self.lineEdit()
        if line_edit:
            line_edit.setPlaceholderText(text)

    def eventFilter(self, source, event):
        """콤보박스 목록 내의 특정 항목에서 우측 삭제 버튼 클릭을 감지합니다."""
        if source == self.view().viewport() and event.type() in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease,
        ):
            index = self.view().indexAt(event.position().toPoint())
            if index.isValid() and index.data(Qt.ItemDataRole.UserRole) != Constants.HISTORY_ACTION_CLEAR:
                rect = self.view().visualRect(index)
                margin = 10
                btn_size = rect.height()
                x_rect = QRect(rect.right() - btn_size - margin, rect.top(), btn_size, btn_size)
                if x_rect.contains(event.position().toPoint()):
                    if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                        text = self.itemText(index.row())
                        self.history_item_deleted.emit(text)
                        self.removeItem(index.row())
                    return True
        return super().eventFilter(source, event)

    def _on_activated(self, index):
        """목록 하단의 '전체 삭제' 항목을 클릭했을 때의 동작을 정의합니다."""
        if self.itemData(index, Qt.ItemDataRole.UserRole) == Constants.HISTORY_ACTION_CLEAR:
            self.history_cleared.emit()
            self.clear()
            line_edit = self.lineEdit()
            if line_edit:
                line_edit.clear()

    def set_history(self, items: list[str]):
        """설정 관리자로부터 받은 데이터 목록을 콤보박스 항목으로 반영합니다."""
        self.blockSignals(True)
        self.clear()
        if items:
            self.addItems(items)
            self.addItem(AppStrings.HISTORY_CLEAR_ALL)
            self.setItemData(self.count() - 1, Constants.HISTORY_ACTION_CLEAR, Qt.ItemDataRole.UserRole)
        self.setCurrentIndex(-1)
        self.blockSignals(False)

    def set_current_text(self, text):
        """콤보박스의 현재 입력 텍스트를 설정합니다."""
        line_edit = self.lineEdit()
        if line_edit:
            line_edit.setText(text)





class LoadingSpinner(QWidget):
    """
    작업 중임을 나타내는 회전하는 원형 스피너 위젯입니다.
    선형 진행바와 달리 무한히 루프되는 애니메이션을 제공하여 '검색 중' 상태를 시각화합니다.
    """

    def __init__(self, parent=None, size=20, color=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.setInterval(30)  # 약 33fps
        self._timer.timeout.connect(self._rotate)
        self._color = color

    def start(self):
        """애니메이션을 시작하고 위젯을 표시합니다."""
        self.show()
        if not self._timer.isActive():
            self._timer.start()

    def stop(self):
        """애니메이션을 중지하고 위젯을 숨깁니다."""
        self._timer.stop()
        self.hide()

    def _rotate(self):
        self._angle = (self._angle + 10) % 360
        self.update()

    def paintEvent(self, event):
        """스피너를 그립니다."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect().adjusted(2, 2, -2, -2)
        pen = QPen()
        pen.setWidth(2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)

        # 시스템 테마에 정의된 강조 색상(Highlight)을 기본값으로 사용합니다.
        color = self._color
        if color is None:
            color = self.palette().color(QPalette.ColorRole.Highlight)

        # 부드러운 회전 효과를 위해 일정 각도의 원호를 그립니다.
        pen.setColor(color)
        painter.setPen(pen)

        # 약 270도 길이의 원호를 그려 스피너 형태를 구현합니다.
        painter.drawArc(rect, self._angle * 16, 270 * 16)
