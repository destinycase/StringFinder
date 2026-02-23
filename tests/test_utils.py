"""
[test_utils.py]

이 테스트는 UI 위젯 및 일반 유틸리티 함수들의 공통 기능을 검증합니다.

- 테스트 목적:
  1. 히스토리 콤보박스, 파일 열기 등 범용적으로 사용되는 컴포넌트의 안정성 보장.

- 주요 검증 사항:
  1. `HistoryComboBox`의 항목 추가, 정렬 및 전체 삭제 로직.
  2. 운영체제별 파일 열기(`os.startfile` 등) 명령의 성공적 호출 및 예외 처리.
"""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from sf_utils.app_strings import AppStrings
from sf_utils.file_helper import open_file
from ui.widgets import HistoryComboBox


def test_history_combo_box_add_items(qtbot):
    """test_history_combo_box_add_items 함수."""
    combo = HistoryComboBox()
    qtbot.addWidget(combo)

    items = ["Item 1", "Item 2", "Item 3"]
    combo.set_history(items)

    assert combo.count() == len(items) + 1
    assert combo.itemText(0) == "Item 1"
    assert combo.itemText(combo.count() - 1) == AppStrings.HISTORY_CLEAR_ALL


def test_history_combo_box_clear_all(qtbot):
    """test_history_combo_box_clear_all 함수."""
    combo = HistoryComboBox()
    qtbot.addWidget(combo)

    combo.set_history(["Item 1"])

    signal_emitted = False

    def on_cleared():
        nonlocal signal_emitted
        signal_emitted = True

    combo.history_cleared.connect(on_cleared)

    clear_index = combo.count() - 1
    combo.activated.emit(clear_index)

    assert signal_emitted
    assert combo.count() == 0


def test_open_file_windows(tmp_path):
    """test_open_file_windows 함수."""
    file_path = tmp_path / "test.txt"
    file_path.touch()

    with patch("os.startfile") as mock_startfile:
        with patch("os.name", "nt"):
            result = open_file(str(file_path))
            assert result is True
            mock_startfile.assert_called_once_with(str(file_path))


def test_open_file_exception():
    """test_open_file_exception 함수."""
    with patch("os.startfile", side_effect=OSError("Error")):
        with patch("os.name", "nt"):
            result = open_file("invalid/path")
            assert result is False
