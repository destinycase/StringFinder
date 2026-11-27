"""8.1.3·8.1.4에서 result_view.py에서 떼어 낸 모듈을 ResultView 없이 검사합니다."""

import ast
import threading
from pathlib import Path

from ui import result_column_layout, result_copy_export, result_preview, result_view, result_widgets
from ui.result_column_layout import compute_match_column_widths

SRC_UI = Path(__file__).resolve().parents[1] / "src" / "ui"
OPTS = dict(minimum=40, key_min=80, key_max_ratio=0.15, position_max=120, value_min_ratio=0.5)


def test_split_modules_never_import_result_view():
    for name in ("result_column_layout", "result_preview", "result_widgets"):
        tree = ast.parse((SRC_UI / f"{name}.py").read_text(encoding="utf-8"))
        modules = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        modules += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        assert not any(m and "result_view" in m for m in modules), name


def test_result_view_keeps_old_names_for_existing_imports():
    assert result_view.SkippedFilesDialog is result_widgets.SkippedFilesDialog
    assert result_view.normalize_skipped_files is result_widgets.normalize_skipped_files
    assert result_view._read_context_lines_from_file is result_preview._read_context_lines_from_file
    assert result_view.MatchColumnWatcher is result_column_layout.MatchColumnWatcher
    assert result_view.ResultView.CONTEXT_MAX_SCAN_LINES == result_preview.CONTEXT_MAX_SCAN_LINES


def test_widths_fill_the_view_exactly_and_value_columns_share_the_rest():
    widths = compute_match_column_widths(1000, 3, [50, 200, 900], [2], None, **OPTS)
    assert sum(widths) == 1000
    assert widths[0] == 50 and widths[1] == 150  # 키 열은 표 폭의 15%까지
    archive = compute_match_column_widths(1000, 4, [50, 100, 600, 600], [2, 3], None, **OPTS)
    assert sum(archive) == 1000 and abs(archive[2] - archive[3]) <= 1


def test_user_ratios_win_and_missing_natural_widths_use_defaults():
    assert compute_match_column_widths(1000, 2, None, [1], [0.25, 0.75], **OPTS) == [250, 750]
    default = compute_match_column_widths(1000, 3, None, [2], None, **OPTS)
    assert sum(default) == 1000 and default[0] == 60
    assert compute_match_column_widths(1000, 0, None, [], None, **OPTS) == []
    narrow = compute_match_column_widths(10, 3, [50, 50, 50], [2], None, **OPTS)
    assert all(w >= 40 for w in narrow)


def test_preview_reads_only_needed_lines_and_honours_cancel(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("".join(f"line{i}\n" for i in range(1, 21)), encoding="utf-8")
    lines = result_preview._read_context_lines_from_file(str(path), 10, 2, 1)
    assert lines == [(8, "line8"), (9, "line9"), (10, "line10"), (11, "line11")]
    cancelled = threading.Event()
    cancelled.set()
    assert result_preview._read_context_lines_from_file(str(path), 10, 2, 1, cancelled) == []
    beyond = result_preview.CONTEXT_MAX_SCAN_LINES + 1
    assert result_preview._read_context_lines_from_file(str(path), beyond, 1, 1) is None


class _FakeResults:
    def __init__(self, rows):
        self.rows = rows

    def get_all_results(self):
        return self.rows


class _Exporter(result_copy_export.ResultCopyExportMixin):
    """ResultView 없이 복사·내보내기 메서드만 씁니다."""

    def __init__(self, mode, rows=(), paths=()):
        self.search_mode = mode
        self.result_model = _FakeResults(list(rows))
        self._paths = list(paths)
        self.copied = []

    def _selected_result_paths_in_order(self):
        return self._paths

    def _copy_to_clipboard(self, text):
        self.copied.append(text)


def test_copy_export_module_never_imports_result_view():
    tree = ast.parse((SRC_UI / "result_copy_export.py").read_text(encoding="utf-8"))
    modules = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    assert not any(m and "result_view" in m for m in modules)
    assert issubclass(result_view.ResultView, result_copy_export.ResultCopyExportMixin)
    assert result_view._sanitize_excel_cell is result_copy_export._sanitize_excel_cell


def test_export_fields_follow_search_mode():
    item = ("3", "ns", "key", "src", "dst", "extra")
    assert _Exporter("ARCHIVE")._match_fields_for_export(item) == ("3", "ns", "key", "src", "dst")
    assert _Exporter("EXCEL")._match_fields_for_export(item) == ("3", "ns", "key", "src")
    assert _Exporter("JSON")._match_fields_for_export(item) == ("3", "ns", "key")
    assert _Exporter("NORMAL")._match_fields_for_export((7, None)) == ("7", "")
    assert _Exporter("NORMAL")._match_fields_for_export(("only",)) == ()


def test_copy_names_and_unique_folders_keep_list_order():
    exporter = _Exporter("NORMAL", paths=[r"C:\b\x.txt", r"C:\a\y.txt", r"c:/B/z.txt"])
    exporter._copy_selected_result_file_names()
    exporter._copy_selected_result_folders()
    assert exporter.copied[0] == "x.txt\ny.txt\nz.txt"
    assert exporter.copied[1].splitlines()[:2] == [r"C:\b", r"C:\a"]
    assert len(exporter.copied[1].splitlines()) == 2


def test_text_export_writes_atomically(tmp_path):
    target = tmp_path / "out.txt"
    exporter = _Exporter("NORMAL", rows=[(2, "a.txt", "C:/", "C:/a.txt", [(1, "hello"), (5, "world")])])
    exporter._export_to_text(str(target))
    text = target.read_text(encoding="utf-8")
    assert "[2] C:/a.txt" in text and "hello" in text and "world" in text
    assert [p.name for p in tmp_path.iterdir()] == ["out.txt"]  # 임시 파일이 남지 않음


def test_excel_cells_never_become_formulas():
    assert result_copy_export._sanitize_excel_cell("=1+1") == "'=1+1"
    assert result_copy_export._sanitize_excel_cell("a\x1bb") == "ab"
    assert result_copy_export._text_or_empty(None) == ""


def test_skipped_files_merge_reasons_per_path():
    entries = result_widgets.normalize_skipped_files([("a", "ERR_X|1"), ("a", "ERR_Y|2"), "b"])
    assert entries == [("a", "ERR_X|1\nERR_Y|2"), ("b", "")]
