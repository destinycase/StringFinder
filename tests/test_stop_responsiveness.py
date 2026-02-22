"""
[test_stop_responsiveness.py]

이 테스트는 검색 작업 중단(Stop) 명령 시의 시스템 반응성 및 상태 복구 무결성을 검증합니다.

- 테스트 목적:
  1. 사용자가 중지 버튼을 눌렀을 때, 즉각적으로 작업을 멈추고 UI를 안전하게 초기 상태로 되돌리는지 확인.

- 주요 검증 사항:
  1. 스캔 및 검색 워커의 즉각적인 루프 탈출 및 스레드 종료.
  2. 중단 시 UI 버튼의 상태 변화(검색 -> 중단 중 -> 검색 복원) 시퀀스 정확도.
  3. 오류 상황 발생 시의 비정상적인 검색 상태 자동 해제 로직.
"""

from unittest.mock import patch

import pytest

from core.worker import ScanWorker, SearchWorker
from sf_utils.app_strings import AppStrings
from ui.search_tab import SearchTab


def test_search_worker_granular_rust_stop():
    """test_search_worker_granular_rust_stop 함수."""
    paths = ["C:/dir1", "C:/dir2"]
    worker = SearchWorker({"file_list": [], "search_string": "test", "search_paths": paths, "extensions": ["txt"]})

    mock_res = {"results": [("C:/dir1/f1.txt", 1, [(1, "match")])], "skipped": []}

    with (
        patch("core.search_engine.HAS_RUST_ENGINE", True),
        patch("core.search_engine.search_directory_fast", return_value=mock_res) as mock_search,
    ):
        # 1. 중간에 중단
        worker.is_running = True
        worker.signals.results_found.connect(lambda r: worker.stop())  # 첫 결과 이후 중단

        worker.run()

        assert mock_search.call_count == 1
        assert not worker.is_running


def test_scan_worker_granular_smart_scan_stop():
    """test_scan_worker_granular_smart_scan_stop 함수."""
    folders = ["C:/dir1", "C:/dir2"]
    worker = ScanWorker(folders, ["txt"], "", "test")

    mock_files = [("C:/dir1/f1.txt", 100)]

    with (
        patch("core.search_engine.HAS_RUST_ENGINE", True),
        patch("core.search_engine.find_files_with_keyword_fast", return_value=mock_files) as mock_find,
    ):
        # 1. 중간에 중단
        worker.is_running = True

        def side_effect(*args, **kwargs):
            worker.stop()
            return mock_files

        mock_find.side_effect = side_effect

        worker.run()

        assert mock_find.call_count == 1
        assert not worker.is_running


def test_scan_worker_report_skipped_files():
    """ScanWorker가 Rust 엔진에서 건너뛴 파일을 올바르게 보고하는지 확인."""
    worker = ScanWorker(["C:/dir1"], ["txt"], "", "test")
    mock_skipped = [("C:/dir1", "Walk error: mock failure")]
    found_files = [("C:/dir1/ok.txt", 10)]

    skipped_payloads = []
    worker.signals.skipped_found.connect(lambda items: skipped_payloads.extend(items))

    with (
        patch("core.search_engine.HAS_RUST_ENGINE", True),
        patch("core.search_engine.find_files_with_keyword_fast", return_value=(found_files, mock_skipped)) as mock_find,
        patch("core.worker.FileScanner.scan") as mock_scan,
    ):
        worker.run()

    assert mock_find.call_count == 1
    # 더 이상 폴백(FileScanner.scan)을 수행하지 않아야 함
    assert mock_scan.call_count == 0
    actual_skipped = [list(item) if isinstance(item, tuple) else item for item in skipped_payloads]
    expected_skipped = [list(item) if isinstance(item, tuple) else item for item in mock_skipped]
    assert actual_skipped == expected_skipped


@pytest.fixture
def search_tab_with_mock(qtbot, mock_config_manager):
    tab = SearchTab(mock_config_manager)
    qtbot.addWidget(tab)
    tab.show()
    # 폴더 추가
    tab.folder_panel.add_folder("C:/test_dir")
    # 검색어 설정
    tab.search_panel.search_combo.setEditText("test")
    return tab


def test_search_tab_button_states_on_stop(search_tab_with_mock, qtbot):
    """test_search_tab_button_states_on_stop 함수."""
    tab = search_tab_with_mock

    with patch("ui.search_tab.ScanWorker"), patch("ui.search_tab.SearchWorker"):
        # 1. 초기 상태
        assert tab.search_panel.search_btn.isVisible()
        assert tab.search_panel.search_btn.text() == AppStrings.SEARCH_BTN

        # 2. 검색 시작
        with patch("PySide6.QtCore.QThreadPool.start"):
            tab.start_search()
            assert not tab.search_panel.search_btn.isVisible()
            assert tab.search_panel.stop_btn.isVisible()
            assert tab.search_panel.stop_btn.text() == AppStrings.SEARCH_BTN_STOP

            # 3. 중지 클릭
            tab._stop_existing_search()

            assert tab.search_panel.stop_btn.text() == AppStrings.SEARCH_BTN_STOPPING
            assert not tab.search_panel.stop_btn.isEnabled()

            # 4. 완료 시뮬레이션
            tab._restore_search_button()

            assert tab.search_panel.search_btn.isVisible()
            assert tab.search_panel.search_btn.isEnabled()
            assert tab.search_panel.search_btn.text() == AppStrings.SEARCH_BTN
            assert not tab.search_panel.stop_btn.isVisible()


def test_search_tab_button_restoration_on_error(search_tab_with_mock):
    """오류 발생 시 버튼이 '검색'으로 복구되는지 확인."""
    tab = search_tab_with_mock

    # 강제로 검색 상태로 설정
    tab.search_panel.set_searching(True)
    assert tab.search_panel.stop_btn.isVisible()

    # 오류 콜백 이후에도 버튼 상태가 정상 복구되는지 확인한다.
    tab._on_search_error("Test Error")
    tab._on_worker_finished()

    assert tab.search_panel.search_btn.isVisible()
    assert tab.search_panel.search_btn.isEnabled()
