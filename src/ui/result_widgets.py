"""검색 결과 화면의 독립 위젯: 건너뛴 파일 창, 검색 중 스피너·빛 띠.

8.1.3에서 result_view.py에서 옮겼습니다. 동작은 그대로입니다.
"""

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from sf_utils.app_strings import AppStrings


def _is_walk_failure(path, reason):
    """폴더 탐색 실패(경로를 모르는 항목 포함)인지입니다. 사유는 코드 형식입니다(7.2.5)."""
    from core.skip_reason_codes import SKIP_CODE_WALK, UNKNOWN_WALK_PATH_PREFIX, decode_skip_reason

    return path.startswith(UNKNOWN_WALK_PATH_PREFIX) or any(
        decode_skip_reason(line)[0] == SKIP_CODE_WALK for line in str(reason or "").splitlines() if line.strip()
    )


def _skipped_count_text(entries, total):
    template = AppStrings.SKIPPED_ITEMS_COUNT if any(
        _is_walk_failure(path, reason) for path, reason in entries
    ) else AppStrings.SKIPPED_FILES_COUNT
    return template.format(total)


def normalize_skipped_files(skipped_files, *, strict_session: bool = False):
    """건너뛴 항목을 ``(경로, 사유 코드)`` 목록으로 정리합니다. 같은 경로의 사유는 줄로 합칩니다.

    사유는 코드 형식(``ERR_…|상세``)으로 두고, 문장은 보여 줄 때 ``render_skip_reason``으로 만듭니다(7.2.5).
    ``strict_session``이면 세션에서 읽은 값이라 모양을 엄격히 확인하고, 코드 형식이 아닌 사유는 버립니다.
    """
    from core.skip_reason_codes import TEMPLATE_NAMES, decode_skip_reason, is_coded_reason

    normalized_by_path: dict[str, str] = {}
    if not isinstance(skipped_files, (list, tuple)):
        return []
    for item in skipped_files:
        if isinstance(item, (list, tuple)):
            if not item:
                continue
            raw_path = item[0]
            raw_reason = item[1] if len(item) > 1 else ""
        else:
            if strict_session:
                continue
            raw_path = item
            raw_reason = ""

        if strict_session:
            if not isinstance(raw_path, str) or not isinstance(raw_reason, str):
                continue
            if (
                not raw_path.strip()
                or len(raw_path) > 32767
                or any(character in raw_path for character in ("\x00", "\r", "\n"))
                or len(raw_reason) > 65536
                or not is_coded_reason(raw_reason)
                or any(decode_skip_reason(line)[0] not in TEMPLATE_NAMES
                       for line in raw_reason.splitlines() if line.strip())
            ):
                continue

        path = str(raw_path).strip()
        if not path:
            continue
        reason = str(raw_reason or "").strip()
        previous_reason = normalized_by_path.get(path, "")
        if previous_reason and reason:
            reason_lines = list(dict.fromkeys(
                line for value in (previous_reason, reason) for line in value.splitlines() if line
            ))
            normalized_by_path[path] = "\n".join(reason_lines)
        elif reason or path not in normalized_by_path:
            normalized_by_path[path] = reason
    return list(normalized_by_path.items())


def _format_skipped_files_text(skipped_files, total_count):
    """건너뛴 파일 경로와 사유를 클립보드 친화적인 일반 텍스트로 만듭니다."""
    entries = normalize_skipped_files(skipped_files)
    blocks = []
    from core.skip_reason_codes import UNKNOWN_WALK_PATH_PREFIX

    walk_count = sum(_is_walk_failure(path, reason) for path, reason in entries)
    if walk_count:
        blocks.append(AppStrings.SKIPPED_FILES_AND_WALK_ERRORS.format(len(entries) - walk_count, walk_count))
    for index, (path, reason) in enumerate(entries, start=1):
        is_walk = _is_walk_failure(path, reason)
        if path.startswith(UNKNOWN_WALK_PATH_PREFIX):
            path = AppStrings.SKIPPED_WALK_PATH_UNKNOWN
        if is_walk:
            path = AppStrings.SKIPPED_WALK_ENTRY.format(path)
        lines = [f"{index}. {path}"]
        if reason:
            from core.skip_reason_formatter import render_skip_reason

            lines.append(f"   {AppStrings.SKIPPED_FILES_REASON.format(render_skip_reason(reason))}")
        blocks.append("\n".join(lines))

    missing_count = max(0, int(total_count or 0) - len(entries))
    if missing_count:
        blocks.append(AppStrings.SKIPPED_FILES_DETAILS_MISSING.format(missing_count))
    return "\n\n".join(blocks)


class SkippedFilesDialog(QDialog):
    """건너뛴 파일과 사유를 표시하고 전체 목록 복사를 제공하는 대화상자입니다."""

    def __init__(self, skipped_files, total_count, parent=None):
        super().__init__(parent)
        self.skipped_files = normalize_skipped_files(skipped_files)
        self.total_count = max(int(total_count or 0), len(self.skipped_files))
        self.list_text = _format_skipped_files_text(self.skipped_files, self.total_count)

        has_walk_errors = any(_is_walk_failure(path, reason) for path, reason in self.skipped_files)
        self.setWindowTitle(AppStrings.SKIPPED_ITEMS_DIALOG_TITLE if has_walk_errors else AppStrings.SKIPPED_FILES_DIALOG_TITLE)
        self.resize(760, 460)
        layout = QVBoxLayout(self)

        count_label = QLabel(_skipped_count_text(self.skipped_files, self.total_count))
        count_label.setStyleSheet("font-weight: 700;")
        layout.addWidget(count_label)

        self.list_edit = QPlainTextEdit()
        self.list_edit.setReadOnly(True)
        self.list_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.list_edit.setPlainText(self.list_text)
        layout.addWidget(self.list_edit)

        button_layout = QHBoxLayout()
        self.copy_confirmation_label = QLabel(AppStrings.SKIPPED_FILES_COPIED)
        self.copy_confirmation_label.setVisible(False)
        button_layout.addWidget(self.copy_confirmation_label)
        button_layout.addStretch(1)

        self.copy_button = QPushButton(AppStrings.SKIPPED_FILES_COPY_BUTTON)
        self.copy_button.clicked.connect(self.copy_to_clipboard)
        button_layout.addWidget(self.copy_button)

        close_button = QPushButton(AppStrings.SKIPPED_FILES_CLOSE_BUTTON)
        close_button.clicked.connect(self.accept)
        button_layout.addWidget(close_button)
        layout.addLayout(button_layout)

    def copy_to_clipboard(self):
        QApplication.clipboard().setText(self.list_text)
        self.copy_confirmation_label.setVisible(True)


class SearchSpinner(QWidget):
    """검색 중 문구 앞에서 도는 원호입니다. 글자 기호는 글꼴마다 폭이 달라 흔들리므로 직접 그립니다."""

    SIZE = 14
    STEP_DEGREES = 30
    INTERVAL_MS = 80

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        # 부모(요약 줄)의 테두리·여백·최소 높이 스타일을 물려받지 않게 합니다.
        self.setStyleSheet("border: none; background: transparent; padding: 0; margin: 0; min-height: 0;")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._angle = 0
        self._color = QColor("#C47B00")
        self._timer = QTimer(self)
        self._timer.setInterval(self.INTERVAL_MS)
        self._timer.timeout.connect(self._advance)
        self.hide()

    def set_color(self, color):
        self._color = QColor(color)
        self.update()

    def start(self):
        if not self._timer.isActive():
            self._timer.start()
        self.show()

    def stop(self):
        self._timer.stop()
        self.hide()

    def is_spinning(self):
        return self._timer.isActive()

    def _advance(self):
        self._angle = (self._angle + self.STEP_DEGREES) % 360
        self.update()

    def paintEvent(self, _event):
        from PySide6.QtGui import QPainter, QPen

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(self._color, 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        rect = self.rect().adjusted(2, 2, -2, -2)
        painter.drawArc(rect, -self._angle * 16, 270 * 16)
        painter.end()


class SearchShimmer(QWidget):
    """검색 중 요약 줄 위로 밝은 띠가 왼쪽에서 오른쪽으로 지나가는 효과입니다(7.1.20).

    요약 줄과 같은 크기로 겹쳐 그리며, 마우스 입력은 아래로 넘깁니다.
    """

    PERIOD_MS = 1800
    INTERVAL_MS = 30
    BAND_RATIO = 0.4

    def __init__(self, parent):
        super().__init__(parent)
        self.setStyleSheet("border: none; background: transparent; padding: 0; margin: 0; min-height: 0;")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._target = parent
        self._phase = 0.0
        self._color = QColor(240, 196, 106, 70)
        self._timer = QTimer(self)
        self._timer.setInterval(self.INTERVAL_MS)
        self._timer.timeout.connect(self._advance)
        parent.installEventFilter(self)
        self.hide()

    def eventFilter(self, watched, event):
        if watched is self.parent() and event.type() == QEvent.Type.Resize:
            self.setGeometry(watched.rect())
        return False

    def set_color(self, color):
        self._color = QColor(color)
        self.update()

    def start(self):
        self.setGeometry(self._target.rect())
        if not self._timer.isActive():
            self._phase = 0.0
            self._timer.start()
        self.show()
        self.raise_()

    def stop(self):
        self._timer.stop()
        self.hide()

    def is_running(self):
        return self._timer.isActive()

    def _advance(self):
        self._phase = (self._phase + self.INTERVAL_MS / self.PERIOD_MS) % 1.0
        self.update()

    def paintEvent(self, _event):
        from PySide6.QtGui import QLinearGradient, QPainter

        width = self.width()
        band = max(1.0, width * self.BAND_RATIO)
        # 띠가 왼쪽 밖에서 들어와 오른쪽 밖으로 완전히 나갈 때까지 움직입니다.
        left = -band + (width + band) * self._phase
        gradient = QLinearGradient(left, 0, left + band, 0)
        clear = QColor(self._color)
        clear.setAlpha(0)
        gradient.setColorAt(0.0, clear)
        gradient.setColorAt(0.5, self._color)
        gradient.setColorAt(1.0, clear)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        # 테두리 안쪽만 칠합니다.
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 3, 3)
        painter.end()
