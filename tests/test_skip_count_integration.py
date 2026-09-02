"""
[test_skip_count_integration.py]

이 테스트는 검색 프로세스의 각 단계에서 발생하는 '스킵된 파일' 카운트의 통합 및 UI 반영을 검증합니다.

- 테스트 목적:
  1. 스캔 단계와 검색 단계에서 각각 발생하는 스킵 사유 및 개수가 유실 없이 합산되는지 확인.
  2. 최종 결과 요약(Summary)에 정확한 스킵 카운트가 표시되는지 보장.

- 주요 검증 사항:
  1. 스캔 단계의 스킵 정보(권한 오류 등) 누적.
  2. 검색 워커 종료 시 전달되는 개별 스킵 카운트와의 합산 무결성.
  3. UI 요약 정보 갱신 시그널의 인자 정확도.
"""

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

    # 2. 통합 파이프라인(SearchWorker)의 첫 번째 동작 중 스킵 신호 발생 시뮬레이션
    scan_skips = [("C:/Restricted/File1.txt", "Access Denied"), ("C:/Restricted/File2.txt", "OS Error")]
    search_tab._on_skipped_found(scan_skips)

    assert len(search_tab.skipped_files_list) == 2

    # 3. 통합 파이프라인의 후속 탐색 중 또 다른 스킵 신호 발생 시뮬레이션
    search_skips = [("C:/Search/Error.txt", "Read Error")]
    search_tab._on_skipped_found(search_skips)

    assert len(search_tab.skipped_files_list) == 3

    # 4. 종료 요약 정보 생성 시 전체 카운트(3)가 전달되는지 확인
    with patch.object(search_tab.result_view_panel, "set_summary_info") as mock_summary:
        search_tab.total_files = 10
        search_tab.total_matches = 100
        search_tab.scan_start_time = time.time()

        # 통합 검색 단계(실제 SearchWorker 실행 결과 통계 수신) 종료 시그널 시뮬레이션
        # found_count: 10, total_matches: 100, skipped_count: 1 (워커의 누적 합계)
        search_tab._on_search_finished(10, 100, 1)

        # 실시간 목록 3건과 최종 누적 합계 1건 중 큰 값인 3을 사용해야 함
        # 최종 누적 합계를 다시 더하면 동일한 스킵 항목을 중복 집계하게 됩니다.
        mock_summary.assert_called()
        args, kwargs = mock_summary.call_args
        assert kwargs.get("skip_count") == 3


def test_signal_connection_in_start_search(search_tab, qtbot):
    """start_search 호출 시 SearchWorker의 skipped_found 시그널이 올바르게 연결되는지 확인"""

    search_tab.search_panel.search_combo.setEditText("test")
    search_tab.folder_panel.add_folder("C:/Test")

    with patch("ui.search_tab.SearchWorker") as MockSearchWorker:
        mock_worker = MagicMock()
        MockSearchWorker.return_value = mock_worker

        # start_search 실행
        search_tab.start_search()

        # results_found 시그널이 _on_results_found에 연결되었는지 확인 (또는 skipped_found)
        # unified worker에서는 worker.signals.skipped_found가 연결됨
        mock_worker.signals.skipped_found.connect.assert_called_with(search_tab._on_skipped_found)
