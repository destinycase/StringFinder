import json
import os
import re
import sys
import tempfile
import unittest

# Adjust path to include src
src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../src"))
sys.path.insert(0, src_path)

from core.search_engine import search_in_json_special  # noqa: E402
from ui.models import MatchDetailModel  # noqa: E402

# Mock QApplication requirement for models
from PySide6.QtWidgets import QApplication  # noqa: E402

# Create a global app instance if needed, though MatchDetailModel might not strictly need it
# if it doesn't use GUI elements in __init__.
# Looking at models.py, it inherits QAbstractTableModel.
# Creating QObject requires QApplication usually?
# QAbstractTableModel imports: Qt, QAbstractTableModel, QModelIndex.
# It might run without full QApplication if no event loop usage.
# But let's create one just in case.
if not QApplication.instance():
    app = QApplication(sys.argv)


class TestOptimizations(unittest.TestCase):
    def test_json_recursion_limit(self):
        print("\n[Test] JSON Recursion Limit")
        sys.setrecursionlimit(2000)
        # Create deep json exceeding 1000
        depth = 1005
        data = {"level": 0}
        current = data
        for i in range(depth):
            current["next"] = {"level": i + 1}
            current = current["next"]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tmp:
            json.dump(data, tmp)
            tmp_path = tmp.name

        try:
            # Should return SKIPPED due to depth > 1000
            res = search_in_json_special(tmp_path, "level", exact_match=False)
            print(f"Result for depth {depth}: {res}")
            self.assertTrue(isinstance(res, tuple))
            self.assertEqual(res[0], "SKIPPED")
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_regex_precompilation(self):
        print("\n[Test] Regex Precompilation")
        model = MatchDetailModel()

        # 1. Normal Mode -> Should compile
        search_text = "test_pattern"
        model.set_matches("dummy.txt", [], search_text=search_text, search_mode="Normal")

        self.assertIsNotNone(model.highlight_pattern)
        # re.escape might change it if characters need escaping, but alphanumeric usually same
        self.assertEqual(model.highlight_pattern.pattern, re.escape(search_text))
        print("Normal mode: Pattern compiled successfully.")

        # 2. Exact Match Mode -> Should be None
        model.set_matches("dummy.txt", [], search_text=search_text, search_mode="전체 일치")
        self.assertIsNone(model.highlight_pattern)
        print("Exact match mode: Pattern is None as expected.")

        # 3. Empty search -> Should be None
        model.set_matches("dummy.txt", [], search_text="", search_mode="Normal")
        self.assertIsNone(model.highlight_pattern)
        print("Empty search: Pattern is None as expected.")


if __name__ == "__main__":
    unittest.main()
