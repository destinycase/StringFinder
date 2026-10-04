"""Malformed session fields must not prevent startup or alter valid results."""
from pathlib import Path
from unittest.mock import Mock

import pytest

from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants as C
from ui.main_window import MainWindow
from ui.panels import SearchOptionsPanel
from ui.search_tab import SearchTab


@pytest.mark.parametrize("value", [None, 123, {}, [], True])
def test_invalid_query_recovers_without_discarding_results(qtbot, mock_config_manager, value):
    tab = SearchTab(mock_config_manager)
    qtbot.addWidget(tab)
    details = [[1, "needle actual content"]]
    tab.load_state({
        C.PAYLOAD_INPUTS: {C.STATE_KEY_SEARCH: value},
        C.PAYLOAD_RESULTS: [[1, "data.txt", "", "data.txt", details]],
    })
    assert tab.search_panel.search_combo.currentText() == ""
    assert tab.result_view_panel.search_text == ""
    assert tab.result_view_panel.result_model.get_all_results()[0][4] == details


@pytest.mark.parametrize("value", [None, 1, "false", "true", {}, [], [True]])
def test_invalid_boolean_fields_use_defaults(qtbot, value):
    panel = SearchOptionsPanel()
    qtbot.addWidget(panel)
    panel.load_state({
        C.PAYLOAD_USE_COMPLEX_SEARCH: value,
        C.PAYLOAD_EXISTENCE_ONLY: value,
        C.PAYLOAD_EXCLUDE_HIDDEN: value,
    })
    assert not panel.is_complex_search()
    assert not panel.is_existence_only()
    assert panel.exclude_hidden_check.isChecked()


@pytest.mark.parametrize("value", [False, True])
def test_valid_search_settings_roundtrip(qtbot, value):
    panel = SearchOptionsPanel()
    qtbot.addWidget(panel)
    state = {
        C.STATE_KEY_SEARCH: "  needle 한글  ",
        C.PAYLOAD_USE_COMPLEX_SEARCH: value,
        C.PAYLOAD_EXISTENCE_ONLY: value,
        C.PAYLOAD_EXCLUDE_HIDDEN: value,
    }
    panel.load_state(state)
    assert panel.get_state() == state


def create_window(qtbot, monkeypatch):
    monkeypatch.setattr(MainWindow, "_apply_theme", lambda self: None)
    window = MainWindow()
    qtbot.addWidget(window)
    monkeypatch.setattr(window, "_quit_application", lambda: None)
    return window


def test_real_startup_recovers_malformed_input_and_loads_next_session(
    qtbot, mock_config_manager, monkeypatch
):
    manager = mock_config_manager
    assert manager.save_session("invalid-input", {C.PAYLOAD_INPUTS: {
        C.STATE_KEY_SEARCH: 123, C.PAYLOAD_EXCLUDE_HIDDEN: {},
    }})
    assert manager.save_session("valid", {C.PAYLOAD_INPUTS: {C.STATE_KEY_SEARCH: "needle"}})
    manager.set_tab_order(["invalid-input", "valid"])
    window = create_window(qtbot, monkeypatch)
    assert window.tab_widget.count() == 2
    assert window.tab_widget.widget(0).search_panel.search_combo.currentText() == ""
    assert window.tab_widget.widget(1).search_panel.get_search_text() == "needle"
    assert manager.load_session("invalid-input")[C.PAYLOAD_INPUTS][C.STATE_KEY_SEARCH] == 123
    window.cleanup()


@pytest.mark.parametrize("all_failed", [False, True])
def test_startup_isolates_restore_failure_and_preserves_session_files(
    qtbot, mock_config_manager, monkeypatch, all_failed
):
    manager = mock_config_manager
    bad_name = AppStrings.SEARCH_TAB_TITLE_TEMPLATE.format(AppStrings.SEARCH_TAB_DEFAULT_TITLE, 1)
    bad_state = {C.PAYLOAD_INPUTS: {C.STATE_KEY_SEARCH: "injected failure"}}
    assert manager.save_session(bad_name, bad_state)
    if not all_failed:
        assert manager.save_session("valid", {C.PAYLOAD_INPUTS: {C.STATE_KEY_SEARCH: "needle"}})
    manager.set_tab_order([bad_name])  # The valid session exercises unordered restoration too.
    before = {str(path): path.read_bytes() for path in Path(manager.sessions_dir).glob("*.json")}
    real_load = SearchTab.load_state
    failed_tabs = []

    def fail_one(tab, state):
        if state == bad_state:
            failed_tabs.append(tab)
            raise TypeError("injected restoration failure")
        return real_load(tab, state)

    monkeypatch.setattr(SearchTab, "load_state", fail_one)
    log = Mock()
    monkeypatch.setattr("ui.main_window.logger.error", log)
    window = create_window(qtbot, monkeypatch)
    assert window.tab_widget.count() == 1
    assert window.tab_widget.tabText(0) != bad_name
    assert len(failed_tabs) == 1
    assert window.tab_widget.indexOf(failed_tabs[0]) == -1
    assert not failed_tabs[0]._liveliness_timer.isActive()
    log.assert_called_once()
    assert "injected restoration failure" in log.call_args.args[0]
    assert manager.get_tab_order() == [bad_name]
    assert {str(path): path.read_bytes() for path in Path(manager.sessions_dir).glob("*.json")} == before
    if all_failed:
        window._save_tab(0)
        assert manager.load_session(bad_name)[C.PAYLOAD_INPUTS] == bad_state[C.PAYLOAD_INPUTS]
    window.cleanup()
