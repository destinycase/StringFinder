"""Native failure completion must retain delivered data, not report success."""

import time

import pytest

from core.worker import SearchWorker
from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants
from sf_utils.localization import get_language, set_language
from ui.search_tab import SearchTab


@pytest.mark.parametrize("language", ["ko", "en"])
@pytest.mark.parametrize("file_list", [False, True])
@pytest.mark.parametrize("partial", [False, True])
def test_native_failure_completion(
    qtbot, mock_config_manager, monkeypatch, caplog, language, file_list, partial
):
    previous_language = get_language()
    set_language(language)
    try:
        tab = SearchTab(mock_config_manager)
        qtbot.addWidget(tab)
        tab.scan_start_time = time.time()
        tab.search_state = Constants.SearchState.SEARCHING
        params = {"search_paths": ["unused"], "search_string": "needle"}
        if file_list:
            params["file_list"] = [("partial.txt", 100)]
        worker = SearchWorker(params)
        tab.worker = worker
        completions = []
        skipped = []
        worker.signals.error.connect(tab._on_search_error)
        worker.signals.finished.connect(tab._on_worker_finished)
        worker.signals.search_finished.connect(tab._on_search_finished)
        worker.signals.results_found.connect(tab._on_results_found)
        worker.signals.skipped_found.connect(tab._on_skipped_found)
        worker.signals.search_finished.connect(lambda *values: completions.append(values))
        worker.signals.skipped_found.connect(lambda batch: skipped.extend(batch))

        def fail(*args, **kwargs):
            if partial:
                kwargs["results_callback"]([
                    ("partial.txt", [(1, "needle retained content", None, None),
                                     (0, "__SF_TRUNCATED__", None, None)])
                ])
            raise RuntimeError("Injected native batch failure")

        api = "search_files_list_fast" if file_list else "search_directory_fast"
        monkeypatch.setattr(f"core.search_engine.{api}", fail)
        with caplog.at_level("INFO"):
            worker.run()

        count = int(partial)
        assert completions == [(count, count, count)]
        assert len(skipped) == count
        assert len(worker.all_results) == count
        if partial:
            assert worker.all_results[0][2][0][1] == "needle retained content"
        assert tab.search_state == Constants.SearchState.IDLE
        assert tab.result_view_panel.result_model.rowCount() == count
        assert AppStrings.SUMMARY_PREFIX_FAILED in tab.result_view_panel.summary_label.text()
        messages = [record.getMessage() for record in caplog.records]
        assert AppStrings.LOG_SCH_FAILED_PARTIAL.format(count, count, count) in messages
        assert AppStrings.STATUS_FOUND_COUNT.format(count) not in messages
        assert not any(message.startswith(AppStrings.LOG_SCH_ALL_DONE.split("{")[0]) for message in messages)
    finally:
        set_language(previous_language)
