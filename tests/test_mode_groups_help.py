"""One-line controls, extension groups and local help preserve search policy."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialogButtonBox, QTextBrowser

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
    assert row.indexOf(tab.search_panel.search_mode_combo) < row.indexOf(panel.search_btn)
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
    restored.set_special_mode(AppStrings.SPECIAL_SEARCH_ITEMS[3])
    assert restored.get_selected_extensions() == [C.EXT_JSON]
    restored.set_special_mode(AppStrings.SPECIAL_SEARCH_ITEMS[0])
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
    assert AppStrings.SEARCH_MODE_LABEL in text and AppStrings.SPECIAL_SEARCH_OFF_DISPLAY in text
    assert window.help_button.geometry().left() < window.log_button.geometry().left()
    assert window.log_button.geometry().left() < window.settings_button.geometry().left()
    bar = window.statusBar()
    for button in (window.help_button, window.log_button, window.settings_button):
        top = button.mapTo(bar, button.rect().topLeft()).y()
        bottom = bar.height() - top - button.height()
        assert top >= 3 and bottom >= 3
        assert abs(top - bottom) <= 2
    window._set_ui_locked(True)
    assert window.help_button.isEnabled()
    dialog.close()
    qtbot.mouseClick(window.help_button, Qt.MouseButton.LeftButton)
    assert window._search_help_dialog is dialog and dialog.isVisible()
    window._set_ui_locked(False)
    window.close()
