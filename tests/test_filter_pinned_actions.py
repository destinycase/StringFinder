"""Filter actions stay fixed while names scroll horizontally and vertically."""
import pytest
from PySide6.QtCore import QPoint, Qt

from ui.panels import ExtensionFilterPanel, FilenameFilterPanel, FolderFilterPanel


@pytest.fixture(params=[FolderFilterPanel, ExtensionFilterPanel, FilenameFilterPanel])
def panel_and_list(request, qtbot):
    panel = request.param()
    qtbot.addWidget(panel)
    if isinstance(panel, FolderFilterPanel):
        listing, add = panel.folder_list, panel.add_folder
    elif isinstance(panel, ExtensionFilterPanel):
        listing, add = panel.ext_list, panel.add_extension
    else:
        listing, add = panel.filename_list, panel.add_filename
    # Keep panel ownership so density changes follow the production path.
    for index in range(40):
        add(f"{index:02d}_" + "long_filter_name_" * 40)
    listing.setFixedSize(180, 180)
    panel.resize(340, 320)
    panel.show()
    qtbot.waitUntil(lambda: listing.horizontalScrollBar().maximum() > 0)
    return panel, listing


def assert_pinned(listing, index):
    item = listing.item(index)
    widget = listing.itemWidget(item)
    button = widget.delete_btn
    assert button.isVisible()
    rect = listing.visualItemRect(item)
    position = button.mapTo(listing, QPoint(0, 0))
    viewport = listing.viewport().geometry()
    assert position.x() >= viewport.right() + 1
    assert position.x() + button.width() <= listing.width()
    expected_y = viewport.top() + rect.top() + (rect.height() - button.height()) // 2
    assert position.y() == expected_y
    return position


@pytest.mark.parametrize("compact", [True, False])
def test_pinned_delete_with_horizontal_scroll_and_resize(panel_and_list, qtbot, compact):
    panel, listing = panel_and_list
    panel.apply_display_density(compact)
    listing.doItemsLayout()
    assert listing.itemWidget(listing.item(0)).layout().contentsMargins().top() == (0 if compact else 2)
    before = assert_pinned(listing, 0)
    listing.horizontalScrollBar().setValue(listing.horizontalScrollBar().maximum())
    assert assert_pinned(listing, 0) == before
    listing.setFixedSize(260, 180)
    listing.doItemsLayout()
    after = assert_pinned(listing, 0)
    assert after.x() > before.x()
    original_second = listing.itemWidget(listing.item(1)).text()
    qtbot.mouseClick(listing.itemWidget(listing.item(0)).delete_btn, Qt.MouseButton.LeftButton)
    assert listing.count() == 39
    assert listing.itemWidget(listing.item(0)).text() == original_second
    assert len(listing._actions) == 39


def test_vertical_scroll_delete_correct_row_and_restore(panel_and_list, qtbot):
    panel, listing = panel_and_list
    listing.scrollToItem(listing.item(25))
    listing.doItemsLayout()
    assert_pinned(listing, 25)
    text = listing.itemWidget(listing.item(25)).text()
    qtbot.mouseClick(listing.itemWidget(listing.item(25)).delete_btn, Qt.MouseButton.LeftButton)
    assert listing.count() == 39
    assert text not in [listing.itemWidget(listing.item(i)).text() for i in range(listing.count())]
    panel.restore_state(["restored_name_" * 40])
    listing.doItemsLayout()
    assert listing.count() == len(listing._actions) == 1
    listing.horizontalScrollBar().setValue(listing.horizontalScrollBar().maximum())
    assert_pinned(listing, 0)
    qtbot.mouseClick(listing.itemWidget(listing.item(0)).delete_btn, Qt.MouseButton.LeftButton)
    assert listing.count() == len(listing._actions) == 0
