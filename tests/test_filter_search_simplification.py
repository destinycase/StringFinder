"""Retired policies cannot reinterpret results; grouped filters retain their terms."""
import inspect
import json

import pytest

from core import search_engine as engine
from core.worker import SearchWorker
from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants as C
from sf_utils.localization import get_language, set_language
from ui.main_window import MainWindow
from ui.panels import FilenameFilterPanel, SearchOptionsPanel
from ui.search_tab import SearchTab


@pytest.fixture(params=["ko", "en"])
def language(request):
    previous = get_language()
    set_language(request.param)
    yield request.param
    set_language(previous)


def test_filename_groups_roundtrip_and_overlap(qtbot, language):
    panel = FilenameFilterPanel()
    qtbot.addWidget(panel)
    assert panel.add_filename(" report, invoice,report ")
    assert panel.add_filename("REPORT,summary")
    assert panel.filename_list.count() == 2
    assert panel.get_selected_filenames() == ["report", "invoice", "summary"]
    panel.filename_list.itemWidget(panel.filename_list.item(0)).checkbox.setChecked(False)
    state = json.loads(json.dumps(panel.get_state()))
    restored = FilenameFilterPanel()
    qtbot.addWidget(restored)
    restored.load_state(state)
    assert restored.get_selected_filenames() == ["REPORT", "summary"]
    assert restored.get_state() == state
    assert restored.status_label.text() == AppStrings.FILENAME_FILTER_SELECTED.format(2, "REPORT, summary")
    restored.toggle_all(False)
    assert restored.get_selected_filenames() == []


@pytest.mark.parametrize("group", [",report", "report,", "report,,invoice", "report,*.txt"])
def test_invalid_filename_group_is_atomic(qtbot, group):
    panel = FilenameFilterPanel()
    qtbot.addWidget(panel)
    panel.add_filename("saved")
    assert not panel.add_filename(group)
    assert panel.get_selected_filenames() == ["saved"]
    assert not panel.filename_input_notice.isHidden()


def test_status_conditions_and_warning_icon(qtbot, mock_config_manager, language):
    window = MainWindow()
    qtbot.addWidget(window)
    tab = window.tab_widget.currentWidget()
    tab.folder_panel.restore_state({})
    tab.ext_panel.restore_state({})
    tab.filename_panel.restore_state({})
    window.show()
    assert not window.filter_status_warning.isHidden()
    assert not window.filter_status_warning.pixmap().isNull()
    assert AppStrings.FOLDER_FILTER_EMPTY in window.filter_status_label.text()
    tab.folder_panel.add_folder("D:/Documents")
    tab.ext_panel.add_extension("xls,xlsm")
    tab.filename_panel.add_filename("report,invoice")
    assert window.filter_status_warning.isHidden()
    assert tab.filter_tabs.tabText(1) == AppStrings.FILTER_FILENAME_TAB.format(2)
    assert window.filter_status_label.text() == " | ".join([
        tab.search_panel.search_mode_combo.currentText(),
        AppStrings.FOLDER_FILTER_SELECTED.format(1),
        AppStrings.EXTENSION_FILTER_SELECTED.format(2, "xls, xlsm"),
        AppStrings.FILENAME_FILTER_SELECTED.format(2, "report, invoice"),
    ])
    tab.ext_panel.set_special_mode(AppStrings.SPECIAL_SEARCH_ITEMS[3])
    assert AppStrings.EXTENSION_FILTER_SELECTED.format(1, "json") in window.filter_status_label.text()
    assert window.help_button.isVisible() and window.settings_button.isVisible()
    window.close()


def test_retired_feature_has_no_live_ui_or_api(qtbot, language):
    panel = SearchOptionsPanel()
    qtbot.addWidget(panel)
    assert not hasattr(panel, "boolean_search_check")
    assert not hasattr(panel, "is_existence_only")
    assert "existence_only" not in panel.get_state()
    # 검색 모드(기본/글자 변형) 메뉴도 검색 방식 통합으로 없어졌습니다.
    assert not hasattr(panel, "profile_menu") and not hasattr(panel, "set_complex_search")
    assert C.PAYLOAD_USE_COMPLEX_SEARCH not in panel.get_state()
    assert "existence_only" not in inspect.signature(engine.search_in_file).parameters
    assert not hasattr(engine, "find_files_with_keyword_fast")
    assert not hasattr(engine.sf_engine, "find_files_with_keyword")
    assert engine.sf_engine.API_VERSION == 8
    worker = SearchWorker({C.PAYLOAD_SEARCH_STRING: "needle"})
    assert not hasattr(worker, "existence_only")


@pytest.mark.parametrize("metadata", ["top", "context"])
def test_retired_snapshot_requires_research(qtbot, mock_config_manager, metadata, language):
    tab = SearchTab(mock_config_manager)
    qtbot.addWidget(tab)
    state = {"inputs": {"search": "needle"}, "results": [[1, "book.xlsx", "", "book.xlsx", [[1, "generated notice"]]]],
             "summary": {"total_elapsed": 10}, "skipped": [["book.xlsx", "ERR_OPEN|missing"]]}
    if metadata == "top":
        state["results_existence_only"] = True
    else:
        state["results_search_context"] = {"existence_only": True}
    tab.load_state(state)
    assert tab.result_view_panel.result_model.rowCount() == 0
    assert tab.skipped_count == 0 and tab.total_matches == 0
    assert tab.result_view_panel.empty_label.text() == AppStrings.SESSION_RESEARCH_REQUIRED
    assert tab.search_panel.get_search_text() == "needle"
    assert "results_existence_only" not in tab.get_state()
    tab.close()


def test_old_input_option_cannot_discard_real_results(qtbot, mock_config_manager):
    tab = SearchTab(mock_config_manager)
    qtbot.addWidget(tab)
    tab.load_state({"inputs": {"search": "needle", "existence_only": True},
                    "results": [[1, "file.txt", "", "file.txt", [[1, "needle actual content"]]]]})
    assert tab.result_view_panel.result_model.get_all_results()[0][4][0][1] == "needle actual content"
    tab.close()
