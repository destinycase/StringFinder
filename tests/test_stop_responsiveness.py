import pytest
from unittest.mock import patch
from core.worker import SearchWorker, ScanWorker
from ui.search_tab import SearchTab
from utils.app_strings import AppStrings


def test_search_worker_granular_rust_stop():
    """Verify SearchWorker (Rust path) checks is_running between folder calls."""
    paths = ["C:/dir1", "C:/dir2"]
    worker = SearchWorker({"file_list": [], "search_string": "test", "search_paths": paths, "extensions": ["txt"]})

    # Mock search_directory_fast to return results
    mock_res = {"results": [("C:/dir1/f1.txt", 1, [(1, "match")])], "skipped": []}

    with (
        patch("core.search_engine.HAS_RUST_ENGINE", True),
        patch("core.search_engine.search_directory_fast", return_value=mock_res) as mock_search,
    ):
        # 1. Stop mid-way
        worker.is_running = True
        worker.signals.results_found.connect(lambda r: worker.stop())  # Stop after first result

        worker.run()

        # search_directory_fast should be called only once if stopped early
        assert mock_search.call_count == 1
        assert not worker.is_running


def test_scan_worker_granular_smart_scan_stop():
    """Verify ScanWorker (Smart Scan) checks is_running between folder calls."""
    folders = ["C:/dir1", "C:/dir2"]
    worker = ScanWorker(selected_folders=folders, selected_exts=["txt"], filename_filter="", search_string="test")

    # Mock find_files_with_keyword_fast
    mock_files = [("C:/dir1/f1.txt", 100)]

    with (
        patch("core.search_engine.HAS_RUST_ENGINE", True),
        patch("core.search_engine.find_files_with_keyword_fast", return_value=mock_files) as mock_find,
    ):
        # 1. Stop mid-way
        worker.is_running = True

        def side_effect(*args, **kwargs):
            worker.stop()
            return mock_files

        mock_find.side_effect = side_effect

        worker.run()

        # find_files_with_keyword_fast should be called only once
        assert mock_find.call_count == 1
        assert not worker.is_running


@pytest.fixture
def search_tab_with_mock(qtbot, mock_config_manager):
    tab = SearchTab(mock_config_manager)
    qtbot.addWidget(tab)
    # Add some folders
    tab._add_folder_item("C:/test_dir")
    # Set search text
    tab.search_combo.setEditText("test")
    return tab


def test_search_tab_button_states_on_stop(search_tab_with_mock, qtbot):
    """Verify SearchTab search button transitions: Search -> Stop -> Stopping -> Search."""
    tab = search_tab_with_mock

    # Mock QThread and ScanWorker to avoid actual threading
    # But we DON'T mock _setup_search_worker because it initializes tab.thread
    with patch("ui.search_tab.ScanWorker"), patch("ui.search_tab.SearchWorker"):
        # 1. Initial State
        assert tab.search_btn.text() == AppStrings.SEARCH_BTN

        # 2. Start Search
        # We need to mock QThreadPool.start to do nothing
        with patch("PySide6.QtCore.QThreadPool.start"):
            tab.start_search()
            # It should have reached the "Stop" state
            assert tab.search_btn.text() == AppStrings.SEARCH_BTN_STOP

            # 3. Click Stop
            tab._stop_existing_search()
            assert tab.search_btn.text() == AppStrings.SEARCH_BTN_STOPPING
            # [3-State Button] Stopping 상태에서도 버튼은 활성화되어 있어야 함 (중복 클릭 방지는 내부 로직으로 처리)
            assert tab.search_btn.isEnabled()

            # 4. Simulate Finish
            tab._restore_search_button()
            assert tab.search_btn.text() == AppStrings.SEARCH_BTN
            assert tab.search_btn.isEnabled()


def test_search_tab_button_restoration_on_error(search_tab_with_mock):
    """Verify button is restored to 'Search' when an error occurs."""
    tab = search_tab_with_mock
    tab.search_btn.setText(AppStrings.SEARCH_BTN_STOP)
    tab._on_search_error("Test Error")
    assert tab.search_btn.text() == AppStrings.SEARCH_BTN
    assert tab.search_btn.isEnabled()
