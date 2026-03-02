"""
[test_search_tab_log_filters.py]

이 테스트는 UI 로그 출력창의 레벨별 필터링 기능을 검증합니다.

- 테스트 목적:
  1. 사용자가 선택한 로그 레벨(INFO, DEBUG, ERROR 등)에 따라 실시간 로그 출력이 정확히 제어되는지 확인.

- 주요 검증 사항:
  1. 기본 로그 레벨(INFO) 설정 준수.
  2. 체크박스 조작에 따른 실시간 로그 가시성 업데이트.
  3. 로그 데이터 버퍼와 UI 표시 간의 일치성.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from ui.search_tab import SearchTab


def test_log_filter_defaults_info_only(qtbot, mock_config_manager):
    tab = SearchTab(mock_config_manager)
    qtbot.addWidget(tab)

    assert tab._log_level_checkboxes["INFO"].isChecked() is True
    assert tab._log_level_checkboxes["DEBUG"].isChecked() is False
    assert tab._log_level_checkboxes["WARNING"].isChecked() is False
    assert tab._log_level_checkboxes["CRITICAL"].isChecked() is False


def test_log_filter_hides_and_shows_levels(qtbot, mock_config_manager):
    tab = SearchTab(mock_config_manager)
    qtbot.addWidget(tab)

    tab._on_log_message("INFO", "info line")
    tab._on_log_message("DEBUG", "debug line")
    tab._on_log_message("ERROR", "error line")

    # 3. 'ERROR' 필터 활성화 (ERROR가 독자 레벨로 분리됨)
    tab._log_level_checkboxes["INFO"].setChecked(False)
    tab._log_level_checkboxes["ERROR"].setChecked(True)
    tab._refresh_logs_output()

    visible_text = tab.logs_output.toPlainText()
    assert "error line" in visible_text
    assert "info line" not in visible_text
    assert "debug line" not in visible_text

    # 4. 전체 활성화 확인
    tab._log_level_checkboxes["INFO"].setChecked(True)
    tab._log_level_checkboxes["DEBUG"].setChecked(True)
    tab._refresh_logs_output()

    visible_text = tab.logs_output.toPlainText()
    assert "info line" in visible_text
    assert "debug line" in visible_text
    assert "error line" in visible_text

    state = tab.get_state()
    assert "debug line" in state["logs"]
    assert "error line" in state["logs"]
