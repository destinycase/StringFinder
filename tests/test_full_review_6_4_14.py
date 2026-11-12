"""6.4.14 외부 전체 검수 지적 사항의 회귀 시험입니다.

각 시험 이름 앞의 기호(U-1 등)는 검수 보고서의 항목 번호입니다.
"""
import json
import os
import sys

import openpyxl
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileIconProvider

from core.worker import SearchWorker
from sf_utils.constants import Constants as C
from ui.result_view import ResultView

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
from rust_engine import sf_engine  # noqa: E402


def _view(qtbot, mock_config_manager):
    view = ResultView(QFileIconProvider(), mock_config_manager)
    qtbot.addWidget(view)
    return view


def test_u1_hidden_match_filter_is_cleared_when_mode_changes(qtbot, mock_config_manager):
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


def test_u2_new_tab_titles_never_collide(qtbot, mock_config_manager):
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


def test_u4_total_limit_is_a_stop_not_an_error():
    worker = SearchWorker({"file_list": [], "search_string": "needle"})
    worker.search_settings_snapshot[C.CONFIG_KEY_MAX_TOTAL_MATCHES] = 1000
    errors, limits = [], []
    worker.signals.error.connect(errors.append)
    worker.signals.total_match_limit_reached.connect(limits.append)
    worker._stop_for_total_match_limit()
    assert limits == [1000]
    assert errors == []
    assert worker.stop_event.is_set()


def test_e4_exactly_reaching_the_limit_is_not_reported_as_reached():
    worker = SearchWorker({"file_list": [], "search_string": "needle"})
    worker._total_matches_accumulated = 7
    worker.search_settings_snapshot[C.CONFIG_KEY_MAX_TOTAL_MATCHES] = 10
    accepted, count, reached = worker._fit_results_to_total_limit([("a.txt", 3, [(1, "a"), (2, "b"), (3, "c")])])
    assert count == 3 and reached is False
    worker._total_matches_accumulated = 10
    _accepted, _count, reached = worker._fit_results_to_total_limit([("b.txt", 1, [(1, "d")])])
    assert reached is True


class _FakeModel:
    def __init__(self, rows):
        self._rows = rows

    def get_all_results(self):
        return self._rows


def _export(tmp_path, mode, rows):
    output = tmp_path / "out.xlsx"
    fake = type("FakeView", (), {"search_mode": mode, "result_model": _FakeModel(rows)})()
    ResultView._export_to_excel(fake, str(output))
    return openpyxl.load_workbook(output).worksheets[1]


def test_u5_u10_export_survives_control_characters_and_skips_notice_rows(tmp_path):
    matches = [
        (1, "\x1b[31mERROR\x1b[0m needle", 0, 6),
        (-1, "파일당 상한 안내", None, None),
        (-2, "부분 검색 안내", None, None),
        (3, "Sheet1\tB2\tneedle 값", None, None),
    ]
    sheet = _export(tmp_path, C.MODE_NORMAL, [(2, "a.log", "dir", "dir/a.log", matches)])
    rows = [[cell.value for cell in row] for row in sheet.iter_rows(min_row=2)]
    assert rows == [["dir/a.log", "1", "[31mERROR[0m needle"], ["dir/a.log", "3", "Sheet1!B2 | needle 값"]]


def test_u10_value_mode_export_writes_empty_cells_not_none(tmp_path):
    sheet = _export(tmp_path, C.MODE_JSON + " (부분 일치)", [(1, "a.json", "d", "d/a.json", [("", "k", None, None, None)])])
    row = [cell.value for cell in next(sheet.iter_rows(min_row=2))]
    # 빈 값은 빈 칸으로 저장됩니다(예전에는 "None"이라는 글자가 들어감). openpyxl은 빈 칸을 None으로 읽습니다.
    assert row == ["d/a.json", None, "k", None]


def test_u9_cleared_data_is_not_resaved_on_close(qtbot, mock_config_manager):
    from ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    window._save_tab(0)
    sessions = mock_config_manager.sessions_dir
    assert os.listdir(sessions)
    mock_config_manager.clear_all_data()
    assert os.listdir(sessions) == []
    window.close()
    assert os.listdir(sessions) == []


def test_u6_header_sort_orders_all_pages(qtbot, mock_config_manager):
    view = _view(qtbot, mock_config_manager)
    view.result_model.set_page_size(1000) if hasattr(view.result_model, "set_page_size") else None
    view.result_model.add_results([(f"d/n{i:05d}.txt", 1, [(1, "x", None, None)]) for i in range(2500)])
    with qtbot.waitSignal(view.result_model.sort_completed, timeout=5000):
        view.proxy_model.sort(1, Qt.SortOrder.DescendingOrder)
    first = view.result_model.get_item(view.proxy_model.mapToSource(view.proxy_model.index(0, 0)).row())
    assert first[0] == "d/n02499.txt"


def test_u3_selection_survives_incoming_results(qtbot, mock_config_manager):
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


def test_s4_one_click_builds_details_once(qtbot, mock_config_manager, monkeypatch):
    view = _view(qtbot, mock_config_manager)
    view.show()
    view.result_model.add_results([("d/a.txt", 1, [(1, "x", None, None)]), ("d/b.txt", 1, [(1, "y", None, None)])])
    calls = []
    original = view.match_model.set_matches
    monkeypatch.setattr(view.match_model, "set_matches", lambda *a, **k: (calls.append(a[0]), original(*a, **k)))
    rect = view.result_view.visualRect(view.proxy_model.index(1, 1))
    qtbot.mouseClick(view.result_view.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert len(calls) == 1


def test_s5_select_all_notifies_once(qtbot):
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


def test_e1_unreadable_settings_are_backed_up(mock_config_manager):
    from sf_utils.config_manager import ConfigManager

    path = mock_config_manager.config_path
    mock_config_manager.stop()
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("{ broken json")
    ConfigManager._instance = None
    reloaded = ConfigManager()
    try:
        backups = [name for name in os.listdir(os.path.dirname(path)) if ".corrupt-" in name]
        assert backups
        with open(os.path.join(os.path.dirname(path), backups[0]), encoding="utf-8") as handle:
            assert handle.read() == "{ broken json"
    finally:
        reloaded.stop()


def test_f1_exact_match_ignores_spaces_around_the_query(tmp_path):
    path = tmp_path / "a.json"
    path.write_text(json.dumps({"k": "needle"}), encoding="utf-8")
    found = sf_engine.search_file(str(path), "  needle ", C.RUST_MODE_JSON | C.RUST_MODE_EXACT)
    assert [m.line for m in found if m.kind == "match"]
    assert not sf_engine.search_file(str(path), "   ", C.RUST_MODE_JSON | C.RUST_MODE_EXACT)


def test_u8_logs_go_only_to_the_searching_tab(qtbot, mock_config_manager, monkeypatch):
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


def test_u11_batch_editor_launch_avoids_percent_and_caret_paths(tmp_path, monkeypatch):
    from sf_utils import file_helper

    target = tmp_path / "a%PATH%b^.txt"
    target.write_text("x", encoding="utf-8")
    launched, fallback = [], []
    monkeypatch.setattr(file_helper.shutil, "which", lambda _name: str(tmp_path / "code.cmd"))
    monkeypatch.setattr(file_helper.subprocess, "Popen", lambda command, **_k: launched.append(command))
    monkeypatch.setattr(file_helper, "open_file", lambda path: fallback.append(path) or True)
    file_helper.open_in_external_editor(str(target), 3, {C.CONFIG_KEY_EDITOR_TYPE: "vscode"})
    assert launched == []
    assert fallback == [str(target)]


@pytest.mark.parametrize("query", ["needle", "한글"])
def test_s1_binary_files_are_excluded_quickly_for_any_query(tmp_path, query):
    path = tmp_path / "big.bin"
    with open(path, "wb") as handle:
        handle.write(b"\x00\xff" * 512)
        handle.write(bytes(range(256)) * 4096 * 8)
    assert sf_engine.search_file(str(path), query, C.RUST_MODE_EXCLUDE_BINARY) == []


@pytest.mark.parametrize("text,query,marked", [
    ("주소 Straße 끝", "strasse", "Straße"),
    ("x " + __import__("unicodedata").normalize("NFD", "목표") + " y", "목표",
     __import__("unicodedata").normalize("NFD", "목표")),
])
def test_f3_highlight_follows_the_search_comparison(text, query, marked):
    from ui.models import MatchDetailModel

    model = MatchDetailModel()
    model.set_matches("a.txt", [(1, text, None, None)], query, C.MODE_NORMAL)
    html = model._render_highlighted(text)
    assert f"font-weight: bold;'>{marked}</span>" in html


def test_s6_engine_stops_producing_after_the_total_limit(tmp_path):
    from core import search_engine as se

    for index in range(300):
        (tmp_path / f"f{index:03d}.txt").write_text("e\n" * 50, encoding="utf-8")
    batches = []
    result = se.search_directory_fast([str(tmp_path)], "e", ["txt"], results_callback=batches.append,
                                      batch_size=1, max_total_matches=100)
    delivered = sum(len(rows) for batch in batches for _path, rows in batch) + sum(
        count for _path, count, _rows in result["results"])
    # 상한(100)을 넘긴 뒤에는 엔진이 멈추므로 300파일 × 50건을 다 만들지 않습니다.
    assert 100 < delivered < 300 * 50


def test_s3_session_title_is_read_from_the_head(mock_config_manager, monkeypatch):
    mock_config_manager.save_session("탭 이름", {"results": [["a", 1, []]] * 10, "title": "탭 이름"})
    path = mock_config_manager._find_existing_session_file("탭 이름")
    with open(path, encoding="utf-8") as handle:
        assert handle.read(40).startswith('{"__sf_session_name":"탭 이름"')
    monkeypatch.setattr(mock_config_manager, "_read_session_title_full",
                        lambda _p: (_ for _ in ()).throw(AssertionError("full parse")))
    mock_config_manager.__dict__.pop("_session_title_cache", None)
    assert mock_config_manager.get_all_session_names() == ["탭 이름"]
    assert mock_config_manager.load_session("탭 이름")["title"] == "탭 이름"


def test_s3_old_session_files_are_parsed_once(mock_config_manager, monkeypatch):
    path = os.path.join(mock_config_manager.sessions_dir, "old.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"results": [], "title": "old", "__sf_session_name": "old"}, handle)
    calls = []
    original = mock_config_manager._read_session_title_full
    monkeypatch.setattr(mock_config_manager, "_read_session_title_full", lambda p: calls.append(p) or original(p))
    for _ in range(3):
        assert mock_config_manager.get_all_session_names() == ["old"]
    assert mock_config_manager.load_session("old") is not None
    assert len(calls) == 1


def test_f4_json_keys_with_slashes_stay_distinguishable(tmp_path):
    from core import search_engine as se

    (tmp_path / "a.json").write_text(json.dumps(
        {"a/b": "needle1", "a": {"b": "needle2"}, "/lead": "needle3", "t~x": "needle4"}), encoding="utf-8")
    result = se.search_directory_fast([str(tmp_path)], "needle", ["json"], special_mode=C.MODE_JSON + " (부분 일치)")
    keys = sorted(row[1] for _path, _count, rows in result["results"] for row in rows)
    assert keys == ["/lead", "a.b", "a/b", "t~x"]


def test_s3_search_finish_saves_in_background_and_close_waits(qtbot, mock_config_manager, monkeypatch):
    import threading
    from ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    tab = window.tab_widget.widget(0)
    release = threading.Event()
    threads = []
    original = mock_config_manager._save_session_locked

    def slow_save(name, data):
        threads.append(threading.current_thread().name)
        release.wait(5)
        return original(name, data)

    monkeypatch.setattr(mock_config_manager, "_save_session_locked", slow_save)
    window._on_search_finished_in_tab(tab)  # 저장이 끝나지 않아도 바로 돌아옵니다.
    assert not release.is_set()
    release.set()
    mock_config_manager.wait_for_session_saves(5)
    assert threads and threads[0] != threading.main_thread().name
    assert os.listdir(mock_config_manager.sessions_dir)
    window.close()


def test_s3_clear_all_data_waits_for_pending_saves(mock_config_manager, monkeypatch):
    import threading
    import time

    started = threading.Event()
    original = mock_config_manager._save_session_locked

    def slow_save(name, data):
        started.set()
        time.sleep(0.3)
        return original(name, data)

    monkeypatch.setattr(mock_config_manager, "_save_session_locked", slow_save)
    mock_config_manager.save_session_async("late", {"results": []})
    started.wait(5)
    mock_config_manager.clear_all_data()
    # 늦게 끝난 저장이 지운 세션을 되살리지 않습니다.
    assert os.listdir(mock_config_manager.sessions_dir) == []
