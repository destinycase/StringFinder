"""검색 폴더 그룹: 일괄 선택·부분 표시·접기·이동·저장 계약과 값만 검색 시 확장자 안내."""
import json

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants as C
from ui.panels import ExtensionFilterPanel, FolderFilterPanel, FolderGroupHeader

FOLDERS = {"C:/loose": True, "D:/game/a": True, "D:/game/b": False, "D:/work/x": True, "D:/work/y": True}
GROUPS = [
    {"name": "Game", "collapsed": False, "folders": ["D:/game/a", "D:/game/b"]},
    {"name": "Work", "collapsed": True, "folders": ["D:/work/x", "D:/work/y"]},
]


@pytest.fixture
def panel(qtbot):
    widget = FolderFilterPanel()
    qtbot.addWidget(widget)
    widget.restore_state(dict(FOLDERS), json.loads(json.dumps(GROUPS)))
    widget.resize(360, 260)
    widget.show()
    qtbot.waitExposed(widget)
    return widget


def header(panel, name):
    for row in range(panel.folder_list.count()):
        widget = panel.folder_list.itemWidget(panel.folder_list.item(row))
        if isinstance(widget, FolderGroupHeader) and widget.text() == name:
            return row, widget
    raise AssertionError(name)


def layout(panel):
    rows = []
    for row in range(panel.folder_list.count()):
        widget = panel.folder_list.itemWidget(panel.folder_list.item(row))
        rows.append(f"[{widget.text()}]" if isinstance(widget, FolderGroupHeader) else widget.text())
    return rows


def test_restore_keeps_ungrouped_first_and_round_trips(panel):
    assert layout(panel) == ["C:/loose", "[Game]", "D:/game/a", "D:/game/b", "[Work]", "D:/work/x", "D:/work/y"]
    assert panel.get_state() == FOLDERS
    assert panel.get_group_state() == GROUPS
    # 검색은 체크된 폴더를 목록 순서대로 합니다. 접힌 그룹도 접기는 표시만 바꿉니다.
    assert panel.get_selected_folders() == ["C:/loose", "D:/game/a", "D:/work/x", "D:/work/y"]


def test_header_shows_partial_and_counts(panel):
    _, game = header(panel, "Game")
    _, work = header(panel, "Work")
    assert game.checkbox.checkState() == Qt.CheckState.PartiallyChecked
    assert game.count_label.text() == "(1/2)"
    assert work.checkbox.checkState() == Qt.CheckState.Checked
    assert work.count_label.text() == "(2/2)"


def test_group_checkbox_selects_all_then_clears_all(panel, qtbot):
    row, game = header(panel, "Game")
    changes = []
    panel.filter_changed.connect(lambda: changes.append(True))
    qtbot.mouseClick(game.checkbox, Qt.MouseButton.LeftButton, pos=game.checkbox.rect().center())
    assert panel.get_state()["D:/game/a"] and panel.get_state()["D:/game/b"]
    _, game = header(panel, "Game")
    assert game.checkbox.checkState() == Qt.CheckState.Checked
    qtbot.mouseClick(game.checkbox, Qt.MouseButton.LeftButton, pos=game.checkbox.rect().center())
    assert not panel.get_state()["D:/game/a"] and not panel.get_state()["D:/game/b"]
    assert header(panel, "Game")[1].checkbox.checkState() == Qt.CheckState.Unchecked
    assert len(changes) == 2
    assert panel.get_state()["C:/loose"] is True  # 다른 그룹과 그룹 없는 폴더는 그대로입니다.


def test_child_toggle_updates_group_state(panel):
    row, _ = header(panel, "Game")
    child = panel.folder_list.itemWidget(panel.folder_list.item(row + 2))
    child.checkbox.setChecked(True)
    assert header(panel, "Game")[1].checkbox.checkState() == Qt.CheckState.Checked


def test_collapse_hides_children_and_is_saved(panel, qtbot):
    row, work = header(panel, "Work")
    assert panel.folder_list.item(row + 1).isHidden()
    qtbot.mouseClick(work.toggle_btn, Qt.MouseButton.LeftButton)
    assert not panel.folder_list.item(row + 1).isHidden()
    assert panel.get_group_state()[1]["collapsed"] is False
    game_row, game = header(panel, "Game")
    qtbot.mouseClick(game.toggle_btn, Qt.MouseButton.LeftButton)
    assert panel.folder_list.item(game_row + 1).isHidden()
    assert panel.get_group_state()[0]["collapsed"] is True


def test_add_rename_and_delete_group_keeps_folders(panel):
    assert panel.add_group("  New  ")
    assert layout(panel)[-1] == "[New]"
    assert not panel.add_group("   ")
    row, _ = header(panel, "Game")
    assert panel.rename_group(row, "Games")
    assert panel.get_group_state()[0]["name"] == "Games"
    row, _ = header(panel, "Games")
    panel._delete_item(panel.folder_list.item(row))
    assert layout(panel) == ["C:/loose", "D:/game/a", "D:/game/b", "[Work]", "D:/work/x", "D:/work/y", "[New]"]
    assert panel.get_state()["D:/game/b"] is False  # 체크 상태도 유지합니다.


def test_delete_button_on_header_removes_only_the_group(panel, qtbot):
    _, work = header(panel, "Work")
    qtbot.mouseClick(work.delete_btn, Qt.MouseButton.LeftButton)
    assert "[Work]" not in layout(panel)
    assert set(panel.get_state()) == set(FOLDERS)
    assert layout(panel)[:3] == ["C:/loose", "D:/work/x", "D:/work/y"]


def test_move_folder_between_groups_and_to_no_group(panel):
    assert panel.move_folder_to_group(0, header(panel, "Game")[0])
    assert panel.get_group_state()[0]["folders"] == ["D:/game/a", "D:/game/b", "C:/loose"]
    row = layout(panel).index("D:/game/a")
    assert panel.move_folder_to_group(row, -1)
    assert layout(panel)[0] == "D:/game/a"
    # 접힌 그룹으로 옮기면 그룹을 펼쳐서 옮긴 폴더가 보이게 합니다.
    row = layout(panel).index("D:/game/a")
    assert panel.move_folder_to_group(row, header(panel, "Work")[0])
    work_row, work = header(panel, "Work")
    assert not work.collapsed
    assert panel.get_group_state()[1]["folders"][-1] == "D:/game/a"


def test_dragging_a_folder_row_changes_its_group(panel):
    assert panel.folder_list.move_row(0, header(panel, "Game")[0] + 1)
    assert panel.get_group_state()[0]["folders"][0] == "C:/loose"


def test_header_moves_with_its_folders_and_never_splits_another_group(panel):
    game_row, _ = header(panel, "Game")
    # 다른 그룹 한가운데에 놓아도 그 그룹 끝(목록 끝)으로 맞춰 옮깁니다.
    assert panel.folder_list.move_row(game_row, header(panel, "Work")[0] + 1)
    assert layout(panel) == ["C:/loose", "[Work]", "D:/work/x", "D:/work/y", "[Game]", "D:/game/a", "D:/game/b"]
    panel.folder_list.setFocus()
    panel.folder_list.setCurrentRow(header(panel, "Game")[0])
    QTest.keyClick(panel.folder_list, Qt.Key.Key_Up, Qt.KeyboardModifier.AltModifier)
    assert layout(panel)[1:4] == ["[Game]", "D:/game/a", "D:/game/b"]
    # 그룹은 그룹 없는 폴더 위로 올라가지 않습니다.
    QTest.keyClick(panel.folder_list, Qt.Key.Key_Up, Qt.KeyboardModifier.AltModifier)
    assert layout(panel)[0] == "C:/loose"
    assert panel.get_selected_folders() == ["C:/loose", "D:/game/a", "D:/work/x", "D:/work/y"]


def test_add_folder_goes_to_selected_group_or_no_group(panel):
    panel.folder_list.setCurrentRow(-1)
    panel.add_folder("E:/new_loose")
    assert layout(panel)[1] == "E:/new_loose"
    panel.folder_list.setCurrentRow(header(panel, "Work")[0])
    panel.add_folder("E:/new_work")
    assert panel.get_group_state()[1]["folders"][-1] == "E:/new_work"
    assert panel.get_group_state()[1]["collapsed"] is False
    panel.add_folder("D:/game/a")  # 이미 있는 폴더는 다시 추가하지 않습니다.
    assert layout(panel).count("D:/game/a") == 1


def test_select_all_and_clear_all_cover_grouped_folders(panel):
    panel.toggle_all(True)
    assert all(panel.get_state().values())
    assert header(panel, "Game")[1].checkbox.checkState() == Qt.CheckState.Checked
    panel.toggle_all(False)
    assert not any(panel.get_state().values())


@pytest.mark.parametrize("groups", [
    None, "bad", [1, None], [{"name": ""}], [{"name": 3, "folders": []}],
    [{"name": "Ghost", "folders": ["Z:/missing", 5]}],
])
def test_old_or_invalid_group_data_falls_back_safely(qtbot, groups):
    widget = FolderFilterPanel()
    qtbot.addWidget(widget)
    widget.restore_state({"C:/one": True, "C:/two": False}, groups)
    assert widget.get_state() == {"C:/one": True, "C:/two": False}
    assert all(name.startswith("[") is False for name in layout(widget)[:2])


def test_folder_listed_in_two_groups_appears_once(qtbot):
    widget = FolderFilterPanel()
    qtbot.addWidget(widget)
    widget.restore_state({"C:/one": True}, [{"name": "A", "folders": ["C:/one"]}, {"name": "B", "folders": ["C:/one"]}])
    assert layout(widget) == ["[A]", "C:/one", "[B]"]


def test_groups_persist_in_config_and_tab_sessions(qtbot, mock_config_manager):
    from ui.search_tab import SearchTab

    tab = SearchTab(mock_config_manager)
    qtbot.addWidget(tab)
    tab.folder_panel.restore_state(dict(FOLDERS), json.loads(json.dumps(GROUPS)))
    tab.folder_panel.add_group("Later")
    filters = mock_config_manager.get_filters()
    assert filters[C.CONFIG_KEY_FOLDER_GROUPS][-1] == {"name": "Later", "collapsed": False, "folders": []}
    assert filters[C.CONFIG_KEY_FOLDER_GROUPS][:2] == GROUPS
    fresh = SearchTab(mock_config_manager)
    qtbot.addWidget(fresh)
    assert fresh.folder_panel.get_group_state() == tab.folder_panel.get_group_state()
    saved = json.loads(json.dumps(tab.get_state()))
    restored = SearchTab(mock_config_manager)
    qtbot.addWidget(restored)
    restored.load_state(saved)
    assert restored.folder_panel.get_group_state() == tab.folder_panel.get_group_state()
    assert restored.folder_panel.get_selected_folders() == tab.folder_panel.get_selected_folders()
    for widget in (tab, fresh, restored):
        widget.close()


def test_values_only_mode_shows_extension_notice(qtbot):
    widget = ExtensionFilterPanel()
    qtbot.addWidget(widget)
    widget.show()
    assert widget.values_only_notice.isHidden()
    widget.set_special_mode(AppStrings.SPECIAL_SEARCH_ITEMS[3])
    assert widget.values_only_notice.isVisible()
    assert widget.values_only_notice.text() == AppStrings.EXTENSION_FILTER_VALUES_ONLY_NOTICE
    assert not widget.ext_list.isEnabled()
    widget.set_special_mode(AppStrings.SPECIAL_SEARCH_OFF)
    assert widget.values_only_notice.isHidden()
    assert widget.ext_list.isEnabled()
