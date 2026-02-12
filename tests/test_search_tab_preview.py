import pytest
import os
import time
from PySide6.QtWidgets import QApplication

# Ensure src is in path (conftest.py usually handles this but for safety)
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from ui.search_tab import SearchTab
from utils.config_manager import ConfigManager

@pytest.fixture
def search_tab(qtbot, tmp_path):
    config_manager = ConfigManager()
    # Mock config to avoid file I/O issues or use tmp_path
    widget = SearchTab(config_manager)
    qtbot.addWidget(widget)
    return widget

def test_preview_lazy_loading_correctness(search_tab, tmp_path):
    """Verify that lazy loading correctly retrieves the target lines."""
    file_path = tmp_path / "test_file.txt"
    lines = [f"Line {i+1}\n" for i in range(100)]
    file_path.write_text("".join(lines), encoding="utf-8")
    
    # Target Line 50
    target_line = 50
    search_tab._update_preview(str(file_path), target_line)
    
    content = search_tab.preview_text.toHtml()
    
    # Check if Line 50 is present
    assert "Line 50" in content
    # Check context (Line 45 to 55)
    assert "Line 45" in content
    assert "Line 55" in content
    # Check out of context (Line 1, Line 100)
    assert "Line 1" not in content
    assert "Line 100" not in content

def test_preview_lazy_loading_performance(search_tab, tmp_path):
    """Verify performance on a large file."""
    file_path = tmp_path / "large_file.txt"
    # Create 500k lines file (~5MB+)
    with open(file_path, "w", encoding="utf-8") as f:
        for i in range(500000):
            f.write(f"Line {i+1}\n")
            
    target_line = 499000
    
    start_time = time.time()
    search_tab._update_preview(str(file_path), target_line)
    duration = time.time() - start_time
    
    # Reading to line 499,000 line-by-line is O(N), but much faster than readlines() + string allocation for all lines.
    # On a modern machine, this should be under 1-2 seconds.
    # readlines() would likely take longer and spike memory.
    
    print(f"Lazy loading duration for 500k lines: {duration:.4f}s")
    
    content = search_tab.preview_text.toHtml()
    assert f"Line {target_line}" in content
    assert f"Line {target_line-5}" in content
    
    # Assert it finished reasonably fast (e.g. < 2.0s). 
    # This might be flaky on very slow CI, but good for local verification.
    assert duration < 2.0 
