"""여러 파일을 골라 상세 목록에 합쳐 보기, 파일 목록 기타 칸(7.1.24)."""
import time

import pytest
from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtWidgets import QFileIconProvider

from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants
from ui.models import MatchDetailModel
from ui.result_view import ResultView


def _view(qtbot, mock_config_manager):
    view = ResultView(QFileIconProvider(), mock_config_manager)
    qtbot.addWidget(view)
    view.resize(1000, 700)
    view.show()
    view.set_search_context("sword", Constants.MODE_NORMAL)
    view.set_results([
        [3, "a.txt", "D:/d", "D:/d/a.txt", [[1, "sword a1", 0, 5], [2, "sword a2", 0, 5], [3, "sword a3", 0, 5]]],
        [2, "b.txt", "D:/d", "D:/d/b.txt", [[7, "sword b7", 0, 5], [8, "sword b8", 0, 5],
                                           [-1, AppStrings.MSG_MATCH_LIMIT_PER_FILE.format(2), None, None]]],
        [1, "c.txt", "D:/d", "D:/d/c.txt", [[4, "sword c4", 0, 5]]],
    ])
    return view


def _rows(view):
    model = view.match_proxy_model
    return [
        [model.data(model.index(row, col), Qt.ItemDataRole.EditRole) for col in range(model.columnCount())]
        for row in range(model.rowCount())
    ]


def _select_rows(view, rows):
    selection = view.result_view.selectionModel()
    flags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
    first = True
    for row in rows:
        index = view.proxy_model.index(row, 0)
        if first:
            selection.setCurrentIndex(index, QItemSelectionModel.SelectionFlag.ClearAndSelect
                                      | QItemSelectionModel.SelectionFlag.Rows)
            first = False
        else:
            selection.select(index, flags)


def _path_of_row(view, row):
    return view.result_model.get_item(view.proxy_model.mapToSource(view.proxy_model.index(row, 0)).row())[0]


def test_single_file_detail_has_no_file_column_and_no_notice_row(qtbot, mock_config_manager):
    view = _view(qtbot, mock_config_manager)
    row_b = next(r for r in range(3) if _path_of_row(view, r).endswith("b.txt"))
    _select_rows(view, [row_b])
    qtbot.waitUntil(lambda: view.match_model.current_file_path.endswith("b.txt"))
    assert not view.match_model.multi_file
    assert view.match_model.columnCount() == 1
    # 결과 수 제한 안내 줄은 상세 목록에 없습니다(파일 목록 기타 칸에서 봄).
    assert view.match_model.match_count == 2 and len(_rows(view)) == 2


def test_multiple_selected_files_are_combined_in_list_order(qtbot, mock_config_manager):
    view = _view(qtbot, mock_config_manager)
    _select_rows(view, [0, 1, 2])
    qtbot.waitUntil(lambda: view.match_model.multi_file)
    model = view.match_model
    # 파일 칸 방식은 없앴고(7.1.27) 파일마다 머리줄이 붙습니다.
    assert model.columnCount() == 1 and model.grouped
    order = [_path_of_row(view, r) for r in range(3)]
    header_paths = [model.data(model.index(r, 0), Qt.ItemDataRole.UserRole)
                    for r in range(model.rowCount()) if model.is_group_row(r)]
    assert header_paths == order
    assert model.match_count == 6
    assert AppStrings.SELECTED_FILES_INFO_TEMPLATE.format(3, "6") == view.file_info_label.text()


def test_select_all_shows_combined_detail(qtbot, mock_config_manager):
    view = _view(qtbot, mock_config_manager)
    _select_rows(view, [0])
    view.result_view.selectAll()
    qtbot.waitUntil(lambda: view.match_model.multi_file and view.match_model.match_count == 6)


def test_clicking_combined_row_previews_and_opens_that_rows_file(qtbot, mock_config_manager):
    view = _view(qtbot, mock_config_manager)
    _select_rows(view, [0, 1, 2])
    qtbot.waitUntil(lambda: view.match_model.multi_file)
    last = view.match_proxy_model.rowCount() - 1
    expected = view.match_model.data(view.match_model.index(last, 0), Qt.ItemDataRole.UserRole)
    opened = []
    view.match_double_clicked.connect(lambda path, line: opened.append((path, line)))
    view._on_match_clicked(view.match_proxy_model.index(last, 0))
    assert view.selected_file_path == expected and view.match_model.current_file_path == expected
    view._emit_match_open(view.match_proxy_model.index(last, 0))
    assert opened and opened[-1][0] == expected


def test_detail_only_normalizes_the_visible_page():
    model = MatchDetailModel()
    big = [[i + 1, f"line {i} sword", 0, 5] for i in range(200_000)]
    started = time.perf_counter()
    model.set_files([("D:/x/big1.txt", big), ("D:/x/big2.txt", big)], "sword", Constants.MODE_NORMAL)
    elapsed = time.perf_counter() - started
    assert model.match_count == 400_000
    # 머리줄(첫 파일 하나)을 더해 101줄, 정리한 결과는 한 페이지 분량뿐입니다.
    assert model.rowCount() == 101 and len(model._cache) <= 100
    assert elapsed < 2.0
    model.go_to_page(model.get_total_pages())
    last = model.get_match(model.rowCount() - 1)
    assert last.source_path == "D:/x/big2.txt" and last.position == "200000"


def test_filter_on_combined_detail_spans_all_files():
    model = MatchDetailModel()
    model.set_files([("D:/x/a.txt", [[1, "alpha", 0, 1], [2, "beta", 0, 1]]),
                     ("D:/x/b.txt", [[3, "beta two", 0, 1]])], "", Constants.MODE_NORMAL)
    model.set_column_filter(0, "beta")
    assert [item.source_path for item in model._filtered_buffer] == ["D:/x/a.txt", "D:/x/b.txt"]


def test_note_column_shows_short_reason_and_tooltip(qtbot, mock_config_manager):
    view = _view(qtbot, mock_config_manager)
    reason = AppStrings.SKIP_REASON_FILE_MATCH_LIMIT.format(2) + "\n" + AppStrings.SKIP_REASON_EXCEL_PARTIAL.format("Sheet3")
    view.set_skipped_files([("D:/d/b.txt", reason), ("D:/d/missing.bin", "unreadable")])
    model = view.result_model
    assert model.headerData(3, Qt.Orientation.Horizontal) == AppStrings.HEADER_NOTE
    notes = {}
    for row in range(model.rowCount()):
        path = model.data(model.index(row, 0), Qt.ItemDataRole.UserRole)[0]
        notes[path] = model.data(model.index(row, 3))
    assert notes["D:/d/a.txt"] == "" and notes["D:/d/c.txt"] == ""
    shown = notes["D:/d/b.txt"]
    assert AppStrings.FILE_NOTE_MATCH_LIMIT.format(2) in shown
    assert AppStrings.FILE_NOTE_EXCEL_PARTIAL.format("Sheet3") in shown and "⚠" in shown
    # 글자는 기본 글자색, ⚠만 강조색입니다(7.1.25).
    assert shown.index("</span>") < shown.index(AppStrings.FILE_NOTE_MATCH_LIMIT.format(2))
    row_b = next(r for r in range(model.rowCount())
                 if model.data(model.index(r, 0), Qt.ItemDataRole.UserRole)[0] == "D:/d/b.txt")
    assert model.data(model.index(row_b, 3), Qt.ItemDataRole.ToolTipRole) == reason


@pytest.mark.parametrize("language", ["ko", "en"])
def test_every_known_reason_has_a_short_label(language, monkeypatch):
    from sf_utils.localization import set_language
    from ui.models import short_file_note

    set_language(language)
    try:
        for template, value in (
            (AppStrings.SKIP_REASON_FILE_MATCH_LIMIT, "10000"),
            (AppStrings.SKIP_REASON_DECODE_PARTIAL, "UTF-8"),
            (AppStrings.SKIP_REASON_JSON_DEPTH_LIMIT, "64"),
            (AppStrings.SKIP_REASON_EXCEL_PARTIAL, "Sheet3"),
        ):
            short = short_file_note(template.format(value))
            assert value in short and len(short) < len(template.format(value))
    finally:
        set_language("ko")


def test_text_export_includes_note(qtbot, mock_config_manager, tmp_path):
    view = _view(qtbot, mock_config_manager)
    view.set_skipped_files([("D:/d/b.txt", AppStrings.SKIP_REASON_FILE_MATCH_LIMIT.format(2))])
    target = tmp_path / "out.txt"
    view._export_to_text(str(target))
    text = target.read_text(encoding="utf-8")
    assert "⚠ " + AppStrings.SKIP_REASON_FILE_MATCH_LIMIT.format(2) in text


def _grouped_model(entries, page_size=100):
    from ui.models import FileGroupHeader

    model = MatchDetailModel()
    model.set_page_size(page_size)
    model.set_files(entries, "", Constants.MODE_NORMAL)
    return model, FileGroupHeader


def test_grouped_view_inserts_one_header_per_file_without_file_column():
    """파일별 묶음 보기: 파일마다 머리줄, 파일 칸 없음, 머리줄은 결과로 치지 않음(7.1.26)."""
    model, header_cls = _grouped_model([
        ("D:/x/a.txt", [[1, "a1", 0, 1], [2, "a2", 0, 1]]),
        ("D:/x/b.txt", [[3, "b3", 0, 1]]),
    ])
    assert model.columnCount() == 1
    kinds = [type(row).__name__ for row in model._data]
    assert kinds == ["FileGroupHeader", "SearchMatchSchema", "SearchMatchSchema", "FileGroupHeader", "SearchMatchSchema"]
    assert model.match_count == 3 and model.get_total_pages() == 1
    assert model.get_match(0) is None and model.first_match_row() == 1
    assert model.flags(model.index(0, 0)) == Qt.ItemFlag.NoItemFlags
    shown = model.data(model.index(0, 0))
    assert "D:/x/a.txt" in shown and AppStrings.GROUP_HEADER_COUNT.format("2") in shown


def test_grouped_view_repeats_header_as_continued_on_next_page():
    rows = [[i, f"line {i}", 0, 1] for i in range(1, 151)]
    model, header_cls = _grouped_model([("D:/x/a.txt", rows), ("D:/x/b.txt", [[1, "b", 0, 1]])], page_size=100)
    first = model._data[0]
    assert isinstance(first, header_cls) and not first.continued
    model.go_to_page(2)
    top = model._data[0]
    assert isinstance(top, header_cls) and top.continued and top.path == "D:/x/a.txt"
    assert AppStrings.GROUP_HEADER_CONTINUED in model.data(model.index(0, 0))
    headers = [row.path for row in model._data if isinstance(row, header_cls)]
    assert headers == ["D:/x/a.txt", "D:/x/b.txt"]


def test_grouped_header_shows_note_and_filter_keeps_only_matching_files():
    model, header_cls = _grouped_model([
        ("D:/x/a.txt", [[1, "alpha", 0, 1]]),
        ("D:/x/b.txt", [[2, "beta", 0, 1]]),
    ])
    model.note_lookup = lambda path: "NOTE-B" if path.endswith("b.txt") else ""
    model.beginResetModel()
    model.endResetModel()
    b_row = next(r for r, row in enumerate(model._data) if isinstance(row, header_cls) and row.path.endswith("b.txt"))
    assert "NOTE-B" in model.data(model.index(b_row, 0))
    model.set_column_filter(0, "beta")
    assert [row.path for row in model._data if isinstance(row, header_cls)] == ["D:/x/b.txt"]


def test_single_file_is_same_in_both_modes():
    model, header_cls = _grouped_model([("D:/x/a.txt", [[1, "a1", 0, 1]])])
    assert not any(isinstance(row, header_cls) for row in model._data) and model.columnCount() == 1


def test_group_rows_are_taller_spanned_and_skipped_by_selection(qtbot, mock_config_manager):
    from ui.widgets import GROUP_HEADER_GAP, GROUP_HEADER_LINE, GROUP_ROLE

    view = _view(qtbot, mock_config_manager)
    _select_rows(view, [0, 1, 2])
    qtbot.waitUntil(lambda: view.match_model.multi_file)
    model = view.match_model
    default = view.match_view.verticalHeader().defaultSectionSize()
    for row in range(model.rowCount()):
        if model.is_group_row(row):
            assert view.match_view.rowHeight(row) == default + GROUP_HEADER_GAP + GROUP_HEADER_LINE
            assert model.data(model.index(row, 0), GROUP_ROLE) == "header"
            assert "📄" in model.data(model.index(row, 0))
        else:
            assert view.match_view.rowHeight(row) == default
            assert model.data(model.index(row, 0), GROUP_ROLE) == "child"
    view.match_view.selectAll()
    selected_rows = {index.row() for index in view.match_view.selectionModel().selectedRows()}
    assert selected_rows and all(not model.is_group_row(row) for row in selected_rows)


def test_single_file_rows_are_not_indented(qtbot, mock_config_manager):
    from ui.widgets import GROUP_ROLE

    view = _view(qtbot, mock_config_manager)
    _select_rows(view, [0])
    qtbot.waitUntil(lambda: view.match_model.match_count > 0)
    assert view.match_model.data(view.match_model.index(0, 0), GROUP_ROLE) is None


def test_page_switch_stays_fast_with_large_grouped_detail(qtbot, mock_config_manager):
    """머리줄 여백·들여쓰기를 넣어도 큰 결과의 페이지 전환은 보는 페이지만 다룹니다(7.1.27)."""
    view = ResultView(QFileIconProvider(), mock_config_manager)
    qtbot.addWidget(view)
    view.resize(1000, 700)
    view.show()
    view.set_search_context("x", Constants.MODE_NORMAL)
    files = [[2000, f"f{i}.txt", "D:/d", f"D:/d/f{i}.txt", [[n, f"x {n}", 0, 1] for n in range(1, 2001)]]
             for i in range(200)]
    view.set_results(files)
    view.result_view.selectAll()
    qtbot.waitUntil(lambda: view.match_model.match_count == 400_000, timeout=5000)
    view.match_model.set_page_size(500)
    started = time.perf_counter()
    for page in (2, 3, 400, 800):
        view.match_model.go_to_page(page)
        qtbot.wait(1)
    per_page = (time.perf_counter() - started) / 4
    assert per_page < 0.5
