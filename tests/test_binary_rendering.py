"""
[test_binary_rendering.py]

이 테스트는 검색 결과가 UI에 표시될 때 이진 데이터나 특수 문자가 정상적으로 렌더링되고 하이라이팅되는지 검증합니다.

- 테스트 목적:
  1. HTML 특수 문자(&, <, > 등)가 포함된 검색 결과의 이스케이프 처리 무결성 확인.
  2. 검색어 하이라이팅 시 HTML 태그 내부의 문자가 중복 치환되는 버그 방지.

- 주요 검증 사항:
  1. `MatchDetailModel`을 통한 데이터 변환 시 HTML Entity 변환 여부.
  2. 렌더링된 결과에서 중첩된 HTML 태그가 발생하지 않는지 확인.
"""

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
