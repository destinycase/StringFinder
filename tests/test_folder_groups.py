"""검색 폴더 그룹: 일괄 선택·부분 표시·접기·이동·저장 계약과 값만 검색 시 확장자 안내."""
import json

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants as C
from ui.panels import ExtensionFilterPanel, FilenameFilterPanel, FolderFilterPanel, FolderGroupHeader

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


def click_toggle(panel, qtbot, head):
    """실제 사용처럼 목록 위 단추 자리를 누릅니다(단추는 마우스를 목록으로 넘깁니다)."""
    viewport = panel.folder_list.viewport()
    pos = head.toggle_btn.mapTo(viewport, head.toggle_btn.rect().center())
    qtbot.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=pos)


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
    click_toggle(panel, qtbot, work)
    assert not panel.folder_list.item(row + 1).isHidden()
    assert panel.get_group_state()[1]["collapsed"] is False
    game_row, game = header(panel, "Game")
    click_toggle(panel, qtbot, game)
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


def test_group_checkbox_lines_up_with_ungrouped_folder_checkboxes(panel):
    loose = panel.folder_list.itemWidget(panel.folder_list.item(0))
    _, game = header(panel, "Game")
    # 펼치기 단추는 그룹 이름 오른쪽에 있어 체크 위치를 밀지 않습니다.
    assert game.checkbox.x() == loose.checkbox.x()
    # 펼치기 단추는 체크 상자와 이름 사이에 있습니다(7.1.22).
    assert game.checkbox.x() < game.toggle_btn.x() < game.name_label.x() < game.count_label.x()
    # 개수는 그룹 이름 바로 오른쪽에 붙습니다(7.4.9).
    _, work = header(panel, "Work")
    for item in (game, work):
        gap = item.count_label.x() - item.name_label.geometry().right()
        assert 0 < gap <= item.layout().spacing() + 1


@pytest.mark.parametrize("panel_type", [FolderFilterPanel, ExtensionFilterPanel, FilenameFilterPanel])
def test_empty_list_guidance_box_sits_above_the_list(qtbot, panel_type):
    from ui.styles import UIStyles

    widget = panel_type()
    qtbot.addWidget(widget)
    widget.resize(340, 300)
    widget.show()
    qtbot.waitExposed(widget)
    listing = {FolderFilterPanel: "folder_list", ExtensionFilterPanel: "ext_list",
               FilenameFilterPanel: "filename_list"}[panel_type]
    listing = getattr(widget, listing)
    qtbot.wait(10)
    hint = listing.empty_hint
    assert hint.isVisible() and hint.parent() is widget
    # 목록 바로 위에 붙고, 목록과 같은 폭입니다.
    assert hint.geometry().bottom() < listing.geometry().top()
    assert (hint.x(), hint.width()) == (listing.x(), listing.width())
    # 스타일 테두리(1px)가 안쪽 여백에 더해집니다.
    assert hint.contentsMargins().left() >= UIStyles.GUIDE_BOX_PADDING[0]
    assert "border" in hint.styleSheet()


def test_empty_list_guidance_hides_when_items_exist(qtbot):
    widget = FilenameFilterPanel()
    qtbot.addWidget(widget)
    widget.show()
    widget.add_filename("report")
    widget.filename_list._position_actions()
    assert widget.usage_label.isHidden()
    widget.filename_list.clear()
    assert widget.usage_label.isVisible()


def test_values_only_notice_and_empty_guidance_share_width_without_overlap(qtbot):
    widget = ExtensionFilterPanel()
    qtbot.addWidget(widget)
    widget.resize(340, 300)
    widget.show()
    qtbot.waitExposed(widget)
    empty_geometry = widget.usage_label.geometry()
    widget.set_special_mode(AppStrings.SPECIAL_SEARCH_ITEMS[3])
    qtbot.wait(10)
    # 값만 검색 안내만 보이고, 빈 목록 안내와 같은 폭·같은 위쪽 자리를 씁니다.
    assert widget.usage_label.isHidden()
    notice = widget.values_only_notice.geometry()
    assert (notice.x(), notice.width(), notice.y()) == (empty_geometry.x(), empty_geometry.width(), empty_geometry.y())
    widget.set_special_mode(AppStrings.SPECIAL_SEARCH_OFF)
    assert widget.usage_label.isVisible()


def test_values_only_notice_uses_notice_box(qtbot):
    from ui.styles import UIStyles

    widget = ExtensionFilterPanel()
    qtbot.addWidget(widget)
    assert widget.values_only_notice.styleSheet() == UIStyles.STYLE_NOTICE_BOX
    assert widget.values_only_notice.contentsMargins().top() >= UIStyles.GUIDE_BOX_PADDING[1]


@pytest.mark.parametrize("session_groups", ["missing", "empty"])
def test_stale_session_without_groups_keeps_groups_from_config(qtbot, mock_config_manager, session_groups):
    """검색 뒤에 만든 그룹은 설정에만 있습니다. 정상 종료 없이 꺼져 세션이 예전 상태로 남아도
    다음 시작 때 탭 복원이 그룹을 지우면 안 됩니다(6.3.9부터 있던 문제, 7.1.3 수정)."""
    from ui.search_tab import SearchTab

    tab = SearchTab(mock_config_manager)
    qtbot.addWidget(tab)
    tab.folder_panel.restore_state(dict(FOLDERS), [])
    stale = json.loads(json.dumps(tab.get_state()))  # 예전 버전이 검색 끝에 저장한 세션: 그룹 없음
    stale[C.PAYLOAD_INPUTS].pop(C.STATE_KEY_INPUTS_TRACKED)
    if session_groups == "missing":
        stale[C.PAYLOAD_INPUTS].pop(C.CONFIG_KEY_FOLDER_GROUPS)
    tab.folder_panel.restore_state(dict(FOLDERS), json.loads(json.dumps(GROUPS)))
    tab._sync_filters_to_config()  # 그룹은 설정 파일에만 저장됨
    restored = SearchTab(mock_config_manager)
    qtbot.addWidget(restored)
    restored.load_state(stale)
    assert restored.folder_panel.get_group_state() == GROUPS
    assert restored.folder_panel.get_state() == tab.folder_panel.get_state()
    for widget in (tab, restored):
        widget.close()


def test_new_session_with_no_groups_is_not_given_groups_from_config(qtbot, mock_config_manager):
    """7.1.4부터 저장한 세션은 그룹을 비운 상태도 그대로 복원합니다(다른 탭의 그룹이 붙지 않음)."""
    from ui.search_tab import SearchTab

    tab = SearchTab(mock_config_manager)
    qtbot.addWidget(tab)
    tab.folder_panel.restore_state(dict(FOLDERS), [])
    no_groups = json.loads(json.dumps(tab.get_state()))
    tab.folder_panel.restore_state(dict(FOLDERS), json.loads(json.dumps(GROUPS)))
    tab._sync_filters_to_config()
    restored = SearchTab(mock_config_manager)
    qtbot.addWidget(restored)
    restored.load_state(no_groups)
    assert restored.folder_panel.get_group_state() == []
    for widget in (tab, restored):
        widget.close()


def test_old_session_does_not_get_groups_made_only_of_other_folders(qtbot, mock_config_manager):
    """예전 형식 세션에 설정의 그룹을 쓸 때, 그 탭에 없는 폴더로만 된 그룹은 빈 채로 붙이지 않습니다(검수 4.3)."""
    from ui.search_tab import SearchTab

    tab = SearchTab(mock_config_manager)
    qtbot.addWidget(tab)
    tab.folder_panel.restore_state({"C:/loose": True}, [])
    old = json.loads(json.dumps(tab.get_state()))
    old[C.PAYLOAD_INPUTS].pop(C.STATE_KEY_INPUTS_TRACKED)
    tab.folder_panel.restore_state(dict(FOLDERS), json.loads(json.dumps(GROUPS)))
    tab._sync_filters_to_config()
    restored = SearchTab(mock_config_manager)
    qtbot.addWidget(restored)
    restored.load_state(old)
    assert restored.folder_panel.get_group_state() == []
    for widget in (tab, restored):
        widget.close()


def test_drag_from_group_toggle_moves_group_without_toggling(panel, qtbot):
    """펼치기 단추 위에서 끌어도 그룹째 옮겨지고 펼침 상태는 그대로입니다(7.1.22)."""
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest

    listing = panel.folder_list
    _, game = header(panel, "Game")
    collapsed = game.collapsed
    viewport = listing.viewport()
    start = game.toggle_btn.mapTo(viewport, game.toggle_btn.rect().center())
    last_visible = max(r for r in range(listing.count()) if not listing.item(r).isHidden())
    end = QPoint(start.x(), listing.visualItemRect(listing.item(last_visible)).bottom() - 1)
    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(viewport, QPoint(start.x(), start.y() + 20))
    QTest.mouseMove(viewport, end)
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
    names = [g["name"] for g in panel.get_group_state()]
    assert names[-1] == "Game"
    assert header(panel, "Game")[1].collapsed == collapsed


@pytest.mark.parametrize("compact", [True, False])
def test_row_item_height_matches_widget_so_text_is_centered(panel, qtbot, compact):
    """목록 항목(선택 강조) 높이가 행 위젯과 같아 이름이 강조 영역의 가운데에 옵니다(7.6.3)."""
    panel.apply_display_density(compact)
    qtbot.wait(20)
    listing = panel.folder_list
    for row in range(listing.count()):
        item = listing.item(row)
        if item.isHidden():
            continue
        widget = listing.itemWidget(item)
        assert listing.visualItemRect(item).height() == widget.height(), row
        assert item.sizeHint().height() >= widget.minimumHeight()


def test_f2_renames_current_group_and_menu_shows_shortcut(panel, qtbot, monkeypatch):
    """F2로 고른 그룹의 이름 바꾸기 창이 뜨고, 메뉴에는 F2가 표시됩니다(7.6.3)."""
    from PySide6.QtWidgets import QInputDialog, QMenu

    header_row = next(row for row in range(panel.folder_list.count()) if panel._is_header_row(row))
    calls = []
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: (calls.append(k.get("text")) or ("Renamed", True)))
    panel.folder_list.setFocus()
    panel.folder_list.setCurrentRow(header_row)
    QTest.keyClick(panel.folder_list, Qt.Key.Key_F2)
    assert calls == ["Game"]
    assert panel._widget(header_row).text() == "Renamed"
    # 폴더 행에서는 F2가 아무것도 하지 않습니다.
    folder_row = next(row for row in range(panel.folder_list.count()) if not panel._is_header_row(row))
    panel.folder_list.setCurrentRow(folder_row)
    QTest.keyClick(panel.folder_list, Qt.Key.Key_F2)
    assert calls == ["Game"]
    # 메뉴의 이름 바꾸기 항목에 F2가 보입니다.
    shown = []

    class RecordingMenu(QMenu):
        def exec(self, *_args):
            shown.extend(self.actions())

    monkeypatch.setattr("ui.panels.QMenu", RecordingMenu)
    panel._show_context_menu(panel.folder_list.visualItemRect(panel.folder_list.item(header_row)).center())
    rename = next(a for a in shown if a.text() == AppStrings.FOLDER_GROUP_RENAME)
    assert rename.shortcut().toString() == "F2" and rename.isShortcutVisibleInContextMenu()
