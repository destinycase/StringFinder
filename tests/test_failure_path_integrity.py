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


@pytest.mark.parametrize("error_type", [PermissionError, FileNotFoundError])
@pytest.mark.parametrize("read_number", [2, 3])
def test_second_pre_read_io_failure_is_visible_as_skipped(
    qtbot, mock_config_manager, tmp_path, monkeypatch, language, error_type, read_number
):
    path = tmp_path / "utf16.txt"
    path.write_bytes("needle\n".encode("utf-16-le"))
    monkeypatch.setattr(engine.sf_engine, "search_file", lambda *args, **kwargs: [])
    calls = fail_open_number(monkeypatch, path, read_number, error_type("injected read failure"))
    result = engine.search_in_file(str(path), "needle")
    assert len(calls) == read_number
    detail = (AppStrings.SKIP_DETAIL_PERMISSION_DENIED if error_type is PermissionError
              else AppStrings.SKIP_DETAIL_FILE_NOT_FOUND)
    expected = AppStrings.ERROR_IO_DURING_SEARCH.format(detail)
    assert result == (C.STATUS_SKIPPED, expected)
    assert engine.is_supported_skip_reason(expected)
    tab = SearchTab(mock_config_manager)
    qtbot.addWidget(tab)
    tab._on_skipped_found([(str(path), result[1])])
    assert tab.skipped_files_list == [(str(path), expected)]
    assert not tab.result_view_panel.skipped_files_banner.isHidden()


def test_first_pre_read_failure_does_not_prevent_real_native_result(tmp_path, monkeypatch, language):
    path = tmp_path / "readable.txt"
    path.write_text("needle recovered by native reader\n", encoding="utf-8")
    calls = fail_open_number(monkeypatch, path, 1, PermissionError("temporary pre-read failure"))
    with patch.object(engine.logger, "debug") as log:
        result = engine.search_in_file(str(path), "needle")
    assert len(calls) == 1
    assert result and result[0] == str(path) and result[1] == 1
    assert "needle" in result[2][0][1]
    assert any(AppStrings.LOG_SCH_ENCODING_PROBE_FAIL.split("{}")[0] in call.args[0]
               for call in log.call_args_list)


def test_first_detector_exception_preserves_native_recovery(tmp_path, monkeypatch):
    path = tmp_path / "readable.txt"
    path.write_text("needle", encoding="utf-8")
    monkeypatch.setattr(engine, "detect_encoding_quickly", lambda _head: (_ for _ in ()).throw(ValueError("detector")))
    result = engine.search_in_file(str(path), "needle")
    assert result and result[0] == str(path) and result[1] == 1


def test_unexpected_second_pre_read_failure_is_not_normal_no_result(tmp_path, monkeypatch):
    path = tmp_path / "utf16.txt"
    path.write_bytes("needle".encode("utf-16-le"))
    monkeypatch.setattr(engine.sf_engine, "search_file", lambda *args, **kwargs: [])
    fail_open_number(monkeypatch, path, 2, RuntimeError("unexpected read failure"))
    result = engine.search_in_file(str(path), "needle")
    assert result and result[0] == C.STATUS_SKIPPED
    assert result[1].startswith(AppStrings.ERROR_UNEXPECTED_FILE.split("{}")[0])


def test_compatibility_pre_read_normal_ascii_and_utf16_policy_is_preserved(tmp_path, monkeypatch):
    ascii_path = tmp_path / "ascii.txt"
    ascii_path.write_text("ordinary text", encoding="utf-8")
    utf16_path = tmp_path / "utf16.txt"
    utf16_path.write_bytes("needle".encode("utf-16-le"))
    monkeypatch.setattr(engine.sf_engine, "search_file", lambda *args, **kwargs: [])
    assert engine.search_in_file(str(ascii_path), "absent") is None
    # Reach the second pre-check on a non-UTF-8 classification without a BOM/NUL.
    real_detector = engine.detect_encoding_quickly
    monkeypatch.setattr(engine, "detect_encoding_quickly", lambda _head: "utf-16-le")
    assert engine.search_in_file(str(ascii_path), "absent") is None
    monkeypatch.setattr(engine, "detect_encoding_quickly", real_detector)
    result = engine.search_in_file(str(utf16_path), "needle")
    assert result and result[1] == 1
    assert engine.search_in_file(str(utf16_path), "absent") is None


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
