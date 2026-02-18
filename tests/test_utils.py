import os
import sys
from unittest.mock import patch

# Ensure src is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from ui.widgets import HistoryComboBox
from sf_utils.file_helper import open_file
from sf_utils.app_strings import AppStrings


def test_history_combo_box_add_items(qtbot):
    """Test adding history items to the combo box."""
    combo = HistoryComboBox()
    qtbot.addWidget(combo)

    items = ["Item 1", "Item 2", "Item 3"]
    combo.set_history(items)

    # Check count: items + "Clear All" option
    assert combo.count() == len(items) + 1
    assert combo.itemText(0) == "Item 1"
    assert combo.itemText(combo.count() - 1) == AppStrings.HISTORY_CLEAR_ALL


def test_history_combo_box_clear_all(qtbot):
    """Test clearing all history items."""
    combo = HistoryComboBox()
    qtbot.addWidget(combo)

    combo.set_history(["Item 1"])

    # Connect signal
    signal_emitted = False

    def on_cleared():
        nonlocal signal_emitted
        signal_emitted = True

    combo.history_cleared.connect(on_cleared)

    # Trigger clear (simulate activation of last item)
    clear_index = combo.count() - 1
    combo.activated.emit(clear_index)

    assert signal_emitted
    assert combo.count() == 0


def test_open_file_windows(tmp_path):
    """Test open_file on Windows (mocked)."""
    file_path = tmp_path / "test.txt"
    file_path.touch()

    with patch("os.startfile") as mock_startfile:
        # Force os.name to 'nt' for test consistency if running on non-windows (though user is on windows)
        with patch("os.name", "nt"):
            result = open_file(str(file_path))
            assert result is True
            mock_startfile.assert_called_once_with(str(file_path))


def test_open_file_exception():
    """Test open_file handles exceptions gracefully."""
    with patch("os.startfile", side_effect=OSError("Error")):
        with patch("os.name", "nt"):
            result = open_file("invalid/path")
            assert result is False
