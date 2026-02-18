import pytest
import os
import sys
from PySide6.QtCore import Qt

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from ui.search_tab import SearchTab


@pytest.fixture
def search_tab(qtbot, mock_config_manager):
    widget = SearchTab(mock_config_manager)
    qtbot.addWidget(widget)
    # Ensure resize for visibility if needed, though headless usually fine
    widget.resize(800, 600)
    return widget


def wait_for_search_finished(qtbot, search_tab, timeout=5000):
    """Wait for search_finished signal and ensure worker is stopped"""
    with qtbot.waitSignal(search_tab.worker.signals.search_finished, timeout=timeout):
        pass
    # Additional safety: wait for worker thread to actually join if needed?
    # signals.search_finished is emitted at the end of run(), so it should be fine.


class TestSystemE2E:
    def _ensure_selection(self, search_tab):
        """Helper to ensure first result is selected if auto-select failed."""
        if search_tab.match_model.rowCount() == 0 and search_tab.result_model.rowCount() > 0:
            idx = search_tab.proxy_model.index(0, 0)
            if idx.isValid():
                search_tab._show_matches_from_view(idx)

    def test_e2e_basic_workflow(self, search_tab, tmp_path, qtbot):
        """[E2E] Scenario 1: Basic Workflow (Add Folder -> Search -> Verify Result)"""
        try:
            # 1. Setup Data
            target_dir = tmp_path / "basic_data"
            target_dir.mkdir()
            (target_dir / "target.txt").write_text("Hello StringFinder", encoding="utf-8")

            # 2. Add Folder
            search_tab._add_folder_item(str(target_dir))

            # 2.5 Ensure 'txt' extension is enabled
            # The clean way is to ensure config has it, or add it via UI
            if not any(
                search_tab.ext_list.itemWidget(search_tab.ext_list.item(i)).text() == "txt"
                for i in range(search_tab.ext_list.count())
            ):
                # Add txt if missing
                search_tab.ext_edit.setText("txt")
                qtbot.mouseClick(search_tab.add_ext_btn, Qt.MouseButton.LeftButton)

            # Ensure it is checked
            for i in range(search_tab.ext_list.count()):
                w = search_tab.ext_list.itemWidget(search_tab.ext_list.item(i))
                if w.text() == "txt":
                    w.checkbox.setChecked(True)

            # 3. Input Query
            search_tab.search_combo.setEditText("StringFinder")

            # 4. Start Search
            qtbot.mouseClick(search_tab.search_btn, Qt.MouseButton.LeftButton)

            # 5. Wait for Finish
            if search_tab.worker:
                wait_for_search_finished(qtbot, search_tab)
                qtbot.wait(1000)  # Allow slots to process
            else:
                pytest.fail("Worker NOT started")

            # 6. Verify Results
            self._ensure_selection(search_tab)

            # Check File Name via get_full_data
            assert search_tab.result_model.rowCount() == 1
            file_path, matches = search_tab.result_model.get_full_data(0)
            assert "target.txt" in file_path

            # Check Match Content in Match Model (Col 1)
            assert search_tab.match_model.rowCount() == 1
            idx_content = search_tab.match_model.index(0, 1)
            content = search_tab.match_model.data(idx_content, Qt.ItemDataRole.EditRole)
            assert "Hello StringFinder" in content
        finally:
            if search_tab.worker:
                search_tab.worker.stop()

    def test_e2e_extension_filter(self, search_tab, tmp_path, qtbot):
        """[E2E] Scenario 2: Extension Filtering (.py vs .txt)"""
        try:
            target_dir = tmp_path / "filter_data"
            target_dir.mkdir()

            (target_dir / "script.py").write_text("common_keyword", encoding="utf-8")
            (target_dir / "notes.txt").write_text("common_keyword", encoding="utf-8")

            search_tab._add_folder_item(str(target_dir))
            search_tab.search_combo.setEditText("common_keyword")

            # Add 'py' and 'txt'
            for ext in ["py", "txt"]:
                search_tab.ext_edit.setText(ext)
                qtbot.mouseClick(search_tab.add_ext_btn, Qt.MouseButton.LeftButton)

            # Enable 'py' only
            for i in range(search_tab.ext_list.count()):
                item = search_tab.ext_list.item(i)
                widget = search_tab.ext_list.itemWidget(item)
                if widget.text() == "py":
                    widget.checkbox.setChecked(True)
                else:
                    widget.checkbox.setChecked(False)

            # Start Search
            qtbot.mouseClick(search_tab.search_btn, Qt.MouseButton.LeftButton)
            if search_tab.worker:
                wait_for_search_finished(qtbot, search_tab)
                qtbot.wait(1000)
            else:
                pytest.fail("Worker NOT started")

            # Verify
            self._ensure_selection(search_tab)

            # Should match 1 file (script.py)
            assert search_tab.result_model.rowCount() == 1
            file_path, matches = search_tab.result_model.get_full_data(0)
            assert "script.py" in file_path
            assert "notes.txt" not in file_path
        finally:
            if search_tab.worker:
                search_tab.worker.stop()

    def test_e2e_multi_folder(self, search_tab, tmp_path, qtbot):
        """[E2E] Scenario 3: Multi-Folder Search"""
        try:
            dir_a = tmp_path / "DirA"
            dir_b = tmp_path / "DirB"
            dir_a.mkdir()
            dir_b.mkdir()

            (dir_a / "file_a.txt").write_text("find_me", encoding="utf-8")
            (dir_b / "file_b.txt").write_text("find_me", encoding="utf-8")

            search_tab._add_folder_item(str(dir_a))
            search_tab._add_folder_item(str(dir_b))

            # Add txt
            search_tab.ext_edit.setText("txt")
            qtbot.mouseClick(search_tab.add_ext_btn, Qt.MouseButton.LeftButton)

            search_tab.search_combo.setEditText("find_me")

            qtbot.mouseClick(search_tab.search_btn, Qt.MouseButton.LeftButton)
            if search_tab.worker:
                wait_for_search_finished(qtbot, search_tab)
                qtbot.wait(1000)
            else:
                pytest.fail("Worker NOT started")

            self._ensure_selection(search_tab)
            assert search_tab.result_model.rowCount() == 2
        finally:
            if search_tab.worker:
                search_tab.worker.stop()

    def test_e2e_json_mode(self, search_tab, tmp_path, qtbot):
        """[E2E] Scenario 4: JSON Mode Search"""
        try:
            target_dir = tmp_path / "json_data"
            target_dir.mkdir()

            json_content = '{"user": "admin", "id": 123}'
            (target_dir / "data.json").write_text(json_content, encoding="utf-8")

            search_tab._add_folder_item(str(target_dir))

            # Add json extension
            search_tab.ext_edit.setText("json")
            qtbot.mouseClick(search_tab.add_ext_btn, Qt.MouseButton.LeftButton)

            # Ensure json is checked
            for i in range(search_tab.ext_list.count()):
                w = search_tab.ext_list.itemWidget(search_tab.ext_list.item(i))
                if w.text() == "json":
                    w.checkbox.setChecked(True)

            # Select JSON Mode
            # The items are ["검색 안함 (기본)", "XML (부분 일치)", "JSON (부분 일치)", ...]
            # We search for substring "JSON"
            idx = search_tab.special_search_combo.findText("JSON", Qt.MatchFlag.MatchContains)
            if idx >= 0:
                search_tab.special_search_combo.setCurrentIndex(idx)

            search_tab.search_combo.setEditText("admin")

            # Check inputs before click
            assert search_tab.folder_list.count() > 0
            assert search_tab.search_combo.currentText() == "admin"

            qtbot.mouseClick(search_tab.search_btn, Qt.MouseButton.LeftButton)

            # Wait for worker creation (since it involves scanner)
            qtbot.waitUntil(lambda: search_tab.worker is not None, timeout=5000)

            if search_tab.worker:
                wait_for_search_finished(qtbot, search_tab)
                qtbot.wait(1000)
            else:
                pytest.fail("Worker NOT started after wait")

            self._ensure_selection(search_tab)
            assert search_tab.result_model.rowCount() == 1
            idx_match = search_tab.match_model.index(0, 2)
            # JSON Value is col 2 (Position, Key, Value)
            # Just check valid data exists
            val = search_tab.match_model.data(idx_match, Qt.ItemDataRole.EditRole)
            assert val is not None
        finally:
            if search_tab.worker:
                search_tab.worker.stop()

    def test_e2e_korean_search(self, search_tab, tmp_path, qtbot):
        """[E2E] Scenario 5: Korean Unicode Search"""
        try:
            target_dir = tmp_path / "korean_data"
            target_dir.mkdir()

            (target_dir / "korean.txt").write_text("안녕하세요, 반갑습니다!", encoding="utf-8")

            search_tab._add_folder_item(str(target_dir))

            # Add txt
            search_tab.ext_edit.setText("txt")
            qtbot.mouseClick(search_tab.add_ext_btn, Qt.MouseButton.LeftButton)

            search_tab.search_combo.setEditText("반갑")

            qtbot.mouseClick(search_tab.search_btn, Qt.MouseButton.LeftButton)
            if search_tab.worker:
                wait_for_search_finished(qtbot, search_tab)
                qtbot.wait(1000)
            else:
                pytest.fail("Worker NOT started")

            self._ensure_selection(search_tab)

            assert search_tab.result_model.rowCount() == 1
            file_path, matches = search_tab.result_model.get_full_data(0)
            assert "korean.txt" in file_path

            # Check match content
            idx_content = search_tab.match_model.index(0, 1)
            content = search_tab.match_model.data(idx_content, Qt.ItemDataRole.EditRole)
            assert "반갑" in content
        finally:
            if search_tab.worker:
                search_tab.worker.stop()
