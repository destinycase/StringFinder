import pytest
from unittest.mock import patch
from core.worker import SearchWorker, ScanWorker
from ui.search_tab import SearchTab
from sf_utils.app_strings import AppStrings


def test_search_worker_granular_rust_stop():
    """SearchWorker (Rust 경로)가 폴더 호출 사이에서 is_running을 확인하는지 검증."""
    paths = ["C:/dir1", "C:/dir2"]
    worker = SearchWorker({"file_list": [], "search_string": "test", "search_paths": paths, "extensions": ["txt"]})

    # mock 결과를 반환하도록 search_directory_fast 모킹
    mock_res = {"results": [("C:/dir1/f1.txt", 1, [(1, "match")])], "skipped": []}

    with (
        patch("core.search_engine.HAS_RUST_ENGINE", True),
        patch("core.search_engine.search_directory_fast", return_value=mock_res) as mock_search,
    ):
        # 1. 중간에 중단
        worker.is_running = True
        worker.signals.results_found.connect(lambda r: worker.stop())  # 첫 결과 이후 중단

        worker.run()

        # 조기에 중단되었다면 search_directory_fast는 한 번만 호출되어야 함
        assert mock_search.call_count == 1
        assert not worker.is_running


def test_scan_worker_granular_smart_scan_stop():
    """ScanWorker (Smart Scan)가 폴더 호출 사이에서 is_running을 확인하는지 검증."""
    folders = ["C:/dir1", "C:/dir2"]
    # [Fix] I-5: 테스트에서도 Positional Arguments 사용하여 실제 호출과 일치시킴
    # ScanWorker(folders, exts, filename_filter, search_string)
    worker = ScanWorker(folders, ["txt"], "", "test")

    # find_files_with_keyword_fast 모킹
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

        # find_files_with_keyword_fast는 한 번만 호출되어야 함
        assert mock_find.call_count == 1
        assert not worker.is_running


@pytest.fixture
def search_tab_with_mock(qtbot, mock_config_manager):
    tab = SearchTab(mock_config_manager)
    qtbot.addWidget(tab)
    # 폴더 추가
    tab._add_folder_item("C:/test_dir")
    # 검색어 설정
    tab.search_combo.setEditText("test")
    return tab


def test_search_tab_button_states_on_stop(search_tab_with_mock, qtbot):
    """SearchTab 검색 버튼 전환 확인: 검색 -> 중지 -> 중지 중 -> 검색."""
    tab = search_tab_with_mock

    # 실제 쓰레딩을 피하기 위해 QThread와 ScanWorker 모킹
    # tab.thread를 초기화하므로 _setup_search_worker는 모킹하지 않음
    with patch("ui.search_tab.ScanWorker"), patch("ui.search_tab.SearchWorker"):
        # 1. 초기 상태
        assert tab.search_btn.text() == AppStrings.SEARCH_BTN

        # 2. 검색 시작
        # QThreadPool.start가 아무것도 하지 않도록 모킹
        with patch("PySide6.QtCore.QThreadPool.start"):
            tab.start_search()
            # "중지" 상태에 도달해야 함
            assert tab.search_btn.text() == AppStrings.SEARCH_BTN_STOP

            # 3. 중지 클릭
            tab._stop_existing_search()
            assert tab.search_btn.text() == AppStrings.SEARCH_BTN_STOPPING
            # [3-State Button] 중지 중 상태에서도 버튼은 활성화되어 있어야 함 (중복 클릭 방지는 내부 로직으로 처리)
            assert tab.search_btn.isEnabled()

            # 4. 완료 시뮬레이션
            tab._restore_search_button()
            assert tab.search_btn.text() == AppStrings.SEARCH_BTN
            assert tab.search_btn.isEnabled()


def test_search_tab_button_restoration_on_error(search_tab_with_mock):
    """오류 발생 시 버튼이 '검색'으로 복구되는지 확인."""
    tab = search_tab_with_mock
    tab.search_btn.setText(AppStrings.SEARCH_BTN_STOP)
    tab._on_search_error("Test Error")
    assert tab.search_btn.text() == AppStrings.SEARCH_BTN
    assert tab.search_btn.isEnabled()
