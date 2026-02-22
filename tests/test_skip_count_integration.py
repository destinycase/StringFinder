import time
from unittest.mock import MagicMock, patch

import pytest

from ui.search_tab import SearchTab


@pytest.fixture
def search_tab(qtbot, mock_config_manager):
    widget = SearchTab(mock_config_manager)
    qtbot.addWidget(widget)
    return widget


def test_skip_count_accumulation_from_scan_phase(search_tab, qtbot):
    """Phase 1(스캔) 단계의 스킵 카운트가 최종 결과에 반영되는지 검증"""

    # 1. 초기화 확인
    search_tab.search_panel.search_combo.setEditText("test")
    search_tab.folder_panel.add_folder("C:/Test")

    # SearchTab의 내부 상태를 직접 트리거하기 위해 start_search 대신 로직 시뮬레이션
    search_tab.skipped_files_list = []

    # 2. ScanWorker의 스킵 신호 발생 시뮬레이션
    scan_skips = [("C:/Restricted/File1.txt", "Access Denied"), ("C:/Restricted/File2.txt", "OS Error")]
    search_tab._on_skipped_found(scan_skips)

    assert len(search_tab.skipped_files_list) == 2

    # 3. SearchPhase(Phase 2)의 스킵 신호 발생 시뮬레이션
    search_skips = [("C:/Search/Error.txt", "Read Error")]
    search_tab._on_skipped_found(search_skips)

    assert len(search_tab.skipped_files_list) == 3

    # 4. 종료 요약 정보 생성 시 전체 카운트(3)가 전달되는지 확인
    with patch.object(search_tab.result_view_panel, "set_summary_info") as mock_summary:
        search_tab.total_files = 10
        search_tab.total_matches = 100
        search_tab.scan_start_time = time.time()

        # Phase 2 종료 시그널 시뮬레이션 (검색 단계에서 1개 추가로 스킵되었다고 가정)
        search_tab._on_search_finished(10, 1)

        # set_summary_info의 skip_count 인자가 3(2+1)이어야 함
        # 실제 구현에서는 _on_search_finished 내부에서 len(self.skipped_files_list)를 사용하므로 3이 됨
        mock_summary.assert_called_once()
        args, kwargs = mock_summary.call_args
        assert kwargs.get("skip_count") == 3


def test_signal_connection_in_start_search(search_tab, qtbot):
    """start_search 호출 시 ScanWorker의 skipped_found 시그널이 올바르게 연결되는지 확인"""

    search_tab.search_panel.search_combo.setEditText("test")
    search_tab.folder_panel.add_folder("C:/Test")

    with patch("core.worker.ScanWorker") as MockScanWorker:
        mock_worker = MagicMock()
        MockScanWorker.return_value = mock_worker

        # start_search 실행
        search_tab.start_search()

        # skipped_found 시그널이 _on_skipped_found에 연결되었는지 확인
        mock_worker.signals.skipped_found.connect.assert_called_with(search_tab._on_skipped_found)
