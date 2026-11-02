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
        AppStrings.SPECIAL_SEARCH_MENU_ITEMS[index] for index in (0, 3, 4, 1, 2, 5, 6)
    ]
    assert panel.search_settings_button.text() == AppStrings.SEARCH_PROFILE_NORMAL_COMPACT
    assert not hasattr(panel, "search_settings_summary")
    assert panel.search_settings_button.menu() is None
    assert panel.search_settings_button.accessibleName() == (
        "검색 모드: 기본 검색 (권장) / 값만 검색: 사용 안 함" if language == "ko"
        else "Search mode: Default search (recommended) / Values only: Off"
    )
    assert not hasattr(panel, "search_profile_combo")
    assert [action.text() for action in panel.profile_actions] == (
        ["기본 검색 (권장)", "글자 변형 검색 (느림)"] if language == "ko"
        else ["Default search (recommended)", "Character variant search (slow)"]
    )
    assert panel.search_settings_label.text() == ("검색 설정" if language == "ko" else "Search settings")
    assert panel.search_settings_label.buddy() is panel.search_settings_button


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
    assert panel.search_settings_button.accessibleName() == AppStrings.SEARCH_SETTINGS_SELECTION.format(
        profile, AppStrings.SPECIAL_SEARCH_MENU_ITEMS[value_index]
    )
    compact_profile = AppStrings.SEARCH_PROFILE_PRECISE_COMPACT if precise else AppStrings.SEARCH_PROFILE_NORMAL_COMPACT
    value = AppStrings.SPECIAL_SEARCH_COMPACT_ITEMS[value_index]
    expected = AppStrings.SEARCH_SETTINGS_COMPACT_SELECTION.format(compact_profile, value) if value else compact_profile
    assert panel.search_settings_button.text() == expected
    assert panel.search_settings_label.buddy() is panel.search_settings_button
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


def test_requested_labels_and_input_order(qtbot, language):
    panel = SearchOptionsPanel()
    qtbot.addWidget(panel)
    row = panel.input_layout
    assert row.itemAtPosition(0, 0).widget() is panel.search_settings_label
    assert panel.search_label.text() == ("검색할 문자열" if language == "ko" else "Text to search")
    assert row.indexOf(panel.search_settings_button) < row.indexOf(panel.search_combo) < row.indexOf(panel.search_btn)
    if language == "ko":
        assert panel.value_menu.title() == "값만 검색"
        assert [action.text() for action in panel.value_menu.actions()] == [
            "사용 안 함", "JSON (부분 일치)", "JSON (정확히 일치)",
            "XML (부분 일치)", "XML (정확히 일치)",
            "Excel (부분 일치)", "Excel (정확히 일치)",
        ]
    else:
        assert panel.value_menu.title() == "Values only"
        assert [action.text() for action in panel.value_menu.actions()] == [
            "Off", "JSON (contains)", "JSON (exact)", "XML (contains)", "XML (exact)",
            "Excel (contains)", "Excel (exact)",
        ]


def test_menu_warning_is_colored_without_replacing_selection(qtbot, language):
    panel = SearchOptionsPanel()
    qtbot.addWidget(panel)
    panel.profile_actions[1].trigger()
    menu = panel.profile_menu
    menu.popup(QPoint(50, 50))
    QApplication.processEvents()
    image = menu.grab().toImage()
    yellow = [image.pixelColor(x, y) for y in range(image.height()) for x in range(image.width())
              if (color := image.pixelColor(x, y)).red() > 170 and color.green() > 120 and color.blue() < 100]
    assert len(yellow) > 5
    assert panel.profile_actions[1].isChecked()
    assert sum(action.isChecked() for action in panel.profile_actions) == 1
    assert panel.profile_actions[1].icon().isNull()  # Theme-owned check column remains available.
    menu.close()


@pytest.mark.parametrize("width", [600, 720, 1280])
def test_long_selection_keeps_query_and_buttons_inside_panel(qtbot, language, width):
    panel = SearchOptionsPanel()
    qtbot.addWidget(panel)
    panel.profile_actions[1].trigger()
    panel.special_mode_actions[AppStrings.SPECIAL_SEARCH_ITEMS[6]].trigger()
    panel.resize(width, 220)
    panel.show()
    qtbot.wait(30)
    for widget in (panel.search_settings_label, panel.search_label, panel.search_combo, panel.search_settings_button, panel.search_btn):
        assert widget.geometry().right() < panel.width()
        assert widget.geometry().left() >= 0
    assert panel.search_combo.width() >= 100
    if panel._toolbar_mode == 2:
        assert panel.search_settings_label.width() == panel.search_label.width()
        assert panel.search_settings_label.geometry().left() == panel.search_label.geometry().left()
        assert panel.search_settings_button.geometry().left() == panel.search_combo.geometry().left()
    if panel._toolbar_mode == 1:
        assert abs(panel.search_combo.geometry().center().y()
                   - panel.search_settings_button.geometry().center().y()) <= 1


def test_settings_button_opens_menu_and_controls_stay_aligned(qtbot, language):
    panel = SearchOptionsPanel()
    qtbot.addWidget(panel)
    panel.resize(1280, 150)
    panel.show()
    qtbot.wait(20)
    initial_width = panel.search_settings_button.width()
    for precise in (True, False):
        panel.set_complex_search(precise)
        for mode in AppStrings.SPECIAL_SEARCH_ITEMS:
            panel.set_special_mode(mode)
            assert panel.search_settings_button.width() == initial_width
            assert panel.search_settings_button.fontMetrics().horizontalAdvance(
                panel.search_settings_button.text()) <= initial_width - 40
        for searching in (True, False):
            panel.set_searching(searching)
            action = panel.stop_btn if searching else panel.search_btn
            assert panel.search_combo.height() == panel.search_settings_button.height() == action.height()
            assert panel.search_combo.geometry().center().y() == action.geometry().center().y()
    qtbot.mouseClick(panel.search_settings_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(panel.search_settings_menu.isVisible)
    panel.search_settings_menu.close()


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


@pytest.mark.parametrize("precise", [False, True])
@pytest.mark.parametrize("saved_mode,index", [
    ("사용 안함", 0), ("Off", 0),
    ("XML (부분 일치)", 1), ("XML (정확히 일치)", 2),
    ("JSON (부분 일치)", 3), ("JSON (정확히 일치)", 4),
    ("Excel (부분 일치)", 5), ("Excel (정확히 일치)", 6),
    ("XML (contains)", 1), ("XML (exact)", 2),
    ("JSON (contains)", 3), ("JSON (exact)", 4),
    ("Excel (contains)", 5), ("Excel (exact)", 6),
])
def test_legacy_saved_values_remain_engine_values(qtbot, language, precise, saved_mode, index):
    expected = (["사용 안함", "XML (부분 일치)", "XML (정확히 일치)",
                 "JSON (부분 일치)", "JSON (정확히 일치)", "Excel (부분 일치)", "Excel (정확히 일치)"]
                if language == "ko" else
                ["Off", "XML (contains)", "XML (exact)", "JSON (contains)", "JSON (exact)",
                 "Excel (contains)", "Excel (exact)"])
    assert AppStrings.SPECIAL_SEARCH_ITEMS == expected
    panel = SearchOptionsPanel()
    qtbot.addWidget(panel)
    panel.load_state({C.PAYLOAD_USE_COMPLEX_SEARCH: precise, C.PAYLOAD_SPECIAL_MODE: saved_mode})
    state = panel.get_state()
    assert state[C.PAYLOAD_USE_COMPLEX_SEARCH] is precise
    assert state[C.PAYLOAD_SPECIAL_MODE] == expected[index]


def test_display_resources_do_not_use_retired_names(language):
    for key in ("SEARCH_PROFILE_NORMAL", "SEARCH_PROFILE_PRECISE", "SEARCH_PROFILE_PRECISE_COMPACT",
                "SPECIAL_SEARCH_LABEL", "SPECIAL_SEARCH_MENU_ITEMS", "COMPLEX_SEARCH_TOOLTIP",
                "SEARCH_HELP_TEXT", "SEARCH_EDIT_PLACEHOLDER", "RESULT_EMPTY_NO_MATCH"):
        value = str(getattr(AppStrings, key))
        for retired in ("누락 방지", "특수 검색", "값 만", "Miss-prevention", "Special search"):
            assert retired not in value


def test_label_widths_follow_layout_after_repeated_resizing(qtbot, language):
    panel = SearchOptionsPanel()
    qtbot.addWidget(panel)
    panel.show()
    for width in (1280, 600, 1280, 600, 1280):
        panel.resize(width, 180)
        qtbot.wait(10)
        if width == 1280:
            assert panel._toolbar_mode == 1
            for label in (panel.search_label, panel.search_settings_label):
                assert label.minimumWidth() == 0
                assert label.maximumWidth() == 16777215
                assert label.width() == label.sizeHint().width()
            if language == "ko":
                assert panel.search_settings_label.width() < panel.search_label.width()
        else:
            assert panel._toolbar_mode == 2
            assert panel.search_settings_label.width() == panel.search_label.width()


def test_layout_switch_uses_sum_of_natural_label_widths(qtbot, language):
    panel = SearchOptionsPanel()
    qtbot.addWidget(panel)
    panel.resize(1280, 180)
    panel.show()
    qtbot.wait(10)
    margins = panel.layout().contentsMargins()
    labels = panel.search_label.sizeHint().width() + panel.search_settings_label.sizeHint().width()
    actions = max(panel.search_btn.sizeHint().width(), panel.stop_btn.sizeHint().width())
    boundary = (margins.left() + margins.right() + labels + 140
                + panel.search_settings_button.width() + actions + 4 * panel.input_layout.horizontalSpacing())
    for width, expected in ((boundary, 1), (boundary - 1, 2), (boundary, 1)):
        panel.resize(width, 180)
        qtbot.wait(10)
        assert panel.width() == width
        assert panel._toolbar_mode == expected


def test_no_result_encoding_guidance_is_a_separate_paragraph(language):
    paragraphs = AppStrings.RESULT_EMPTY_NO_MATCH.format("needle").split("\n\n")
    assert len(paragraphs) == 3
    assert "needle" in paragraphs[0]
    assert "NFD" in paragraphs[1] and "ß" in paragraphs[1]
    assert ("인코딩" if language == "ko" else "encoding") in paragraphs[2]
