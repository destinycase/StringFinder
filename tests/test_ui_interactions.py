
import pytest
import os
import sys
from unittest.mock import MagicMock
from PySide6.QtCore import Qt

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from ui.search_tab import SearchTab
from utils.app_strings import AppStrings

# Use mock_config_manager from conftest.py
@pytest.fixture
def search_tab_fixture(qtbot, mock_config_manager):
    # Create SearchTab with mocked config manager (which uses temp dir)
    widget = SearchTab(mock_config_manager)
    qtbot.addWidget(widget)
    return widget

def test_initial_ui_state(search_tab_fixture):
    """Verify initial UI components state."""
    # Since config is empty in temp dir, history should be empty
    assert search_tab_fixture.search_combo.count() == 0
    # Search button is always enabled initially (validation happens on click)
    assert search_tab_fixture.search_btn.isEnabled() == True
    
    # Check lists
    assert search_tab_fixture.folder_list.count() == 0
    # ConfigManager has 6 default extensions
    assert search_tab_fixture.ext_list.count() == 6

def test_add_folder_updates_list(search_tab_fixture, tmp_path):
    """Verify that adding a folder updates the folder list."""
    folder_path = str(tmp_path)
    # Manually call _add_folder_item as _add_folder opens dialog
    search_tab_fixture._add_folder_item(folder_path)
    
    assert search_tab_fixture.folder_list.count() == 1
    item = search_tab_fixture.folder_list.item(0)
    widget = search_tab_fixture.folder_list.itemWidget(item)
    assert widget.text() == folder_path
    assert widget.isChecked() == True

def test_add_extension_updates_list(search_tab_fixture, qtbot):
    """Test adding extension filters."""
    # Initial count
    initial_count = search_tab_fixture.ext_list.count()
    
    # Simulate user typing "py" and clicking add
    qtbot.keyClicks(search_tab_fixture.ext_edit, "py")
    qtbot.mouseClick(search_tab_fixture.add_ext_btn, Qt.MouseButton.LeftButton)
    
    assert search_tab_fixture.ext_list.count() == initial_count + 1
    item = search_tab_fixture.ext_list.item(initial_count) # Last item
    widget = search_tab_fixture.ext_list.itemWidget(item)
    assert widget.text() == "py" # Code removes dot
    assert widget.isChecked() == True
    
    # Add another with dot
    search_tab_fixture.ext_edit.clear()
    qtbot.keyClicks(search_tab_fixture.ext_edit, ".cpp")
    qtbot.mouseClick(search_tab_fixture.add_ext_btn, Qt.MouseButton.LeftButton)
    
    assert search_tab_fixture.ext_list.count() == initial_count + 2
    item2 = search_tab_fixture.ext_list.item(initial_count + 1)
    widget2 = search_tab_fixture.ext_list.itemWidget(item2)
    assert widget2.text() == "cpp" # Code removes dot
