"""Filter guidance must describe the existing search contract, not change it."""

import pytest
from PySide6.QtCore import QSignalBlocker, Qt

from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants
from sf_utils.localization import get_language, set_language
from ui.panels import ExtensionFilterPanel, FilenameFilterPanel, FolderFilterPanel
from ui.search_tab import SearchTab


@pytest.fixture(params=["ko", "en"])
def language(request):
    previous = get_language()
    set_language(request.param)
    yield request.param
    set_language(previous)


@pytest.mark.parametrize("panel_class", [FolderFilterPanel, ExtensionFilterPanel, FilenameFilterPanel])
def test_empty_guidance_and_restore_are_current(qtbot, language, panel_class):
    panel = panel_class()
    qtbot.addWidget(panel)
    if panel_class is FolderFilterPanel:
        selected = panel.get_selected_folders
        add = panel.add_folder
        value = "C:/documents"
        empty_text = AppStrings.FOLDER_FILTER_EMPTY
        panel.add_folder(value)
        empty = {}
    elif panel_class is ExtensionFilterPanel:
        selected = panel.get_selected_extensions
        add = panel.add_extension
        value = "txt"
        empty_text = AppStrings.EXTENSION_FILTER_ALL
        panel.add_extension(value)
        empty = {}
    else:
        selected = panel.get_selected_filenames
        add = panel.add_filename
        value = "report"
        empty_text = AppStrings.FILENAME_FILTER_ALL
        panel.add_filename(value)
        empty = {Constants.CONFIG_KEY_FILENAMES: {}}
    assert selected() == [value]
    panel.show()
    qtbot.waitUntil(lambda: not panel.usage_label.isVisible())
    panel.toggle_all(False)
    assert selected() == []
    assert panel.status_label.text() == empty_text
    # Session restoration blocks outward signals; labels must still update.
    with QSignalBlocker(panel):
        add(value + "2")
        panel.restore_state(empty)
    assert selected() == []
    assert panel.status_label.text() == empty_text
    assert panel.usage_label.alignment() == (Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    qtbot.waitUntil(panel.usage_label.isVisible)


@pytest.mark.parametrize("index", range(1, 7))
def test_special_mode_targets_and_saved_extensions_are_preserved(qtbot, language, index):
    panel = ExtensionFilterPanel()
    qtbot.addWidget(panel)
    panel.add_extension("txt")
    panel.add_extension("log", checked=False)
    panel.set_special_mode(AppStrings.SPECIAL_SEARCH_ITEMS[index])
    expected = [Constants.EXT_XML] if index <= 2 else (
        [Constants.EXT_JSON] if index <= 4 else list(Constants.EXT_EXCEL)
    )
    assert panel.get_selected_extensions() == expected
    assert not panel.ext_list.isEnabled()
    for extension in expected:
        assert extension.lstrip('.') in panel.status_label.text()
    assert ".." not in panel.status_label.text()
    saved = panel.get_state()
    restored = ExtensionFilterPanel()
    qtbot.addWidget(restored)
    with QSignalBlocker(restored):
        restored.load_state(saved)
    assert restored.get_selected_extensions() == expected
    assert restored.status_label.text() == panel.status_label.text()
    restored.set_special_mode(AppStrings.SPECIAL_SEARCH_ITEMS[0])
    assert restored.get_selected_extensions() == ["txt"]
    assert restored.ext_list.isEnabled()
    assert restored.get_state()[Constants.CONFIG_KEY_EXTENSIONS] == {"txt": True, "log": False}


def test_document_selector_is_in_search_settings_and_locked(qtbot, mock_config_manager, language):
    tab = SearchTab(mock_config_manager)
    qtbot.addWidget(tab)
    controls = tab.search_panel.search_settings_button
    assert tab.search_panel.isAncestorOf(controls)
    assert not tab.ext_panel.isAncestorOf(controls)
    tab.search_panel.set_searching(True)
    assert not tab.search_panel.search_settings_button.isEnabled()
    tab.search_panel.set_searching(False)
    assert tab.search_panel.search_settings_button.isEnabled()
    tab.close()
