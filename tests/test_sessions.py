import os
import sys

# 프로젝트 루트의 src 디렉토리를 경로에 추가
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PySide6.QtWidgets import QDockWidget
from ui.main_window import MainWindow
from ui.search_tab import SearchTab


def test_config_manager_session_management(mock_config_manager):
    """ConfigManager의 세션 파일 저장, 로드, 삭제 기능을 테스트합니다."""
    test_data = {"key": "value", "nest": {"a": 1}}
    session_name = "test_session_123"

    # 1. 저장 테스트
    mock_config_manager.save_session(session_name, test_data)
    session_path = os.path.join(mock_config_manager.sessions_dir, f"{session_name}.json")
    assert os.path.exists(session_path)

    # 2. 로드 테스트
    loaded_data = mock_config_manager.load_session(session_name)
    assert loaded_data == test_data

    # 3. 삭제 테스트
    mock_config_manager.delete_session(session_name)
    assert not os.path.exists(session_path)
    assert mock_config_manager.load_session(session_name) is None


def test_config_manager_tab_order(mock_config_manager):
    """탭 순서 저장 및 로딩을 테스트합니다."""
    tab_order = ["Tab A", "Tab B", "Tab C"]
    mock_config_manager.set_tab_order(tab_order)

    assert mock_config_manager.get_tab_order() == tab_order


def test_search_tab_state_serialization(qtbot, mock_config_manager):
    """SearchTab의 상태가 딕셔너리로 잘 변환되고 다시 로드되는지 테스트합니다."""
    tab = SearchTab(mock_config_manager)
    qtbot.addWidget(tab)

    # 상태 설정
    tab.search_combo.set_current_text("persistent_query")
    tab.filename_combo.set_current_text("*.py")
    tab.logs_output.setPlainText("Test Logs")

    # 결과 데이터 모킹
    test_results = [("path/to/file.txt", 1, [[1, "line content"]])]
    tab.result_model.add_results(test_results)

    # 상태 추출
    state = tab.get_state()
    assert state["inputs"]["search"] == "persistent_query"
    assert len(state["results"]) == 1
    assert state["logs"] == "Test Logs"

    # 새로운 탭에 복원
    new_tab = SearchTab(mock_config_manager)
    qtbot.addWidget(new_tab)
    new_tab.load_state(state)

    assert new_tab.search_combo.currentText() == "persistent_query"
    assert new_tab.result_model.rowCount() == 1
    assert new_tab.logs_output.toPlainText() == "Test Logs"
    assert not new_tab.result_splitter.isHidden()


def test_main_window_tab_restoration(qtbot, mock_config_manager):
    """MainWindow가 시작 시 ConfigManager에 저장된 탭들을 잘 복원하는지 테스트합니다."""
    # 1. 이전 세션 데이터 저장
    session_name = "SavedTab"
    session_data = {
        "title": session_name,
        "inputs": {"search": "restored_query"},
        "results": [],
        "logs": "Restored logs",
    }
    mock_config_manager.save_session(session_name, session_data)
    mock_config_manager.set_tab_order([session_name])

    # 2. MainWindow 생성 (내부에서 _load_all_tabs가 호출됨)
    main_win = MainWindow()
    qtbot.addWidget(main_win)

    # ... test logic ...

    # Explicit cleanup at end of test if needed, but qtbot usually handles it.
    # However, to be absolutely sure:
    try:
        # 탭이 하나 있고 이름이 복원되었는지 확인
        assert main_win.tab_widget.count() == 1
        assert main_win.tab_widget.tabText(0) == session_name

        # 데이터가 로드되었는지 확인
        # 로그의 초기에는 로그가 포함될 수 있으므로 포함 여부만 확인
        tab = main_win.tab_widget.widget(0)
        assert tab.search_combo.currentText() == "restored_query"
        assert "Restored logs" in tab.logs_output.toPlainText()
    finally:
        main_win.cleanup()
        main_win.close()
        main_win.deleteLater()


def test_dock_widget_layout_locking(qtbot, mock_config_manager):
    """도킹 레이아웃 잠금 기능이 올바르게 작동하는지 테스트합니다."""
    tab = SearchTab(mock_config_manager)
    qtbot.addWidget(tab)

    # 1. 잠금 해제 상태 확인 (이동 기능 활성)
    mock_config_manager.set_lock_dock_layout(False)
    tab._apply_lock_layout()
    assert tab.search_dock.features() & QDockWidget.DockWidgetFeature.DockWidgetMovable

    # 2. 잠금 상태 확인 (기능 비활성)
    mock_config_manager.set_lock_dock_layout(True)
    tab._apply_lock_layout()
    # NoDockWidgetFeatures는 0이므로 비트 연산 결과가 0이어야 함
    assert tab.search_dock.features() == QDockWidget.DockWidgetFeature.NoDockWidgetFeatures
