"""읽기 실패와 비정상 검색 종료를 정상 무결과·완료와 구분합니다."""
import builtins
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from core import search_engine as engine
from core.worker import SearchWorker
from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants as C
from sf_utils.localization import get_language, set_language
from ui.search_tab import SearchTab


@pytest.fixture(params=["ko", "en"])
def language(request):
    old = get_language()
    set_language(request.param)
    yield request.param
    set_language(old)


def fail_open_number(monkeypatch, path, number, error):
    real_open = builtins.open
    calls = []

    def opened(file, mode="r", *args, **kwargs):
        if str(file) == str(path) and mode == "rb":
            calls.append(file)
            if len(calls) == number:
                raise error
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", opened)
    return calls


@pytest.mark.parametrize("file_list", [False, True])
def test_native_error_after_delivery_keeps_results_and_marks_ui_failed(
    qtbot, mock_config_manager, tmp_path, monkeypatch, language, file_list
):
    path = tmp_path / "partial.txt"
    tab = SearchTab(mock_config_manager)
    qtbot.addWidget(tab)
    tab.search_state = C.SearchState.SEARCHING
    tab.scan_start_time = time.time()
    tab.search_panel.search_combo.set_current_text("needle")
    worker = SearchWorker({"search_paths": [str(tmp_path)], "search_string": "needle",
                           "file_list": [(str(path), 6)] if file_list else []})
    worker.signals.results_found.connect(tab._on_results_found)
    worker.signals.error.connect(tab._on_search_error)
    worker.signals.search_finished.connect(tab._on_search_finished)
    errors = []
    worker.signals.error.connect(errors.append)

    def failed_search(*args, **kwargs):
        kwargs["results_callback"]([(str(path), [(1, "needle", 0, 6)])])
        raise RuntimeError("results dispatcher terminated unexpectedly; monitor thread terminated unexpectedly")

    monkeypatch.setattr(engine, "search_files_list_fast" if file_list else "search_directory_fast", failed_search)
    worker.run()
    tab._on_worker_finished()
    assert errors and "results dispatcher terminated unexpectedly" in errors[0]
    qtbot.waitUntil(lambda: tab.result_view_panel.result_model.rowCount() == 1)
    assert tab.total_files == 1 and tab.total_matches == 1
    assert tab._search_failed
    assert tab.search_state == C.SearchState.IDLE
    assert tab.result_view_panel.summary_label.text().startswith(AppStrings.SUMMARY_PREFIX_FAILED)
    assert Path(tab.result_view_panel.result_model.get_item(0)[0]) == path


def test_single_file_native_runtime_error_remains_file_level_skip(tmp_path, monkeypatch, language):
    path = tmp_path / "single.txt"
    path.write_text("needle", encoding="utf-8")
    with patch.object(engine.sf_engine, "search_file", side_effect=RuntimeError("monitor thread terminated unexpectedly")):
        result = engine.search_in_file(str(path), "needle")
    assert result and result[0] == C.STATUS_SKIPPED
