"""
[test_sessions.py]

이 테스트는 검색 탭의 상태를 저장하고 복구하는 세션 관리 시스템의 전 과정을 검증합니다.

- 테스트 목적:
  1. 애플리케이션 종료 및 재시작 시, 이전 작업 상태(검색어, 필터, 결과 목록 등)의 완벽한 복원 보장.

- 주요 검증 사항:
  1. 탭 상태의 직렬화(Serialization) 및 역직렬화 무결성.
  2. 메인 윈도우의 탭 순서(Tab Order) 영속성 유지.
  3. 손상되거나 형식이 잘못된 세션 데이터 로드 시의 안전장치.
  4. 도킹 레이아웃(Dock Layout) 잠금 상태의 유지 및 적용.
"""

import os
import sys
from typing import cast

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PySide6.QtWidgets import QDockWidget

from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants
from ui.main_window import MainWindow
from ui.search_tab import SearchTab


def test_config_manager_session_management(mock_config_manager):
    """test_config_manager_session_management 함수."""
    test_data = {"key": "value", "nest": {"a": 1}}
    session_name = "test_session_123"

    mock_config_manager.save_session(session_name, test_data)
    session_path = os.path.join(mock_config_manager.sessions_dir, f"{session_name}.json")
    assert os.path.exists(session_path)

    loaded_data = mock_config_manager.load_session(session_name)
    assert loaded_data == test_data

    mock_config_manager.delete_session(session_name)
    assert not os.path.exists(session_path)
    assert mock_config_manager.load_session(session_name) is None


def test_config_manager_tab_order(mock_config_manager):
    """test_config_manager_tab_order 함수."""
    tab_order = ["Tab A", "Tab B", "Tab C"]
    mock_config_manager.set_tab_order(tab_order)

    assert mock_config_manager.get_tab_order() == tab_order


def test_search_tab_state_serialization(qtbot, mock_config_manager):
    """test_search_tab_state_serialization 함수."""
    tab = SearchTab(mock_config_manager)
    qtbot.addWidget(tab)

    tab.search_panel.search_combo.set_current_text("persistent_query")
    tab.filename_panel.filename_combo.set_current_text("*.py")
    tab.logs_output.setPlainText("Test Logs")

    test_results = [("path/to/file.txt", 1, [[1, "line content"]])]
    tab.result_view_panel.result_model.add_results(test_results)
    tab._on_skipped_found([("C:/restricted/file.xml", "XML 구문 오류")])

    state = tab.get_state()
    assert state["inputs"]["search"] == "persistent_query"
    assert len(state["results"]) == 1
    assert state["logs"] == "Test Logs"
    localized_reason = AppStrings.ERROR_XML_PARSE.format("XML 구문 오류")
    assert state[Constants.PAYLOAD_SKIPPED] == [["C:/restricted/file.xml", localized_reason]]

    new_tab = SearchTab(mock_config_manager)
    qtbot.addWidget(new_tab)
    new_tab.load_state(state)

    assert new_tab.search_panel.search_combo.currentText() == "persistent_query"
    assert new_tab.result_view_panel.result_model.rowCount() == 1
    assert new_tab.logs_output.toPlainText() == "Test Logs"
    assert not new_tab.result_view_panel.result_splitter.isHidden()
    assert new_tab.skipped_files_list == [("C:/restricted/file.xml", localized_reason)]
    assert not new_tab.result_view_panel.skipped_files_banner.isHidden()


def test_main_window_tab_restoration(qtbot, mock_config_manager):
    """test_main_window_tab_restoration 함수."""

    session_name = "SavedTab"
    session_data = {
        "title": session_name,
        "inputs": {"search": "restored_query"},
        "results": [],
        "logs": "Restored logs",
    }
    mock_config_manager.save_session(session_name, session_data)
    mock_config_manager.set_tab_order([session_name])

    main_win = MainWindow()
    qtbot.addWidget(main_win)

    try:
        assert main_win.tab_widget.count() == 1
        assert main_win.tab_widget.tabText(0) == session_name

        tab_widget = main_win.tab_widget.widget(0)
        assert isinstance(tab_widget, SearchTab)
        tab = cast(SearchTab, tab_widget)
        assert tab.search_panel.search_combo.currentText() == "restored_query"
        assert "Restored logs" in tab.logs_output.toPlainText()
    finally:
        main_win.cleanup()
        main_win.close()
        main_win.deleteLater()


def test_dock_widget_layout_locking(qtbot, mock_config_manager):
    """test_dock_widget_layout_locking 함수."""
    tab = SearchTab(mock_config_manager)
    qtbot.addWidget(tab)

    mock_config_manager.set_lock_dock_layout(False)
    tab._apply_lock_layout()
    assert tab.search_dock.features() & QDockWidget.DockWidgetFeature.DockWidgetMovable

    mock_config_manager.set_lock_dock_layout(True)
    tab._apply_lock_layout()

    assert tab.search_dock.features() == QDockWidget.DockWidgetFeature.NoDockWidgetFeatures


def test_search_tab_load_state_with_malformed_results(qtbot, mock_config_manager):
    tab = SearchTab(mock_config_manager)
    qtbot.addWidget(tab)

    state = {
        Constants.PAYLOAD_INPUTS: {},
        Constants.PAYLOAD_RESULTS: [
            ("C:/legacy/a.txt", 3, [(1, "a"), (2, "b"), (3, "c")]),
            [5, "b.txt", "C:/modern", "C:/modern/b.txt", [(1, "match")]],
            {"invalid": True},
            ["too", "short"],
            [999, "", "", "C:/modern/c.txt", "invalid-matches"],
        ],
        Constants.PAYLOAD_LOGS: "loaded",
    }

    tab.load_state(state)

    assert tab.result_view_panel.result_model.get_total_result_count() == 3
    assert tab.total_files == 3
    assert tab.total_matches == 1007


def test_search_tab_load_state_uses_count_for_total_matches(qtbot, mock_config_manager):
    tab = SearchTab(mock_config_manager)
    qtbot.addWidget(tab)

    truncated_matches = [(i, f"line-{i}") for i in range(1000)]
    state = {
        Constants.PAYLOAD_INPUTS: {},
        Constants.PAYLOAD_RESULTS: [[15000, "big.txt", "C:/modern", "C:/modern/big.txt", truncated_matches]],
        Constants.PAYLOAD_LOGS: "",
    }

    tab.load_state(state)

    assert tab.total_files == 1
    assert tab.total_matches == 15000
