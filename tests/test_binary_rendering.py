"""특수 문자가 든 결과 줄의 강조 표시가 깨지지 않는지 확인합니다."""

import os
import sys
import unittest

from PySide6.QtCore import Qt

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from ui.models import MatchDetailModel


class TestBinaryAndHighlightingFix(unittest.TestCase):
    def setUp(self):
        self.model = MatchDetailModel()

    def test_highlight_with_special_chars(self):
        file_path = "test.txt"
        matches = [("1", "This is A & B content")]
        search_text = "A & B"
        search_mode = "Normal"

        self.model.set_matches(file_path, matches, search_text=search_text, search_mode=search_mode)

        index = self.model.index(0, 0)  # 내용 컬럼 (Normal 모드 통합)
        html_result = self.model.data(index, Qt.ItemDataRole.DisplayRole)

        self.assertIn("A &amp; B", html_result)
        self.assertIn("<span", html_result)
        self.assertNotIn("&amp;amp;", html_result)


if __name__ == "__main__":
    unittest.main()
