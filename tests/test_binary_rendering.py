import os
import sys
import unittest
import tempfile

# Ensure src is in PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PySide6.QtCore import Qt
from ui.models import MatchDetailModel
from core.search_engine import search_in_file

# QApplication is needed for some Qt components
# [v4.33.2 Fix] Remove top-level QApplication init which breaks pytest-qt collection/session.


class TestBinaryAndHighlightingFix(unittest.TestCase):
    def setUp(self):
        self.model = MatchDetailModel()

    def test_highlight_with_special_chars(self):
        file_path = "test.txt"
        matches = [("1", "This is A & B content")]
        search_text = "A & B"
        search_mode = "Normal"

        self.model.set_matches(file_path, matches, search_text=search_text, search_mode=search_mode)

        index = self.model.index(0, 1)  # Content column
        html_result = self.model.data(index, Qt.ItemDataRole.DisplayRole)

        self.assertIn("A &amp; B", html_result)
        self.assertIn("<span", html_result)
        self.assertNotIn("&amp;amp;", html_result)

    def test_binary_file_placeholder(self):
        # Create a temporary binary file with NULL bytes
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as tmp:
            tmp.write(b"Some text\x00More binary data with KEYWORD here")
            tmp_path = tmp.name

        try:
            # Search in the binary file
            search_text = "KEYWORD"
            result = search_in_file(tmp_path, search_text)

            # (path, count, matches)
            assert result is not None
            assert len(result) == 3
            file_path, count, matches = result  # type: ignore
            self.assertEqual(count, 1)
            self.assertEqual(matches[0][1], "[이진 파일에서 1개 항목 발견]")

            # Verify UI display skip highlighting
            self.model.set_matches(tmp_path, matches, search_text=search_text)
            index = self.model.index(0, 1)
            html_result = self.model.data(index, Qt.ItemDataRole.DisplayRole)
            self.assertIn("[이진 파일에서 1개 항목 발견]", html_result)
            self.assertNotIn("<span", html_result)  # Should not be highlighted

        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)


if __name__ == "__main__":
    unittest.main()
