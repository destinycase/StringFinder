"""검색 폴더·확장자·파일명 목록의 순서 바꾸기(끌어서 옮기기, Alt+↑/↓) 계약."""
import json

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants as C
from ui.panels import DragHandle, ExtensionFilterPanel, FilenameFilterPanel, FilterItemWidget, FolderFilterPanel


def make_panel(qtbot, kind):
    if kind == "folder":
        panel = FolderFilterPanel()
        listing, add = panel.folder_list, panel.add_folder
    elif kind == "ext":
        panel = ExtensionFilterPanel()
        listing, add = panel.ext_list, panel.add_extension
    else:
        panel = FilenameFilterPanel()
        listing, add = panel.filename_list, panel.add_filename
    qtbot.addWidget(panel)
    for name, checked in (("aaa", True), ("bbb", False), ("ccc", True)):
        add(name, checked)
    panel.resize(320, 240)
    panel.show()
    qtbot.waitExposed(panel)
    return panel, listing


def rows(listing):
    widgets = [listing.itemWidget(listing.item(index)) for index in range(listing.count())]
    return [(widget.text(), widget.isChecked()) for widget in widgets]


def row_point(listing, row, x=None):
    rect = listing.visualItemRect(listing.item(row))
    return QPoint(rect.right() - 10 if x is None else x, rect.center().y())


KINDS = pytest.mark.parametrize("kind", ["folder", "ext", "filename"])


@KINDS
def test_move_row_keeps_name_check_density_and_reports_change(qtbot, kind):
    panel, listing = make_panel(qtbot, kind)
    panel.apply_display_density(False)
    changes = []
    panel.filter_changed.connect(lambda: changes.append(True))
    assert listing.move_row(0, 3)
    assert rows(listing) == [("bbb", False), ("ccc", True), ("aaa", True)]
    assert listing.currentRow() == 2
    assert changes == [True]
    moved = listing.itemWidget(listing.item(2))
    assert isinstance(moved, FilterItemWidget) and moved._compact is False
    state = panel.get_state()
    saved = state.get("extensions", state.get("filenames", state))
    assert list(saved) == ["bbb", "ccc", "aaa"]
    # 제자리 이동은 변경으로 알리지 않습니다.
    assert not listing.move_row(1, 1) and not listing.move_row(1, 2)
    assert changes == [True]


@KINDS
def test_moved_row_keeps_working_check_and_delete_actions(qtbot, kind):
    panel, listing = make_panel(qtbot, kind)
    listing.move_row(2, 0)
    moved = listing.itemWidget(listing.item(0))
    changes = []
    panel.filter_changed.connect(lambda: changes.append(True))
    moved.checkbox.setChecked(False)
    assert changes and rows(listing)[0] == ("ccc", False)
    qtbot.mouseClick(moved.delete_btn, Qt.MouseButton.LeftButton)
    assert [name for name, _ in rows(listing)] == ["aaa", "bbb"]


@KINDS
def test_drag_from_handle_moves_row_and_shows_drop_indicator(qtbot, kind):
    _, listing = make_panel(qtbot, kind)
    handle = listing.itemWidget(listing.item(0)).drag_handle
    # 손잡이는 좁은 점 무늬로, 체크박스 앞 공간을 적게 차지해야 합니다.
    assert isinstance(handle, DragHandle) and handle.width() <= 8
    assert listing.itemWidget(listing.item(0)).checkbox.x() <= 16
    assert handle.toolTip() == AppStrings.FILTER_ITEM_REORDER_TOOLTIP
    viewport = listing.viewport()
    start = handle.mapTo(viewport, handle.rect().center())
    end = row_point(listing, 2, x=start.x())
    end.setY(listing.visualItemRect(listing.item(2)).bottom() - 1)
    QTest.mousePress(handle, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, handle.rect().center())
    QTest.mouseMove(viewport, QPoint(start.x(), start.y() + 20))
    QTest.mouseMove(viewport, end)
    assert listing._drop_indicator.isVisible()
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
    assert not listing._drop_indicator.isVisible()
    assert [name for name, _ in rows(listing)] == ["bbb", "ccc", "aaa"]


@KINDS
def test_checkbox_click_toggles_without_reordering(qtbot, kind):
    _, listing = make_panel(qtbot, kind)
    checkbox = listing.itemWidget(listing.item(1)).checkbox
    qtbot.mouseClick(checkbox, Qt.MouseButton.LeftButton, pos=QPoint(8, checkbox.height() // 2))
    assert rows(listing) == [("aaa", True), ("bbb", True), ("ccc", True)]


@KINDS
def test_alt_arrow_keys_move_the_current_row(qtbot, kind):
    _, listing = make_panel(qtbot, kind)
    listing.setFocus()
    listing.setCurrentRow(0)
    QTest.keyClick(listing, Qt.Key.Key_Down, Qt.KeyboardModifier.AltModifier)
    assert [name for name, _ in rows(listing)] == ["bbb", "aaa", "ccc"]
    assert listing.currentRow() == 1
    QTest.keyClick(listing, Qt.Key.Key_Up, Qt.KeyboardModifier.AltModifier)
    assert [name for name, _ in rows(listing)] == ["aaa", "bbb", "ccc"]
    QTest.keyClick(listing, Qt.Key.Key_Up, Qt.KeyboardModifier.AltModifier)  # 맨 위에서는 그대로입니다.
    assert [name for name, _ in rows(listing)] == ["aaa", "bbb", "ccc"]


@KINDS
def test_disabled_list_cannot_be_reordered(qtbot, kind):
    _, listing = make_panel(qtbot, kind)
    listing.setEnabled(False)
    assert not listing.move_row(0, 3)
    assert [name for name, _ in rows(listing)] == ["aaa", "bbb", "ccc"]


def test_folder_order_reaches_search_and_survives_session(qtbot, mock_config_manager, tmp_path, monkeypatch):
    from ui.search_tab import SearchTab

    folders = []
    for name in ("first", "second", "third"):
        folder = tmp_path / name
        folder.mkdir()
        folders.append(str(folder))
    tab = SearchTab(mock_config_manager)
    qtbot.addWidget(tab)
    tab.folder_panel.restore_state({folder: True for folder in folders})
    tab.folder_panel.folder_list.move_row(2, 0)
    expected = [folders[2], folders[0], folders[1]]
    assert tab.folder_panel.get_selected_folders() == expected
    assert list(mock_config_manager.get_filters()[C.CONFIG_KEY_FOLDERS]) == expected
    restored = SearchTab(mock_config_manager)
    qtbot.addWidget(restored)
    restored.load_state(json.loads(json.dumps(tab.get_state())))
    assert restored.folder_panel.get_selected_folders() == expected
    restored.search_panel.search_combo.set_current_text("needle")
    captured = []
    monkeypatch.setattr(restored, "_setup_search_worker", captured.append)
    restored.start_search()
    assert captured[0][C.PAYLOAD_SEARCH_PATHS] == expected
    restored.close()
    tab.close()
