"""
[test_tab_persistence.py]

이 테스트는 메인 윈도우 내의 탭(Tab) 순서 및 구성의 영속적인 저장을 검증합니다.

- 테스트 목적:
  1. 사용자가 변경한 탭의 위치나 추가/삭제 정보가 다음 실행 시에도 그대로 유지되는지 확인.

- 주요 검증 사항:
  1. 탭 이동(Drag & Drop) 시 순서 저장 로직 즉시 실행 여부.
  2. 검색 완료 및 탭 닫기 이벤트 시의 자동 저장 트리거 동작.
  3. 로드 시 저장된 순서와 실제 탭 구성 간의 정합성.
"""

from unittest.mock import MagicMock, patch

import pytest

from ui.main_window import MainWindow


@pytest.fixture
def main_window(qtbot, mock_config_manager):
    window = MainWindow()
    qtbot.addWidget(window)
    return window


def test_save_order_on_search_finish(qtbot, main_window):
    """검색 완료 시 탭 순서가 저장되는지 테스트"""
    main_window.config_manager.set_tab_order = MagicMock()

    # 탭 추가 (저장 안 됨)
    tab = main_window.add_new_tab("Tab1")
    main_window.config_manager.set_tab_order.assert_not_called()

    # 검색 완료 시그널 발생 (저장되어야 함)
    main_window._on_search_finished_in_tab(tab)

    main_window.config_manager.set_tab_order.assert_called_once()


def test_only_one_tab_can_start_search_at_a_time(main_window):
    first_tab = main_window.add_new_tab("Tab1")
    second_tab = main_window.add_new_tab("Tab2")

    main_window._on_tab_search_status_changed(first_tab, True)

    assert first_tab._search_allowed is True
    assert second_tab._search_allowed is False
    assert not main_window.tab_widget.tabBar().isEnabled()

    main_window._on_tab_search_status_changed(first_tab, False)

    assert first_tab._search_allowed is True
    assert second_tab._search_allowed is True
    assert main_window.tab_widget.tabBar().isEnabled()


def test_save_order_on_tab_move(qtbot, main_window):
    """탭 이동 시 탭 순서가 저장되는지 테스트"""
    main_window.config_manager.set_tab_order = MagicMock()

    # 탭 2개 추가
    main_window.add_new_tab("Tab1")
    main_window.add_new_tab("Tab2")

    # 탭 이동 시뮬레이션
    main_window.tab_widget.tabBar().tabMoved.emit(0, 1)

    assert main_window.config_manager.set_tab_order.call_count > 0


def test_save_order_on_tab_close(qtbot, main_window):
    """탭 닫기 시 탭 순서가 저장되는지 테스트"""
    main_window.config_manager.set_tab_order = MagicMock()

    # 탭 2개 추가
    main_window.add_new_tab("Tab1")
    main_window.add_new_tab("Tab2")

    # 탭 닫기
    main_window._close_tab(1)

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

        assert main_window.tab_widget.tabText(0) == "Tab2"
        assert main_window.tab_widget.tabText(1) == "Tab1"
        assert main_window.tab_widget.tabText(2) == "Tab3"
