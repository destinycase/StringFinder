"""결과 목록·상세 표시 동작: 필터·탭 이름·선택 유지·정렬·강조·로그 분리."""
import os
import sys

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileIconProvider

from sf_utils.constants import Constants as C
from ui.result_view import ResultView

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


def _view(qtbot, mock_config_manager):
    view = ResultView(QFileIconProvider(), mock_config_manager)
    qtbot.addWidget(view)
    return view


def test_hidden_match_filter_is_cleared_when_mode_changes(qtbot, mock_config_manager):
    view = _view(qtbot, mock_config_manager)
    view.update_match_filter_visibility(C.MODE_JSON + " (부분 일치)")
    view.match_filter_2_edit.setText("값필터")
    assert view.match_filter_2_edit.text() == "값필터"
    # 일반 검색으로 바꾸면 값 입력란이 숨겨지므로 그 글자가 남아 상세 목록을 거르면 안 됩니다.
    view.update_match_filter_visibility(C.MODE_NORMAL)
    assert view.match_filter_2_edit.isHidden()
    assert view.match_filter_2_edit.text() == ""
    view.match_model.set_matches("a.txt", [(1, "needle one", 0, 6), (2, "needle two", 0, 6)], "needle", C.MODE_NORMAL)
    assert view.match_model.rowCount() == 2


def test_new_tab_titles_never_collide(qtbot, mock_config_manager):
    from ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    while window.tab_widget.count() < 3:
        window.add_new_tab()
    window.tab_widget.removeTab(0)
    window.add_new_tab()
    titles = [window.tab_widget.tabText(i) for i in range(window.tab_widget.count())]
    assert len(titles) == len(set(titles)), titles
    window.close()


def test_header_sort_orders_all_pages(qtbot, mock_config_manager):
    view = _view(qtbot, mock_config_manager)
    view.result_model.set_page_size(1000) if hasattr(view.result_model, "set_page_size") else None
    view.result_model.add_results([(f"d/n{i:05d}.txt", 1, [(1, "x", None, None)]) for i in range(2500)])
    with qtbot.waitSignal(view.result_model.sort_completed, timeout=5000):
        view.proxy_model.sort(1, Qt.SortOrder.DescendingOrder)
    first = view.result_model.get_item(view.proxy_model.mapToSource(view.proxy_model.index(0, 0)).row())
    assert first[0] == "d/n02499.txt"


def test_selection_survives_incoming_results(qtbot, mock_config_manager):
    view = _view(qtbot, mock_config_manager)
    view.show()
    view.result_model.add_results([(f"d/f{i:03d}.txt", 1, [(1, "x", None, None)]) for i in range(300)])
    target = view.proxy_model.index(250, 0)
    view.result_view.setCurrentIndex(target)
    chosen = view.result_model.get_item(view.proxy_model.mapToSource(target).row())[0]
    view.result_model.add_results([("d/zzz_new.txt", 1, [(1, "x", None, None)])])
    current = view.result_view.currentIndex()
    assert current.isValid()
    assert view.result_model.get_item(view.proxy_model.mapToSource(current).row())[0] == chosen


def test_one_click_builds_details_once(qtbot, mock_config_manager, monkeypatch):
    view = _view(qtbot, mock_config_manager)
    view.show()
    view.result_model.add_results([("d/a.txt", 1, [(1, "x", None, None)]), ("d/b.txt", 1, [(1, "y", None, None)])])
    calls = []
    original = view.match_model.set_matches
    monkeypatch.setattr(view.match_model, "set_matches", lambda *a, **k: (calls.append(a[0]), original(*a, **k)))
    rect = view.result_view.visualRect(view.proxy_model.index(1, 1))
    qtbot.mouseClick(view.result_view.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert len(calls) == 1


def test_select_all_notifies_once(qtbot):
    from ui.panels import ExtensionFilterPanel, FilenameFilterPanel

    for panel, add in ((ExtensionFilterPanel(), "add_extension"), (FilenameFilterPanel(), "add_filename")):
        qtbot.addWidget(panel)
        panel.restore_state({})
        for value in ("aa", "bb", "cc"):
            getattr(panel, add)(value)
        changes = []
        panel.filter_changed.connect(lambda: changes.append(1))
        panel.toggle_all(False)
        assert len(changes) == 1


def test_logs_go_only_to_the_searching_tab(qtbot, mock_config_manager, monkeypatch):
    from ui.search_tab import SearchTab

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    first, second = SearchTab(mock_config_manager), SearchTab(mock_config_manager)
    qtbot.addWidget(first)
    qtbot.addWidget(second)
    first.search_state = C.SearchState.SCANNING
    before = len(second._pending_logs)
    first._on_log_message("INFO", "first tab search log")
    second._on_log_message("INFO", "first tab search log")
    assert len(second._pending_logs) == before
    assert first._pending_logs[-1][1] == "first tab search log"
    first.search_state = C.SearchState.IDLE
    second._on_log_message("INFO", "common log")
    assert second._pending_logs[-1][1] == "common log"
    first.cleanup()
    second.cleanup()


@pytest.mark.parametrize("text,query,marked", [
    ("주소 Straße 끝", "strasse", "Straße"),
    ("x " + __import__("unicodedata").normalize("NFD", "목표") + " y", "목표",
     __import__("unicodedata").normalize("NFD", "목표")),
])
def test_highlight_follows_the_search_comparison(text, query, marked):
    from ui.models import MatchDetailModel

    model = MatchDetailModel()
    model.set_matches("a.txt", [(1, text, None, None)], query, C.MODE_NORMAL)
    html = model._render_highlighted(text)
    assert f"font-weight: bold;'>{marked}</span>" in html


@pytest.mark.parametrize("position", [-1, -2])
def test_excel_notice_rows_in_normal_search(position):
    from ui.models import MatchDetailModel

    model = MatchDetailModel()
    model.set_matches("C:/t/a.xlsx", [(3, "Sheet1\tA3\tabc", None, None), (position, "LIMIT-MSG", None, None)],
                      "abc", C.MODE_NORMAL)
    shown = model.data(model.index(1, 0))
    assert shown.count("LIMIT-MSG") == 1 and "-1!" not in shown and "-2!" not in shown and " | " not in shown
    assert model.data(model.index(1, 0), Qt.ItemDataRole.EditRole) == "LIMIT-MSG"
    first = model.data(model.index(0, 0))
    assert first.startswith("<html>Sheet1!A3 | ") and "abc</span>" in first


def test_header_sort_clicked_during_search_is_applied_at_finish(qtbot, mock_config_manager):
    view = _view(qtbot, mock_config_manager)
    model = view.result_model
    model.add_results([(f"d/n{i:03d}.txt", 300 - i, [(1, "x", None, None)]) for i in range(300)])
    # 검색 중 결과가 더 들어와 정렬이 버려지는 상황을 흉내 냅니다.
    view.proxy_model.sort(1, Qt.SortOrder.DescendingOrder)
    model.add_results([("d/zzz.txt", 1, [(1, "x", None, None)])])
    qtbot.wait(200)
    with qtbot.waitSignal(model.sort_completed, timeout=5000):
        model.sort_results()
    assert model.get_all_results()[0][1] == "zzz.txt"


def test_default_sort_breaks_ties_by_path_without_header_clicks(qtbot, mock_config_manager):
    view = _view(qtbot, mock_config_manager)
    model = view.result_model
    rows = [(f"d/f{i:04d}.txt", 1 + (i % 2), [(1, "x", None, None)]) for i in range(600)]
    model.add_results(list(reversed(rows)))
    with qtbot.waitSignal(model.sort_completed, timeout=5000):
        model.sort_results()
    paths = [row[3] for row in model.get_all_results()]
    expected = sorted((p for p, c, _m in rows if c == 2)) + sorted((p for p, c, _m in rows if c == 1))
    assert paths == expected


def test_name_sort_orders_same_names_by_path(qtbot, mock_config_manager):
    view = _view(qtbot, mock_config_manager)
    model = view.result_model
    model.add_results([(f"d{i:03d}/same.txt", 1, [(1, "x", None, None)]) for i in reversed(range(200))])
    for order in (Qt.SortOrder.AscendingOrder, Qt.SortOrder.DescendingOrder):
        with qtbot.waitSignal(model.sort_completed, timeout=5000):
            view.proxy_model.sort(1, order)
        paths = [row[3] for row in model.get_all_results()]
        assert paths == sorted(paths)
