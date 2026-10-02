"""Persistent search history is separate from filename filter state."""
from unittest.mock import patch

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox

from sf_utils.config_manager import ConfigManager
from ui.panels import SearchOptionsPanel
from ui.search_tab import SearchTab


def items(combo):
    return [combo.itemText(i) for i in range(combo.count())]


def test_saved_history_loads_without_changing_draft(qtbot, mock_config_manager):
    mock_config_manager.add_history("first")
    mock_config_manager.add_history("second")
    tab = SearchTab(mock_config_manager)
    qtbot.addWidget(tab)
    combo = tab.search_panel.search_combo
    assert items(combo) == ["second", "first"]
    assert combo.currentText() == ""
    combo.set_current_text("  unsaved draft  ")
    tab._load_histories()
    assert combo.currentText() == "  unsaved draft  "


def test_history_survives_config_reload(qtbot, mock_config_manager):
    mock_config_manager.add_history("persisted query")
    assert mock_config_manager.save_immediately()
    mock_config_manager.stop()
    ConfigManager._instance = None
    restored = ConfigManager()
    try:
        tab = SearchTab(restored)
        qtbot.addWidget(tab)
        assert items(tab.search_panel.search_combo) == ["persisted query"]
        assert tab.search_panel.search_combo.currentText() == ""
    finally:
        restored.stop()
        ConfigManager._instance = mock_config_manager


def test_history_is_recent_unique_and_limited(mock_config_manager):
    for index in range(25):
        mock_config_manager.add_history(f"query{index}")
    mock_config_manager.add_history("query10")
    history = mock_config_manager.get_history()
    assert len(history) == 20
    assert len(set(history)) == 20
    assert history[:2] == ["query10", "query24"]
    assert "query0" not in history


def test_valid_search_records_only_after_dispatch(qtbot, mock_config_manager, tmp_path):
    tab = SearchTab(mock_config_manager)
    qtbot.addWidget(tab)
    tab.search_panel.search_combo.set_current_text("  searched query  ")
    with patch.object(tab.folder_panel, "get_selected_folders", return_value=[str(tmp_path)]), \
         patch("ui.search_tab.QThreadPool.globalInstance") as pool:
        tab.start_search()
    pool.return_value.start.assert_called_once()
    assert mock_config_manager.get_history() == ["searched query"]
    assert items(tab.search_panel.search_combo) == ["searched query"]
    assert tab.search_panel.search_combo.currentText() == "  searched query  "
    assert mock_config_manager.get_filename_history() == []
    tab._liveliness_timer.stop()


@pytest.mark.parametrize("query", ["", "x" * 1001, "no folders"])
def test_rejected_search_does_not_record(qtbot, mock_config_manager, query):
    tab = SearchTab(mock_config_manager)
    qtbot.addWidget(tab)
    tab.search_panel.search_combo.set_current_text(query)
    with patch("ui.search_tab.QMessageBox.warning"), \
         patch.object(tab.folder_panel, "get_selected_folders", return_value=[]), \
         patch.object(tab, "_setup_search_worker") as setup:
        tab.start_search()
    setup.assert_not_called()
    assert not mock_config_manager.get_history()


def test_dispatch_failure_is_not_recorded(qtbot, mock_config_manager, tmp_path):
    tab = SearchTab(mock_config_manager)
    qtbot.addWidget(tab)
    tab.search_panel.search_combo.set_current_text("failed query")
    with patch.object(tab.folder_panel, "get_selected_folders", return_value=[str(tmp_path)]), \
         patch("ui.search_tab.QThreadPool.globalInstance") as pool, \
         patch("ui.search_tab.QMessageBox.warning"):
        pool.return_value.start.side_effect = RuntimeError("dispatch failed")
        tab.start_search()
    assert not mock_config_manager.get_history()
    tab._liveliness_timer.stop()


def test_popup_refreshes_shared_history_without_losing_input(qtbot, mock_config_manager):
    panel = SearchOptionsPanel(config_manager=mock_config_manager)
    qtbot.addWidget(panel)
    panel.search_combo.set_current_text("draft")
    mock_config_manager.add_history("other tab query")
    with patch.object(QComboBox, "showPopup"):
        panel.search_combo.showPopup()
    assert items(panel.search_combo) == ["other tab query"]
    assert panel.search_combo.currentText() == "draft"


def test_enter_does_not_insert_unaccepted_history(qtbot):
    panel = SearchOptionsPanel()
    qtbot.addWidget(panel)
    panel.search_combo.set_current_text("unaccepted")
    qtbot.keyClick(panel.search_combo.lineEdit(), Qt.Key.Key_Return)
    assert items(panel.search_combo) == []


def test_bad_saved_items_are_not_offered(qtbot):
    panel = SearchOptionsPanel()
    qtbot.addWidget(panel)
    panel.search_combo.load_history([None, {}, "", "x" * 1001, "valid", "valid"])
    assert items(panel.search_combo) == ["valid"]
