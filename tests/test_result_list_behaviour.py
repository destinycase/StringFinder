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
    original = view.match_model.set_files
    monkeypatch.setattr(view.match_model, "set_files", lambda *a, **k: (calls.append(a[0]), original(*a, **k)))
    rect = view.result_view.visualRect(view.proxy_model.index(1, 1))
    qtbot.mouseClick(view.result_view.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    qtbot.wait(50)  # 선택 변경 신호로 예약된 갱신이 있어도 같은 파일이면 다시 만들지 않습니다.
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


def test_log_window_shows_whole_program_log_in_every_tab(qtbot, mock_config_manager):
    """로그 창은 프로그램 시작 후의 전체 로그를 모든 탭에서 같이 보여 주고, 검색을 시작해도 비우지 않습니다(7.5.3)."""
    from sf_utils.logger import get_log_history, logger
    from ui.search_tab import SearchTab

    logger.info("log before tab was opened")
    first = SearchTab(mock_config_manager)
    qtbot.addWidget(first)
    # 탭을 나중에 열어도 그 전 로그가 보입니다.
    assert "log before tab was opened" in first.logs_output.toPlainText()
    second = SearchTab(mock_config_manager)
    qtbot.addWidget(second)
    first.search_state = C.SearchState.SCANNING
    logger.info("first tab search log")
    # 검색 중인 탭이 아닌 탭에도 같은 로그가 보입니다.
    for tab in (first, second):
        assert "first tab search log" in tab.logs_output.toPlainText()
    first.search_state = C.SearchState.IDLE
    assert any("first tab search log" in message for _, message in get_log_history())
    first._clear_logs()
    assert get_log_history() == []
    assert first.logs_output.toPlainText() == "" and second.logs_output.toPlainText() == ""
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
def test_notice_rows_are_not_shown_in_detail_list(position):
    """파일당 상한·부분 검색 안내는 파일 목록의 기타 칸에만 보이고 상세 목록에는 없습니다(7.1.24)."""
    from ui.models import MatchDetailModel

    model = MatchDetailModel()
    model.set_matches("C:/t/a.xlsx", [(3, "Sheet1	A3	abc", None, None), (position, "LIMIT-MSG", None, None)],
                      "abc", C.MODE_NORMAL)
    assert model.rowCount() == 1 and model.match_count == 1
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


def test_toggling_log_level_does_not_duplicate_pending_line(qtbot, mock_config_manager, monkeypatch):
    """로그를 모아 두는 0.1초 안에 수준 체크를 바꿔도 같은 줄이 두 번 보이지 않습니다(7.5.6, 검수 3.2)."""
    from sf_utils.logger import logger
    from ui.search_tab import SearchTab

    tab = SearchTab(mock_config_manager)
    qtbot.addWidget(tab)
    # 실제 프로그램처럼 바로 붙이지 않고 모아 둡니다.
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    logger.info("pending line for level toggle")
    debug_box = tab._log_level_checkboxes["DEBUG"]
    debug_box.setChecked(False)
    debug_box.setChecked(True)
    tab._flush_pending_logs()
    assert tab.logs_output.toPlainText().count("pending line for level toggle") == 1
    # 수준 거르기: INFO를 끄면 그 줄이 사라지고, 켜면 다시 한 번 보입니다.
    tab._log_level_checkboxes["INFO"].setChecked(False)
    assert "pending line for level toggle" not in tab.logs_output.toPlainText()
    tab._log_level_checkboxes["INFO"].setChecked(True)
    assert tab.logs_output.toPlainText().count("pending line for level toggle") == 1
    tab.cleanup()


def test_clear_logs_after_closing_another_tab(qtbot, mock_config_manager):
    """탭을 닫은 뒤 다른 탭에서 로그를 지워도 오류 없이 남은 탭이 비워집니다(7.5.7)."""
    from sf_utils.logger import logger
    from ui.search_tab import SearchTab

    first, second = SearchTab(mock_config_manager), SearchTab(mock_config_manager)
    qtbot.addWidget(second)
    first.cleanup()
    assert first not in SearchTab._instances
    first.deleteLater()
    qtbot.wait(20)
    logger.info("line after closing a tab")
    assert "line after closing a tab" in second.logs_output.toPlainText()
    second._clear_logs()
    assert second.logs_output.toPlainText() == ""
    second.cleanup()


def test_log_window_has_margins_and_larger_default_size(qtbot, mock_config_manager):
    """로그 창: 창 안쪽 여백, 로그 글자 둘레 여백, 넉넉한 처음 크기(7.6.4)."""
    from ui.search_tab import SearchTab

    tab = SearchTab(mock_config_manager)
    qtbot.addWidget(tab)
    margins = tab.log_dialog.layout().contentsMargins()
    assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == SearchTab.LOG_DIALOG_MARGINS
    assert min(SearchTab.LOG_DIALOG_MARGINS) >= 10
    assert tab.logs_output.document().documentMargin() == SearchTab.LOG_TEXT_MARGIN
    assert (tab.log_dialog.width(), tab.log_dialog.height()) == SearchTab.LOG_DIALOG_DEFAULT_SIZE
    assert tab.log_dialog.width() > 760 and tab.log_dialog.height() > 420
    tab.cleanup()


def test_filter_groups_have_space_above_titles(qtbot, mock_config_manager):
    """확장자·파일명 그룹 제목이 도크 제목과 구분선에 붙지 않게 위를 띄웁니다(7.6.9)."""
    from ui.search_tab import SearchTab

    tab = SearchTab(mock_config_manager)
    qtbot.addWidget(tab)
    top = tab.filter_container.layout().contentsMargins().top()
    assert top == SearchTab.FILTER_CONTAINER_MARGINS[1] >= 6
    assert tab.filter_splitter.handleWidth() == SearchTab.FILTER_SPLITTER_HANDLE_WIDTH >= 10
    tab.cleanup()
