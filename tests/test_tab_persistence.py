import pytest
from unittest.mock import MagicMock, patch
from ui.main_window import MainWindow


@pytest.fixture
def main_window(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    return window


def test_save_order_on_search_finish(qtbot, main_window):
    """검색 완료 시 탭 순서가 저장되는지 테스트"""
    # Mock ConfigManager
    main_window.config_manager.set_tab_order = MagicMock()

    # 탭 추가 (저장 안 됨)
    tab = main_window.add_new_tab("Tab1")
    main_window.config_manager.set_tab_order.assert_not_called()

    # 검색 완료 시그널 발생 (저장되어야 함)
    # _on_search_finished_in_tab 호출 시뮬레이션
    main_window._on_search_finished_in_tab(tab)

    main_window.config_manager.set_tab_order.assert_called_once()


def test_save_order_on_tab_move(qtbot, main_window):
    """탭 이동 시 탭 순서가 저장되는지 테스트"""
    main_window.config_manager.set_tab_order = MagicMock()

    # 탭 2개 추가
    main_window.add_new_tab("Tab1")
    main_window.add_new_tab("Tab2")

    # 탭 이동 시뮬레이션
    main_window.tab_widget.tabBar().tabMoved.emit(0, 1)

    # set_tab_order 호출 확인
    assert main_window.config_manager.set_tab_order.call_count > 0


def test_save_order_on_tab_close(qtbot, main_window):
    """탭 닫기 시 탭 순서가 저장되는지 테스트"""
    main_window.config_manager.set_tab_order = MagicMock()

    # 탭 2개 추가
    main_window.add_new_tab("Tab1")
    main_window.add_new_tab("Tab2")

    # 탭 닫기
    main_window._close_tab(1)

    # set_tab_order 호출 확인
    assert main_window.config_manager.set_tab_order.call_count > 0


def test_load_all_tabs_ordering(qtbot, main_window):
    """저장된 순서대로 탭이 로드되고 누락된 탭이 뒤에 추가되는지 테스트"""
    with (
        patch.object(main_window.config_manager, "get_tab_order", return_value=["Tab2", "Tab1"]),
        patch.object(main_window.config_manager, "get_all_session_names", return_value=["Tab1", "Tab2", "Tab3"]),
        patch.object(main_window.config_manager, "load_session", return_value={"valid": True}),
    ):
        # 기존 탭 모두 제거
        for i in range(main_window.tab_widget.count()):
            main_window.tab_widget.removeTab(0)

        main_window._load_all_tabs()

        # 순서 확인: Tab2 -> Tab1 (저장된 순서) -> Tab3 (알파벳순 추가)
        assert main_window.tab_widget.tabText(0) == "Tab2"
        assert main_window.tab_widget.tabText(1) == "Tab1"
        assert main_window.tab_widget.tabText(2) == "Tab3"
