"""Query-length contract across GUI, restored sessions, workers and engines."""
from unittest.mock import patch

import pytest

from core import search_engine as se
from core.search_query import validate_search_query
from core.worker import SearchWorker
from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants as C
from sf_utils.localization import get_language, set_language
from ui.panels import SearchOptionsPanel
from ui.search_tab import SearchTab


@pytest.mark.parametrize("character", ["a", "가", "😀"])
def test_character_count_boundary(character):
    validate_search_query(character * 1000)
    with pytest.raises(ValueError) as error:
        validate_search_query(character * 1001)
    assert str(error.value) == AppStrings.ERROR_SEARCH_QUERY_LENGTH.format(1000, 1001)


@pytest.mark.parametrize("entry", ["file", "json", "xml", "excel", "batch", "directory", "files", "find"])
def test_python_entry_points_reject_before_any_io(entry):
    calls = {
        "file": lambda q: se.search_in_file("missing.txt", q),
        "json": lambda q: se.search_in_json_special("missing.json", q),
        "xml": lambda q: se.search_in_xml_special("missing.xml", q),
        "excel": lambda q: se.search_in_excel_special("missing.xlsx", q),
        "batch": lambda q: se.search_in_files_batch([], q),
        "directory": lambda q: se.search_directory_fast([], q),
        "files": lambda q: se.search_files_list_fast([], q),
        "find": lambda q: se.find_files_with_keyword_fast([], q),
    }
    with patch.object(se.os.path, "getsize", side_effect=AssertionError("unexpected I/O")):
        with pytest.raises(ValueError, match="1,000"):
            calls[entry]("a" * 1001)


def test_input_preserves_oversized_text_and_clears_notice(qtbot):
    panel = SearchOptionsPanel()
    qtbot.addWidget(panel)
    text = "x" * 1001
    panel.search_combo.set_current_text(text)
    assert panel.get_search_text() == text
    assert panel.query_length_notice.text() == AppStrings.ERROR_SEARCH_QUERY_LENGTH.format(1000, 1001)
    assert not panel.query_length_notice.isHidden()
    panel.search_combo.set_current_text("x" * 1000)
    assert panel.query_length_notice.isHidden()


def test_restored_long_input_blocks_search_without_clearing_results(qtbot, mock_config_manager):
    tab = SearchTab(mock_config_manager)
    qtbot.addWidget(tab)
    tab.search_panel.search_combo.set_current_text("x" * 1001)
    with patch("ui.search_tab.QMessageBox.warning") as warning, \
         patch.object(tab, "_setup_search_worker") as setup, \
         patch.object(tab.result_view_panel, "clear") as clear:
        tab.start_search()
    warning.assert_called_once_with(tab, AppStrings.ERROR_TITLE,
        AppStrings.ERROR_SEARCH_QUERY_LENGTH.format(1000, 1001))
    setup.assert_not_called()
    clear.assert_not_called()
    assert tab.search_state == C.SearchState.IDLE


@pytest.mark.parametrize("complex_search", [False, True])
def test_worker_blocks_before_search_or_query_logging(complex_search):
    worker = SearchWorker({C.PAYLOAD_SEARCH_STRING: "a" * 1001,
                           C.PAYLOAD_USE_COMPLEX_SEARCH: complex_search})
    errors, finished = [], []
    worker.signals.error.connect(errors.append)
    worker.signals.finished.connect(lambda: finished.append(True))
    with patch.object(worker, "_run_rust_search") as rust, \
         patch.object(worker, "_run_python_search") as python, \
         patch("core.worker.logger.info") as log:
        worker.run()
    assert errors == [AppStrings.ERROR_SEARCH_QUERY_LENGTH.format(1000, 1001)]
    assert finished == [True]
    rust.assert_not_called()
    python.assert_not_called()
    log.assert_not_called()


@pytest.mark.parametrize("language", ["ko", "en"])
def test_localized_length_feedback(language, qtbot):
    previous = get_language()
    try:
        set_language(language)
        panel = SearchOptionsPanel()
        qtbot.addWidget(panel)
        panel.search_combo.set_current_text("a" * 1001)
        assert panel.query_length_notice.text() == AppStrings.ERROR_SEARCH_QUERY_LENGTH.format(1000, 1001)
        if language == "en":
            assert "Shorten" in panel.query_length_notice.text()
    finally:
        set_language(previous)


@pytest.mark.parametrize("entry", ["file", "directory", "files", "find"])
def test_native_entry_points_enforce_same_limit(entry, tmp_path):
    if not se.HAS_RUST_ENGINE:
        pytest.skip("Rust engine unavailable")
    assert se.sf_engine.MAX_SEARCH_QUERY_LENGTH == C.MAX_SEARCH_QUERY_LENGTH
    calls = {
        "file": lambda q: se.sf_engine.search_file(str(tmp_path / "missing"), q),
        "directory": lambda q: se.sf_engine.search_dir([], q),
        "files": lambda q: se.sf_engine.search_files_list([], q),
        "find": lambda q: se.sf_engine.find_files_with_keyword([], q),
    }
    with pytest.raises(ValueError, match="1000"):
        calls[entry]("😀" * 1001)


@pytest.mark.parametrize("complex_search", [False, True])
def test_limit_length_query_is_searched_without_truncation(tmp_path, complex_search):
    p = tmp_path / "valid.txt"
    query = "가" * 1000
    p.write_text(query + "\n", encoding="utf-8")
    result = se.search_in_file(str(p), query, use_complex_search=complex_search)
    assert result[1] == 1
