"""User selections stay exclusive within each group and preserve engine inputs."""
import json

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QHelpEvent
from PySide6.QtWidgets import QApplication

from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants as C
from sf_utils.localization import get_language, set_language
from ui.panels import SearchOptionsPanel, normalize_special_search_mode
from ui.search_tab import SearchTab


@pytest.fixture(params=["ko", "en"])
def language(request):
    previous = get_language()
    set_language(request.param)
    yield request.param
    set_language(previous)


def test_defaults_and_menu_order(qtbot, language):
    panel = SearchOptionsPanel()
    qtbot.addWidget(panel)
    assert [action.text() for action in panel.search_settings_menu.actions()] == [
        AppStrings.SEARCH_PROFILE_LABEL, AppStrings.SPECIAL_SEARCH_LABEL,
    ]
    assert panel.profile_actions[0].isChecked()
    assert panel.special_mode_actions[AppStrings.SPECIAL_SEARCH_OFF].isChecked()
    assert [action.text() for action in panel.value_menu.actions()] == [
        AppStrings.SPECIAL_SEARCH_ITEMS[index] for index in (0, 3, 4, 1, 2, 5, 6)
    ]
    assert AppStrings.SEARCH_PROFILE_NORMAL in panel.search_settings_button.text()
    assert AppStrings.SEARCH_VALUE_DISABLED_SELECTION in panel.search_settings_button.text()
    assert not hasattr(panel, "search_profile_combo")


@pytest.mark.parametrize("precise", [False, True])
@pytest.mark.parametrize("value_index", range(7))
def test_each_group_has_exactly_one_selection(qtbot, language, precise, value_index):
    panel = SearchOptionsPanel()
    qtbot.addWidget(panel)
    panel.profile_actions[int(precise)].trigger()
    mode = AppStrings.SPECIAL_SEARCH_ITEMS[value_index]
    action = panel.special_mode_actions[mode]
    action.trigger()
    action.trigger()  # Selecting the current choice cannot leave the group empty.
    assert sum(action.isChecked() for action in panel.profile_actions) == 1
    assert sum(action.isChecked() for action in panel.value_menu.actions()) == 1
    assert panel.is_complex_search() is precise
    assert panel.get_state()[C.PAYLOAD_SPECIAL_MODE] == mode
    profile = AppStrings.SEARCH_PROFILE_PRECISE if precise else AppStrings.SEARCH_PROFILE_NORMAL
    assert profile in panel.search_settings_button.text()
    assert (mode if value_index else AppStrings.SEARCH_VALUE_DISABLED_SELECTION) in panel.search_settings_button.text()
    assert (not panel.search_settings_button.icon().isNull()) is precise


def test_selection_reaches_search_and_survives_session(qtbot, mock_config_manager, tmp_path, language, monkeypatch):
    tab = SearchTab(mock_config_manager)
    qtbot.addWidget(tab)
    tab.folder_panel.restore_state({str(tmp_path): True})
    tab.ext_panel.restore_state({"txt": True, "log": False})
    panel = tab.search_panel
    panel.search_combo.set_current_text("needle")
    panel.profile_actions[1].trigger()
    mode = AppStrings.SPECIAL_SEARCH_ITEMS[4]  # JSON exact, irrespective of menu display order.
    panel.special_mode_actions[mode].trigger()
    assert tab.ext_panel.get_selected_extensions() == [C.EXT_JSON]
    assert not tab.ext_panel.ext_list.isEnabled()
    saved = json.loads(json.dumps(tab.get_state()))
    restored = SearchTab(mock_config_manager)
    qtbot.addWidget(restored)
    before = mock_config_manager.get_filters()
    restored.load_state(saved)
    assert mock_config_manager.get_filters() == before
    assert restored.search_panel.is_complex_search()
    assert restored.search_panel.special_mode_actions[mode].isChecked()
    captured = []
    monkeypatch.setattr(restored, "_setup_search_worker", captured.append)
    restored.start_search()
    assert captured[0][C.PAYLOAD_USE_COMPLEX_SEARCH] is True
    assert captured[0][C.PAYLOAD_SPECIAL_MODE] == mode
    assert captured[0][C.PAYLOAD_EXTENSIONS] == [C.EXT_JSON]
    restored._set_inputs_enabled(True)
    restored.search_panel.special_mode_actions[AppStrings.SPECIAL_SEARCH_OFF].trigger()
    assert restored.ext_panel.get_selected_extensions() == ["txt"]
    assert restored.ext_panel.ext_list.isEnabled()
    restored.close()
    tab.close()


def test_open_menu_closes_and_locks_during_search(qtbot, language):
    panel = SearchOptionsPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel.search_settings_menu.popup(panel.mapToGlobal(QPoint(0, panel.height())))
    assert panel.search_settings_menu.isVisible()
    panel.set_searching(True)
    assert not panel.search_settings_menu.isVisible()
    assert not panel.search_settings_button.isEnabled()
    assert all(not action.isEnabled() for action in [*panel.profile_actions, *panel.value_menu.actions()])
    before = panel.get_state()
    panel.profile_actions[1].trigger()
    assert panel.get_state() == before
    panel.set_searching(False)
    assert panel.search_settings_button.isEnabled()
    panel.profile_actions[1].trigger()
    assert panel.is_complex_search()


def test_only_precise_menu_item_shows_guidance(qtbot, language, monkeypatch):
    panel = SearchOptionsPanel()
    qtbot.addWidget(panel)
    menu = panel.profile_menu
    menu.popup(QPoint(50, 50))
    shown, hidden = [], []
    monkeypatch.setattr("ui.panels.QToolTip.showText", lambda *args: shown.append(args[1]))
    monkeypatch.setattr("ui.panels.QToolTip.hideText", lambda: hidden.append(True))
    for action in reversed(panel.profile_actions):
        position = menu.actionGeometry(action).center()
        event = QHelpEvent(QEvent.Type.ToolTip, position, menu.mapToGlobal(position))
        QApplication.sendEvent(menu, event)
    assert shown == [AppStrings.COMPLEX_SEARCH_TOOLTIP]
    assert hidden
    menu.close()


def test_native_submenus_work_with_keyboard(qtbot, language):
    panel = SearchOptionsPanel()
    qtbot.addWidget(panel)
    panel.show()
    menu = panel.search_settings_menu
    menu.popup(panel.mapToGlobal(QPoint(0, panel.height())))
    qtbot.keyClick(menu, Qt.Key.Key_Down)
    qtbot.keyClick(menu, Qt.Key.Key_Right)
    qtbot.waitUntil(panel.profile_menu.isVisible)
    for _ in range(2):
        if panel.profile_menu.activeAction() is panel.profile_actions[1]:
            break
        qtbot.keyClick(panel.profile_menu, Qt.Key.Key_Down)
    assert panel.profile_menu.activeAction() is panel.profile_actions[1]
    qtbot.keyClick(panel.profile_menu, Qt.Key.Key_Return)
    assert panel.is_complex_search()
    assert panel.profile_actions[1].isChecked()
    assert not menu.isVisible()
    menu.popup(panel.mapToGlobal(QPoint(0, panel.height())))
    qtbot.keyClick(menu, Qt.Key.Key_Escape)
    assert not menu.isVisible()
    assert panel.is_complex_search()


@pytest.mark.parametrize("old_mode", ["끄기", "기본", "Default", "Off", "Normal", None, 123, {}, []])
def test_old_default_labels_restore_to_disabled(old_mode, language):
    assert normalize_special_search_mode(old_mode) == AppStrings.SPECIAL_SEARCH_OFF


@pytest.mark.parametrize("old_mode,index", [("JSON (정확히 일치)", 4), ("XML (exact)", 2), ("Excel (contains)", 5)])
def test_old_modes_restore_in_current_language(qtbot, old_mode, index, language):
    panel = SearchOptionsPanel()
    qtbot.addWidget(panel)
    panel.load_state({C.PAYLOAD_USE_COMPLEX_SEARCH: True, C.PAYLOAD_SPECIAL_MODE: old_mode})
    assert panel.is_complex_search()
    assert panel.special_mode_actions[AppStrings.SPECIAL_SEARCH_ITEMS[index]].isChecked()
