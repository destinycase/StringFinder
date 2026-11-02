from PySide6.QtCore import QRect, QRectF, QSize
from PySide6.QtGui import (
    QAbstractTextDocumentLayout,
    QPalette,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
)



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
        else:
            text_color = options.palette.color(QPalette.ColorRole.Text).name()  # type: ignore

        html_content = f"<div style='color: {text_color};'>{options.text}</div>"  # type: ignore
        doc.setHtml(html_content)
        options.text = ""  # type: ignore
        option.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, options, painter)
        rect = getattr(options, "rect")
        rect = QRect(
            rect.left() + self.cell_left_margin,
            rect.top(),
            max(0, rect.width() - (self.cell_left_margin * 2)),
            rect.height(),
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
