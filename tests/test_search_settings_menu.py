"""User selections stay exclusive within each group and preserve engine inputs."""
import json

import pytest
from PySide6.QtCore import QPoint, Qt

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


def _value_actions(panel):
    return [action for action in panel.value_menu.actions() if not action.isSeparator()]


def test_defaults_and_menu_order(qtbot, language):
    panel = SearchOptionsPanel()
    qtbot.addWidget(panel)
    actions = panel.search_settings_menu.actions()
    # 검색 방식이 하나로 통합되어 설정 메뉴에는 값만 검색 제목과 선택지만 있습니다.
    assert actions[0].isSeparator() and actions[0].text() == AppStrings.SPECIAL_SEARCH_LABEL
    assert panel.value_menu is panel.search_settings_menu
    assert not hasattr(panel, "profile_actions") and not hasattr(panel, "profile_menu")
    assert panel.special_mode_actions[AppStrings.SPECIAL_SEARCH_OFF].isChecked()
    assert [action.text() for action in _value_actions(panel)] == [AppStrings.SPECIAL_SEARCH_MENU_ITEMS[index] for index in (0, 3, 4, 1, 2, 5, 6, 7, 8)]
    assert panel.search_settings_button.text() == AppStrings.SEARCH_SETTINGS_TEXT_COMPACT
    assert panel.search_settings_button.icon().isNull()
    assert panel.search_settings_button.menu() is None
    assert panel.search_settings_button.accessibleName() == (
        "값만 검색: 사용 안 함" if language == "ko" else "Values only: Off"
    )
    assert panel.search_settings_label.text() == ("검색 설정" if language == "ko" else "Search settings")
    assert panel.search_settings_label.buddy() is panel.search_settings_button


@pytest.mark.parametrize("value_index", range(9))
def test_each_group_has_exactly_one_selection(qtbot, language, value_index):
    panel = SearchOptionsPanel()
    qtbot.addWidget(panel)
    mode = AppStrings.SPECIAL_SEARCH_ITEMS[value_index]
    action = panel.special_mode_actions[mode]
    action.trigger()
    action.trigger()  # Selecting the current choice cannot leave the group empty.
    assert sum(action.isChecked() for action in _value_actions(panel)) == 1
    assert panel.get_state()[C.PAYLOAD_SPECIAL_MODE] == mode
    assert C.PAYLOAD_USE_COMPLEX_SEARCH not in panel.get_state()
    assert panel.search_settings_button.accessibleName() == AppStrings.SEARCH_SETTINGS_SELECTION.format(
        AppStrings.SPECIAL_SEARCH_MENU_ITEMS[value_index]
    )
    value = AppStrings.SPECIAL_SEARCH_COMPACT_ITEMS[value_index]
    assert panel.search_settings_button.text() == (value or AppStrings.SEARCH_SETTINGS_TEXT_COMPACT)
    assert panel.search_settings_label.buddy() is panel.search_settings_button
    assert panel.search_settings_button.icon().isNull()


def test_selection_reaches_search_and_survives_session(qtbot, mock_config_manager, tmp_path, language, monkeypatch):
    tab = SearchTab(mock_config_manager)
    qtbot.addWidget(tab)
    tab.folder_panel.restore_state({str(tmp_path): True})
    tab.ext_panel.restore_state({"txt": True, "log": False})
    panel = tab.search_panel
    panel.search_combo.set_current_text("needle")
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
    assert restored.search_panel.special_mode_actions[mode].isChecked()
    captured = []
    monkeypatch.setattr(restored, "_setup_search_worker", captured.append)
    restored.start_search()
    assert C.PAYLOAD_USE_COMPLEX_SEARCH not in captured[0]
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
    assert all(not action.isEnabled() for action in _value_actions(panel))
    before = panel.get_state()
    panel.special_mode_actions[AppStrings.SPECIAL_SEARCH_ITEMS[3]].trigger()
    assert panel.get_state() == before
    panel.set_searching(False)
    assert panel.search_settings_button.isEnabled()
    panel.special_mode_actions[AppStrings.SPECIAL_SEARCH_ITEMS[3]].trigger()
    assert panel.get_state()[C.PAYLOAD_SPECIAL_MODE] == AppStrings.SPECIAL_SEARCH_ITEMS[3]


def test_requested_labels_and_input_order(qtbot, language):
    panel = SearchOptionsPanel()
    qtbot.addWidget(panel)
    row = panel.input_layout
    assert row.itemAtPosition(0, 0).widget() is panel.search_settings_label
    assert panel.search_label.text() == ("검색할 문자열" if language == "ko" else "Text to search")
    assert row.indexOf(panel.search_settings_button) < row.indexOf(panel.search_combo) < row.indexOf(panel.search_btn)
    if language == "ko":
        assert panel.value_menu.actions()[0].text() == "값만 검색"
        assert [action.text() for action in _value_actions(panel)] == [
            "사용 안 함", "JSON (부분 일치)", "JSON (정확히 일치)",
            "XML (부분 일치)", "XML (정확히 일치)",
            "Excel (부분 일치)", "Excel (정확히 일치)",
            "UE 현지화 archive (부분 일치)", "UE 현지화 archive (정확히 일치)",
        ]
    else:
        assert panel.value_menu.actions()[0].text() == "Values only"
        assert [action.text() for action in _value_actions(panel)] == [
            "Off", "JSON (contains)", "JSON (exact)", "XML (contains)", "XML (exact)",
            "Excel (contains)", "Excel (exact)",
            "UE localization archive (contains)", "UE localization archive (exact)",
        ]


@pytest.mark.parametrize("value_index", [6, 8])
@pytest.mark.parametrize("width", [600, 720, 1280])
def test_long_selection_keeps_query_and_buttons_inside_panel(qtbot, language, width, value_index):
    panel = SearchOptionsPanel()
    qtbot.addWidget(panel)
    panel.special_mode_actions[AppStrings.SPECIAL_SEARCH_ITEMS[value_index]].trigger()
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


def test_menu_works_with_keyboard(qtbot, language):
    panel = SearchOptionsPanel()
    qtbot.addWidget(panel)
    panel.show()
    menu = panel.search_settings_menu
    menu.popup(panel.mapToGlobal(QPoint(0, panel.height())))
    target = panel.special_mode_actions[AppStrings.SPECIAL_SEARCH_ITEMS[3]]  # 메뉴 두 번째 선택지
    for _ in range(4):
        if menu.activeAction() is target:
            break
        qtbot.keyClick(menu, Qt.Key.Key_Down)
    assert menu.activeAction() is target
    qtbot.keyClick(menu, Qt.Key.Key_Return)
    assert target.isChecked()
    assert not menu.isVisible()
    menu.popup(panel.mapToGlobal(QPoint(0, panel.height())))
    qtbot.keyClick(menu, Qt.Key.Key_Escape)
    assert not menu.isVisible()


@pytest.mark.parametrize("old_mode", ["끄기", "기본", "Default", "Off", "Normal", None, 123, {}, []])
def test_old_default_labels_restore_to_disabled(old_mode, language):
    assert normalize_special_search_mode(old_mode) == AppStrings.SPECIAL_SEARCH_OFF


@pytest.mark.parametrize("old_mode,index", [
    ("JSON (정확히 일치)", 4), ("XML (exact)", 2), ("Excel (contains)", 5),
    ("UE localization archive (exact)", 8), ("UE 현지화 archive (부분 일치)", 7),
])
def test_old_modes_restore_in_current_language(qtbot, old_mode, index, language):
    panel = SearchOptionsPanel()
    qtbot.addWidget(panel)
    # 예전 세션의 검색 모드 값은 오류 없이 무시합니다.
    panel.load_state({C.PAYLOAD_USE_COMPLEX_SEARCH: True, C.PAYLOAD_SPECIAL_MODE: old_mode})
    assert C.PAYLOAD_USE_COMPLEX_SEARCH not in panel.get_state()
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
    ("UE 현지화 archive (부분 일치)", 7), ("UE 현지화 archive (정확히 일치)", 8),
    ("UE localization archive (contains)", 7), ("UE localization archive (exact)", 8),
])
def test_legacy_saved_values_remain_engine_values(qtbot, language, precise, saved_mode, index):
    expected = (["사용 안함", "XML (부분 일치)", "XML (정확히 일치)",
                 "JSON (부분 일치)", "JSON (정확히 일치)", "Excel (부분 일치)", "Excel (정확히 일치)",
                 "UE 현지화 archive (부분 일치)", "UE 현지화 archive (정확히 일치)"]
                if language == "ko" else
                ["Off", "XML (contains)", "XML (exact)", "JSON (contains)", "JSON (exact)",
                 "Excel (contains)", "Excel (exact)",
                 "UE localization archive (contains)", "UE localization archive (exact)"])
    assert AppStrings.SPECIAL_SEARCH_ITEMS == expected
    panel = SearchOptionsPanel()
    qtbot.addWidget(panel)
    panel.load_state({C.PAYLOAD_USE_COMPLEX_SEARCH: precise, C.PAYLOAD_SPECIAL_MODE: saved_mode})
    state = panel.get_state()
    assert C.PAYLOAD_USE_COMPLEX_SEARCH not in state
    assert state[C.PAYLOAD_SPECIAL_MODE] == expected[index]


def test_display_resources_do_not_use_retired_names(language):
    for key in ("SPECIAL_SEARCH_LABEL", "SPECIAL_SEARCH_MENU_ITEMS", "SEARCH_SETTINGS_TEXT_COMPACT",
                "SEARCH_HELP_TEXT", "SEARCH_EDIT_PLACEHOLDER", "RESULT_EMPTY_NO_MATCH"):
        value = str(getattr(AppStrings, key))
        for retired in ("누락 방지", "특수 검색", "값 만", "Miss-prevention", "Special search"):
            assert retired not in value


def _one_row_width(panel):
    """검색줄을 한 줄로 배치하는 데 필요한 최소 패널 폭입니다.

    글자 폭은 글꼴에 따라 달라집니다(예: 글꼴이 없는 CI의 offscreen Qt). 고정 폭 대신 이 값을 기준으로 시험합니다.
    """
    margins = panel.layout().contentsMargins()
    labels = panel.search_label.sizeHint().width() + panel.search_settings_label.sizeHint().width()
    actions = max(panel.search_btn.sizeHint().width(), panel.stop_btn.sizeHint().width())
    return (margins.left() + margins.right() + labels + 140
            + panel.search_settings_button.width() + actions + 4 * panel.input_layout.horizontalSpacing())


def test_label_widths_follow_layout_after_repeated_resizing(qtbot, language):
    panel = SearchOptionsPanel()
    qtbot.addWidget(panel)
    panel.resize(1280, 180)
    panel.show()
    qtbot.wait(10)
    boundary = _one_row_width(panel)
    wide, narrow = boundary + 40, boundary - 40
    for width in (wide, narrow, wide, narrow, wide):
        panel.resize(width, 180)
        qtbot.wait(10)
        assert panel.width() == width
        if width == wide:
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
    boundary = _one_row_width(panel)
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


def test_settings_button_shows_right_arrow_without_overlapping_text(qtbot, language):
    from PySide6.QtGui import QColor
    from ui.panels import SearchSettingsButton

    panel = SearchOptionsPanel()
    qtbot.addWidget(panel)
    panel.special_mode_actions[AppStrings.SPECIAL_SEARCH_ITEMS[6]].trigger()
    panel.resize(1280, 180)
    panel.show()
    qtbot.wait(10)
    button = panel.search_settings_button
    assert isinstance(button, SearchSettingsButton)
    assert button.menu() is None
    assert f"padding-right: {SearchSettingsButton.RESERVED_RIGHT}px" in button.styleSheet()
    # 가장 긴 문구에 여백과 ▼ 자리까지 더한 고정 폭이어야 글자가 ▼와 겹치지 않습니다.
    longest = max(
        button.fontMetrics().horizontalAdvance(value or AppStrings.SEARCH_SETTINGS_TEXT_COMPACT)
        for value in AppStrings.SPECIAL_SEARCH_COMPACT_ITEMS
    )
    assert button.width() >= longest + 48 + SearchSettingsButton.RESERVED_RIGHT
    image = button.grab().toImage()
    center_x = button.width() - SearchSettingsButton.ARROW_MARGIN - SearchSettingsButton.ARROW_WIDTH // 2
    arrow_y = button.height() // 2 - 1
    above = QColor(image.pixel(center_x, arrow_y - 6)).lightness()
    inside = QColor(image.pixel(center_x, arrow_y)).lightness()
    assert abs(inside - above) > 40, "오른쪽 끝에 ▼가 그려져야 합니다"
