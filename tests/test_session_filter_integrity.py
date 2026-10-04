"""Session snapshots must not change candidates or reinterpret saved results."""
import json

import pytest

from sf_utils.constants import Constants as C
from ui.panels import ExtensionFilterPanel, FilenameFilterPanel, FolderFilterPanel
from ui.panels import FilterItemWidget, FilterListWidget
from ui.search_tab import SearchTab
from PySide6.QtWidgets import QListWidgetItem


@pytest.mark.parametrize("kind", ["folder", "extension", "filename"])
def test_filter_snapshot_replaces_unrelated_current_rows(qtbot, kind):
    cls, add_name, key, selected_name = {
        "folder": (FolderFilterPanel, "add_folder", None, "get_selected_folders"),
        "extension": (ExtensionFilterPanel, "add_extension", C.CONFIG_KEY_EXTENSIONS, "get_selected_extensions"),
        "filename": (FilenameFilterPanel, "add_filename", C.CONFIG_KEY_FILENAMES, "get_selected_filenames"),
    }[kind]
    panel = cls()
    qtbot.addWidget(panel)
    getattr(panel, add_name)("current")
    snapshot = {"saved": True, "disabled": False}
    panel.load_state(snapshot if key is None else {key: snapshot})
    assert getattr(panel, selected_name)() == ["saved"]
    panel.load_state({} if key is None else {key: {}})
    assert getattr(panel, selected_name)() == []


def test_tab_restore_does_not_mix_or_overwrite_global_filters(qtbot, mock_config_manager):
    manager = mock_config_manager
    manager.update_filters({"D:/current": True}, {C.CONFIG_KEY_EXTENSIONS: {"txt": True}}, {})
    before = manager.get_filters()
    tab = SearchTab(manager)
    qtbot.addWidget(tab)
    tab.load_state({C.PAYLOAD_INPUTS: {
        C.CONFIG_KEY_FOLDERS: {"D:/saved": True},
        C.CONFIG_KEY_EXTENSIONS: {"json": True},
        C.CONFIG_KEY_FILENAMES: {"report": True},
    }})
    assert tab.folder_panel.get_selected_folders() == ["D:/saved"]
    assert tab.ext_panel.get_selected_extensions() == ["json"]
    assert tab.filename_panel.get_selected_filenames() == ["report"]
    assert manager.get_filters() == before


@pytest.mark.parametrize("mode, detail", [
    (C.MODE_NORMAL, (1, "needle actual content")),
    (C.MODE_JSON, (1, "$.value", "needle actual content")),
    (C.MODE_XML, (1, "/root/value", "needle actual content")),
    (C.MODE_EXCEL, (1, "Base", "A1", "needle actual content")),
])
def test_result_context_survives_input_changes_and_json_roundtrip(
    qtbot, mock_config_manager, tmp_path, mode, detail
):
    original = SearchTab(mock_config_manager)
    qtbot.addWidget(original)
    original.result_view_panel.set_search_context("needle", mode)
    original.result_view_panel.set_results([[1, "data.txt", "", "data.txt", [detail]]])
    original.result_view_panel.set_filename_filters(["original"])
    original.search_panel.search_combo.setEditText("different draft")
    original.ext_panel._set_special_mode(C.MODE_EXCEL if mode == C.MODE_NORMAL else C.MODE_NORMAL)
    original.search_panel.boolean_search_check.setChecked(True)
    saved = json.loads(json.dumps(original.get_state()))
    restored = SearchTab(mock_config_manager)
    qtbot.addWidget(restored)
    restored.load_state(saved)
    assert restored.search_panel.get_search_text() == "different draft"
    assert restored.result_view_panel.search_text == "needle"
    assert restored.result_view_panel.search_mode == mode
    assert not restored.result_view_panel.existence_only
    assert restored.result_view_panel.result_model.filename_filters == ["original"]
    assert restored.result_view_panel.result_model.get_all_results()[0][4][0][-1] == "needle actual content"
    restored.result_view_panel._export_to_excel(str(tmp_path / "result.xlsx"))
    assert (tmp_path / "result.xlsx").exists()


@pytest.mark.parametrize("cls, add", [
    (FolderFilterPanel, "add_folder"),
    (ExtensionFilterPanel, "add_extension"),
    (FilenameFilterPanel, "add_filename"),
])
def test_malformed_filter_entries_are_ignored(qtbot, cls, add):
    panel = cls()
    qtbot.addWidget(panel)
    panel.restore_state([123, None, {}, "valid"])
    assert getattr(panel, add)(None) in (None, False)
    panel.restore_state({"valid": True, "bad": "false", "bad2": 123})
    state = panel.get_state()
    entries = state.get(C.CONFIG_KEY_EXTENSIONS, state.get(C.CONFIG_KEY_FILENAMES, state))
    assert entries == {"valid": True}


def test_config_filters_validate_nested_entries_before_tab_creation(qtbot, mock_config_manager):
    manager = mock_config_manager
    manager.update_filters([123, "D:/valid"], {
        C.CONFIG_KEY_EXTENSIONS: [123, None, "json"], C.PAYLOAD_SPECIAL_MODE: 123,
    }, {C.CONFIG_KEY_FILENAMES: {"report": True, "invalid": []}})
    tab = SearchTab(manager)
    qtbot.addWidget(tab)
    assert tab.folder_panel.get_selected_folders() == ["D:/valid"]
    assert tab.ext_panel.get_selected_extensions() == ["json"]
    assert tab.filename_panel.get_selected_filenames() == ["report"]


def test_exact_result_policy_is_preserved(qtbot, mock_config_manager):
    source = SearchTab(mock_config_manager)
    qtbot.addWidget(source)
    source.result_view_panel.set_search_context("needle", f"JSON ({C.MODE_EXACT})")
    saved = source.get_state()
    assert saved["results_search_context"]["exact"] is True
    source.ext_panel._set_special_mode(C.MODE_NORMAL)
    source.load_state(saved)
    assert C.MODE_EXACT in source.result_view_panel.search_mode


def test_filter_action_registration_coalesces_position_updates(qtbot, monkeypatch):
    listing = FilterListWidget()
    qtbot.addWidget(listing)
    calls = []
    position = listing._position_actions

    def counted():
        calls.append(1)
        position()

    monkeypatch.setattr(listing, "_position_actions", counted)
    for index in range(100):
        item = QListWidgetItem(listing)
        listing.setItemWidget(item, FilterItemWidget(str(index)))
    assert not calls
    qtbot.waitUntil(lambda: bool(calls))
    # A deferred Qt item-layout event may also position the actions, but there
    # must not be one full-list positioning pass per registered row.
    assert len(calls) <= 3


def test_filename_legacy_migration_roundtrip_preserves_unchecked_state(qtbot):
    panel = FilenameFilterPanel()
    qtbot.addWidget(panel)
    panel.add_filename("unrelated")
    panel.load_state({C.PAYLOAD_FILENAME_FILTER: "report, invoice", C.CONFIG_KEY_FILENAMES: {"report": False}})
    assert panel.get_selected_filenames() == ["invoice"]
    saved = panel.get_state()
    restored = FilenameFilterPanel()
    qtbot.addWidget(restored)
    restored.load_state(saved)
    assert restored.get_state() == saved


def test_missing_filter_snapshots_keep_defaults_but_explicit_empty_clears(qtbot, mock_config_manager):
    manager = mock_config_manager
    manager.update_filters({"D:/default": True}, ["txt"], ["report"])
    tab = SearchTab(manager)
    qtbot.addWidget(tab)
    tab.load_state({C.PAYLOAD_INPUTS: {C.STATE_KEY_SEARCH: "needle"}})
    assert tab.folder_panel.get_selected_folders() == ["D:/default"]
    assert tab.ext_panel.get_selected_extensions() == ["txt"]
    assert tab.filename_panel.get_selected_filenames() == ["report"]
    tab.load_state({C.PAYLOAD_INPUTS: {
        C.CONFIG_KEY_FOLDERS: {}, C.CONFIG_KEY_EXTENSIONS: {}, C.CONFIG_KEY_FILENAMES: {},
    }})
    assert tab.folder_panel.get_selected_folders() == []
    assert tab.ext_panel.get_selected_extensions() == []
    assert tab.filename_panel.get_selected_filenames() == []
