from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import (
    QAbstractTextDocumentLayout,
    QColor,
    QPalette,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
)



# 상세 목록의 파일 묶음 표시용 역할: "header"(파일 머리줄) 또는 "child"(묶음 안 결과의 첫 칸)입니다(7.1.27).
GROUP_ROLE = Qt.ItemDataRole(Qt.ItemDataRole.UserRole.value + 1)
GROUP_HEADER_GAP = 6
GROUP_HEADER_LINE = 2
GROUP_HEADER_LINE_COLOR = QColor(91, 127, 176)
GROUP_CHILD_INDENT = 18


class HtmlDelegate(QStyledItemDelegate):
    """텍스트 내 HTML 태그를 해석하여 하이라이팅된 결과를 렌더링하는 델리게이트입니다."""

    compact = False
    cell_left_margin = 6

    def paint(self, painter, option, index):
        options = QStyleOptionViewItem(option)
        self.initStyleOption(options, index)
        painter.save()
        doc = QTextDocument()
        if self.compact:
            doc.setDocumentMargin(1)
        doc.setDefaultFont(options.font)  # type: ignore

        # 현재 셀이 선택되었는지 확인하여 테마에 맞는 하이라이트 색상을 적용합니다.
        is_selected = bool(options.state & QStyle.StateFlag.State_Selected)  # type: ignore
        if is_selected:
            text_color = options.palette.color(QPalette.ColorRole.HighlightedText).name()  # type: ignore
        elif not index.flags():
            # 파일 머리줄처럼 고를 수 없게 만든 행도 흐리게 그리지 않습니다(7.1.26).
            text_color = options.palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Text).name()  # type: ignore
        else:
            text_color = options.palette.color(QPalette.ColorRole.Text).name()  # type: ignore

        html_content = f"<div style='color: {text_color};'>{options.text}</div>"  # type: ignore
        doc.setHtml(html_content)
        options.text = ""  # type: ignore
        option.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, options, painter)
        rect = getattr(options, "rect")
        kind = index.data(GROUP_ROLE)
        top_offset = 0
        left_extra = 0
        if kind == "header":
            # 머리줄 위에 표 바탕색 여백과 굵은 윗선을 그려 파일 사이를 끊어 줍니다.
            painter.fillRect(QRect(rect.left(), rect.top(), rect.width(), GROUP_HEADER_GAP),
                             options.palette.color(QPalette.ColorRole.Base))  # type: ignore
            painter.fillRect(QRect(rect.left(), rect.top() + GROUP_HEADER_GAP, rect.width(), GROUP_HEADER_LINE),
                             GROUP_HEADER_LINE_COLOR)
            top_offset = GROUP_HEADER_GAP + GROUP_HEADER_LINE
        elif kind == "child":
            left_extra = GROUP_CHILD_INDENT
        rect = QRect(
            rect.left() + self.cell_left_margin + left_extra,
            rect.top() + top_offset,
            max(0, rect.width() - (self.cell_left_margin * 2) - left_extra),
            rect.height() - top_offset,
        )
        painter.translate(rect.left(), rect.top())  # HTML 렌더링을 위해 좌표계를 셀 위치로 이동합니다.
        clip = QRectF(0, 0, rect.width(), rect.height())
        painter.setClipRect(clip)
        ctx = QAbstractTextDocumentLayout.PaintContext()
        doc.documentLayout().draw(painter, ctx)
        painter.restore()

    def sizeHint(self, option, index):
        options = QStyleOptionViewItem(option)
        self.initStyleOption(options, index)
        doc = QTextDocument()
        if self.compact:
            doc.setDocumentMargin(1)
        doc.setDefaultFont(options.font)  # type: ignore
        doc.setHtml(options.text)  # type: ignore
        return QSize(int(doc.idealWidth()) + (self.cell_left_margin * 2), int(doc.size().height()))
