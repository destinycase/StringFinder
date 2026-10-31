"""One-line controls, extension groups and local help preserve search policy."""
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialogButtonBox, QTextBrowser

from core import search_engine as se
from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants as C
from sf_utils.localization import get_language, set_language
from ui.main_window import MainWindow
from ui.panels import ExtensionFilterPanel
from ui.search_tab import SearchTab


@pytest.fixture(params=["ko", "en"])
def language(request):
    old = get_language()
    set_language(request.param)
    yield request.param
    set_language(old)


def test_mode_order(qtbot, mock_config_manager, language):
    tab = SearchTab(mock_config_manager)
    qtbot.addWidget(tab)
    panel = tab.search_panel
    row = panel.input_layout
    assert row.indexOf(tab.ext_panel.special_controls) < row.indexOf(panel.search_btn)
    tab.resize(1280, 640)
    tab.show()
    qtbot.waitUntil(tab.isVisible)
    tab.close()


def test_extension_group_round_trip_and_mode_switch(qtbot, language):
    panel = ExtensionFilterPanel()
    qtbot.addWidget(panel)
    panel.ext_edit.setText(" .XLSX, xlsm, .xls, xlsb,xlsx ")
    panel._on_add_clicked()
    assert panel.ext_list.count() == 1
    assert panel.get_selected_extensions() == list(C.EXT_EXCEL)
    panel.add_extension("xlsx,txt")
    assert panel.get_selected_extensions() == [*C.EXT_EXCEL, "txt"]
    panel.ext_list.itemWidget(panel.ext_list.item(0)).checkbox.setChecked(False)
    saved = panel.get_state()
    restored = ExtensionFilterPanel()
    qtbot.addWidget(restored)
    restored.load_state(saved)
    assert restored.get_selected_extensions() == ["xlsx", "txt"]
    restored.special_combo.setCurrentIndex(3)
    assert restored.get_selected_extensions() == [C.EXT_JSON]
    restored.special_combo.setCurrentIndex(0)
    assert restored.get_state() == saved
    restored.toggle_all(False)
    assert restored.get_selected_extensions() == []


@pytest.mark.parametrize("group", ["txt,", ",txt", "txt,,json", "txt,.", "txt,*.json"])
def test_invalid_groups_are_rejected_atomically(qtbot, language, group):
    panel = ExtensionFilterPanel()
    qtbot.addWidget(panel)
    panel.add_extension("xml")
    panel.ext_edit.setText(group)
    panel._on_add_clicked()
    assert panel.ext_list.count() == 1
    assert panel.get_selected_extensions() == ["xml"]
    assert panel.ext_edit.text() == group
    assert not panel.extension_input_notice.isHidden()
    assert panel.extension_input_notice.text() == (
        AppStrings.EXTENSION_FILTER_WILDCARD_NOT_ALLOWED if "*" in group
        else AppStrings.EXTENSION_FILTER_INVALID_GROUP
    )


@pytest.mark.parametrize("precise", [False, True])
def test_groups_select_same_files_on_search_paths(tmp_path, qtbot, language, precise):
    for name in ("a.txt", "b.json", "c.xml", "no_extension"):
        (tmp_path / name).write_text("needle", encoding="utf-8")
    panel = ExtensionFilterPanel()
    qtbot.addWidget(panel)
    panel.add_extension("txt,json")
    extensions = panel.get_selected_extensions()
    if precise:
        files = se.FileScanner([str(tmp_path)], extensions, exclude_hidden=False).scan()
        names = {Path(row[0]).name for row in files}
    else:
        result = se.search_directory_fast([str(tmp_path)], "needle", extensions, exclude_hidden=False)
        names = {Path(row[0]).name for row in result["results"]}
    assert names == {"a.txt", "b.json"}


def test_help_button_order_localized_content_and_reuse(qtbot, mock_config_manager, language):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    qtbot.mouseClick(window.help_button, Qt.MouseButton.LeftButton)
    dialog = window._search_help_dialog
    assert dialog.isVisible() and not dialog.isModal()
    assert dialog.windowTitle() == AppStrings.SEARCH_HELP_TITLE
    assert dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Close).text() == AppStrings.BTN_CLOSE
    text = dialog.findChild(QTextBrowser).toPlainText()
    assert "xlsx,xlsm,xls,xlsb" in text
    assert AppStrings.SPECIAL_SEARCH_LABEL in text
    assert window.help_button.geometry().left() < window.log_button.geometry().left()
    assert window.log_button.geometry().left() < window.settings_button.geometry().left()
    window._set_ui_locked(True)
    assert window.help_button.isEnabled()
    dialog.close()
    qtbot.mouseClick(window.help_button, Qt.MouseButton.LeftButton)
    assert window._search_help_dialog is dialog and dialog.isVisible()
    window._set_ui_locked(False)
    window.close()
