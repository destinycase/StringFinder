"""Shared list density changes presentation, never the preview or search data."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileIconProvider, QStyleOptionViewItem

from sf_utils.constants import Constants
from ui.panels import SearchOptionsPanel
from ui.result_view import ResultView


def test_search_and_stop_share_input_row(qtbot):
    panel = SearchOptionsPanel()
    qtbot.addWidget(panel)
    panel.resize(1000, 150)
    panel.show()
    row = panel.layout().itemAt(0).layout()
    assert row.indexOf(panel.search_combo) >= 0
    assert row.indexOf(panel.search_btn) >= 0
    assert row.indexOf(panel.stop_btn) >= 0
    with qtbot.waitSignal(panel.search_started):
        qtbot.mouseClick(panel.search_btn, Qt.MouseButton.LeftButton)
    panel.set_searching(True)
    assert panel.search_btn.isHidden()
    assert not panel.stop_btn.isHidden()
    with qtbot.waitSignal(panel.stop_requested):
        qtbot.mouseClick(panel.stop_btn, Qt.MouseButton.LeftButton)
    panel.set_searching(False)
    with qtbot.waitSignal(panel.search_started):
        qtbot.keyClick(panel.search_combo.lineEdit(), Qt.Key.Key_Return)


def test_compact_rows_preserve_preview_and_persist(qtbot, mock_config_manager):
    mock_config_manager.set(Constants.CONFIG_KEY_COMPACT_RESULT_ROWS, False)
    view = ResultView(QFileIconProvider(), mock_config_manager)
    qtbot.addWidget(view)
    normal_height = view.result_view.verticalHeader().defaultSectionSize()
    detail_height = view.match_view.verticalHeader().defaultSectionSize()
    font = view.result_view.font()
    preview_font = view.context_preview.font()
    splitter_state = view.result_splitter.saveState()
    mock_config_manager.set(Constants.CONFIG_KEY_COMPACT_RESULT_ROWS, True)
    view.apply_display_density()
    assert view.result_view.verticalHeader().defaultSectionSize() < normal_height
    assert view.result_delegate.compact
    assert view.result_view.font() == font
    assert view.match_view.verticalHeader().defaultSectionSize() < detail_height
    assert view.match_view.itemDelegate().compact
    assert view.context_preview.font() == preview_font
    assert view.result_splitter.saveState() == splitter_state
    assert mock_config_manager.get(Constants.CONFIG_KEY_COMPACT_RESULT_ROWS) is True
    restored = ResultView(QFileIconProvider(), mock_config_manager)
    qtbot.addWidget(restored)
    assert restored.result_delegate.compact
    assert not hasattr(restored, "compact_rows_button")
    mock_config_manager.set(Constants.CONFIG_KEY_COMPACT_RESULT_ROWS, False)
    view.apply_display_density()
    assert view.result_view.verticalHeader().defaultSectionSize() == normal_height
    assert not view.result_delegate.compact


def test_compact_highlight_fits_row(qtbot, mock_config_manager):
    from PySide6.QtGui import QStandardItem, QStandardItemModel

    view = ResultView(QFileIconProvider(), mock_config_manager)
    qtbot.addWidget(view)
    model = QStandardItemModel()
    model.appendRow(QStandardItem('<b>검색 needle</b>.xml'))
    option = QStyleOptionViewItem()
    option.font = view.result_view.font()
    view.apply_display_density()
    hint = view.result_delegate.sizeHint(option, model.index(0, 0))
    assert hint.height() <= view.result_view.verticalHeader().defaultSectionSize()


def test_settings_density_applies_to_all_lists(qtbot, mock_config_manager):
    from PySide6.QtWidgets import QGroupBox, QListWidget
    from ui.search_tab import SearchTab
    from ui.settings_dialog import SettingsDialog
    from sf_utils.app_strings import AppStrings

    tabs = [SearchTab(mock_config_manager), SearchTab(mock_config_manager)]
    for tab in tabs:
        qtbot.addWidget(tab)
        tab.folder_panel.add_folder("D:/example")
    dialog = SettingsDialog(mock_config_manager)
    qtbot.addWidget(dialog)
    assert dialog.compact_rows_combo.currentData() is True
    assert dialog.minimumWidth() >= 520
    assert dialog.minimumHeight() >= 560
    assert dialog.compact_rows_combo.width() == dialog.theme_combo.width()
    group = dialog.compact_rows_combo.parentWidget()
    assert isinstance(group, QGroupBox)
    assert group.title() == AppStrings.SETTINGS_GROUP_APPEARANCE
    for tab in tabs:
        dialog.display_density_changed.connect(tab.apply_display_density)
    for iteration, compact in enumerate((False, True, False)):
        dialog.compact_rows_combo.setCurrentIndex(dialog.compact_rows_combo.findData(compact))
        for tab in tabs:
            tab.ext_panel.add_extension(f"density_test_{iteration}")
            tab.folder_panel.add_folder(f"D:/density_test_{iteration}")
            tab.filename_panel.add_filename(f"density_test_{iteration}")
            assert tab.result_view_panel.result_delegate.compact == compact
            assert tab.result_view_panel.match_view.itemDelegate().compact == compact
            for panel in (tab.folder_panel, tab.ext_panel, tab.filename_panel):
                assert panel._compact_rows == compact
                assert panel.layout().contentsMargins().top() == (6 if compact else 15)
                for listing in panel.findChildren(QListWidget):
                    for index in range(listing.count()):
                        item = listing.item(index)
                        widget = listing.itemWidget(item)
                        assert widget.layout().contentsMargins().top() == (0 if compact else 2)
                        assert item.sizeHint() == widget.sizeHint()
