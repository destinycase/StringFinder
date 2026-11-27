"""검색 상세의 문맥 미리보기: 파일에서 필요한 줄만 읽는 부분(UI 스레드 밖에서 실행).

8.1.3에서 result_view.py에서 옮겼습니다. 동작은 그대로입니다.
"""

from PySide6.QtCore import QObject, QRunnable, Signal

# 이보다 뒤의 줄은 미리보기를 읽지 않습니다(ResultView.CONTEXT_MAX_SCAN_LINES와 같은 값).
CONTEXT_MAX_SCAN_LINES = 200_000


def _detect_text_encoding(file_path):
    """미리보기 인코딩을 검색 엔진과 같은 판정(파일 전체 기준, 설정의 지정 인코딩 우선)으로 정합니다."""
    with open(file_path, "rb") as file:
        sample = file.read(4)
    if sample.startswith(b"\xff\xfe\x00\x00"):
        return "utf-32-le"
    if sample.startswith(b"\x00\x00\xfe\xff"):
        return "utf-32-be"
    from core.search_engine import detect_preview_encoding

    return detect_preview_encoding(file_path)


def _read_context_lines_from_file(file_path, target_line, before_lines, after_lines, cancel_event=None):
    """파일에서 필요한 문맥만 읽습니다. UI 객체에는 접근하지 않습니다."""
    if target_line > CONTEXT_MAX_SCAN_LINES:
        return None
    first_line = max(1, target_line - before_lines)
    last_line = target_line + after_lines
    encoding = _detect_text_encoding(file_path)
    context = []
    with open(file_path, "r", encoding=encoding, errors="replace") as file:
        for line_number, line in enumerate(file, start=1):
            if cancel_event is not None and cancel_event.is_set():
                return []
            if line_number >= first_line:
                context.append((line_number, line.rstrip("\r\n")))
            if line_number >= last_line:
                break
    return context


class ContextPreviewSignals(QObject):
    finished = Signal(int, object, object, bool)


class ContextPreviewWorker(QRunnable):
    """문맥 미리보기용 파일 읽기를 UI 스레드 밖에서 수행합니다."""

    def __init__(self, request_id, file_path, target_line, before_lines, after_lines, cancel_event):
        super().__init__()
        self.setAutoDelete(False)
        self.request_id = request_id
        self.file_path = file_path
        self.target_line = target_line
        self.before_lines = before_lines
        self.after_lines = after_lines
        self.cancel_event = cancel_event
        self.signals = ContextPreviewSignals()

    def run(self):
        try:
            lines = _read_context_lines_from_file(
                self.file_path,
                self.target_line,
                self.before_lines,
                self.after_lines,
                self.cancel_event,
            )
            cancelled = self.cancel_event.is_set()
            self.signals.finished.emit(self.request_id, lines, None, cancelled)
        except Exception as error:  # 파일/인코딩/예상하지 못한 읽기 오류를 UI로 전달
            self.signals.finished.emit(self.request_id, None, error, self.cancel_event.is_set())
