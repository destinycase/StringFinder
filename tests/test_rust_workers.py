import pytest
from unittest.mock import patch

from PySide6.QtCore import QObject

# Import workers to test
from core.worker import ScanWorker, SearchWorker

# Constants to mock - Target core.search_engine because that's where they are defined and imported from
ENGINE_MODULE = "core.search_engine"


class MockSignalReceiver(QObject):
    def __init__(self):
        super().__init__()
        self.scan_results = []
        self.search_results = []
        self.scan_finished_called = False
        self.search_finished_called = False
        self.error_received = None

    def on_scan_finished(self, files):
        self.scan_results = files
        self.scan_finished_called = True

    def on_results_found(self, results):
        self.search_results.extend(results)

    def on_search_finished(self, found, skipped):
        self.search_finished_called = True

    def on_error(self, msg):
        self.error_received = msg


@pytest.fixture
def signal_receiver(qtbot):
    receiver = MockSignalReceiver()
    return receiver


def test_scan_worker_smart_scan_basic(qtbot, signal_receiver):
    """Test ScanWorker using Smart Scan (Mocked Rust Engine)"""

    # Mock return data: List of (path, size)
    mock_files = [("C:/test/file1.txt", 100), ("C:/test/file2.log", 200), ("C:/test/file3.py", 300)]

    with patch(f"{ENGINE_MODULE}.HAS_RUST_ENGINE", True):
        with patch(f"{ENGINE_MODULE}.find_files_with_keyword_fast", return_value=mock_files) as mock_find:
            # Init worker with search_string to trigger Smart Scan
            worker = ScanWorker(
                selected_folders=["C:/test"],
                selected_exts=["txt", "log", "py"],
                filename_filter="",
                search_string="keyword",
            )
            # qtbot.addWidget(worker)  # ScanWorker is not a widget

            # Connect signals
            worker.signals.scan_finished.connect(signal_receiver.on_scan_finished)
            worker.signals.error.connect(signal_receiver.on_error)

            # Run
            worker.run()

            # Verify
            assert signal_receiver.scan_finished_called
            # PySide6 signal might convert tuples to lists
            assert len(signal_receiver.scan_results) == len(mock_files)
            for i, res in enumerate(signal_receiver.scan_results):
                assert res[0] == mock_files[i][0]
                assert res[1] == mock_files[i][1]
            mock_find.assert_called_once()


def test_scan_worker_smart_scan_with_filename_filter(qtbot, signal_receiver):
    """Test ScanWorker Smart Scan with Python-side filename filtering"""

    mock_files = [("C:/test/target_file.txt", 100), ("C:/test/other_file.txt", 200), ("C:/test/target_data.log", 150)]

    with patch(f"{ENGINE_MODULE}.HAS_RUST_ENGINE", True):
        with patch(f"{ENGINE_MODULE}.find_files_with_keyword_fast", return_value=mock_files):
            # Filter for "target"
            worker = ScanWorker(
                selected_folders=["C:/test"], selected_exts=[], filename_filter="*target*", search_string="keyword"
            )
            worker.signals.scan_finished.connect(signal_receiver.on_scan_finished)

            worker.run()

            # Expect only 'target_file.txt' and 'target_data.log'
            assert len(signal_receiver.scan_results) == 2
            paths = [f[0] for f in signal_receiver.scan_results]
            assert "C:/test/target_file.txt" in paths
            assert "C:/test/target_data.log" in paths
            assert "C:/test/other_file.txt" not in paths


def test_search_worker_rust_mode(qtbot, signal_receiver):
    """Test SearchWorker in Rust Engine Mode"""

    # Mock Search Result
    # Structure based on search_directory_fast return:
    # {"results": [ (path, count, matches), ... ], "skipped": []}

    mock_results = {
        "results": [
            ("C:/test/file1.txt", 2, [(10, "found keyword"), (15, "found keyword again")]),
            ("C:/test/file2.txt", 1, [(5, "keyword here")]),
        ],
        "skipped": [],
    }

    with patch(f"{ENGINE_MODULE}.HAS_RUST_ENGINE", True):
        with patch(f"{ENGINE_MODULE}.search_directory_fast", return_value=mock_results) as mock_search:
            # SearchWorker with search_paths AND no special_mode triggers Rust path
            worker = SearchWorker(
                {
                    "file_list": [],
                    "search_string": "keyword",
                    "special_mode": None,
                    "search_paths": ["C:/test"],
                    "extensions": ["txt"],
                }
            )
            worker.signals.results_found.connect(signal_receiver.on_results_found)
            worker.signals.search_finished.connect(signal_receiver.on_search_finished)

            worker.run()

            # Verify
            assert signal_receiver.search_finished_called
            assert len(signal_receiver.search_results) == 2
            # PySide signal might convert inner tuples to lists, so we check content values
            first_res = signal_receiver.search_results[0]
            assert first_res[0] == "C:/test/file1.txt"
            assert first_res[1] == 2
            # Check matches list content
            assert first_res[2][0][0] == 10
            assert first_res[2][0][1] == "found keyword"
            assert first_res[2][1][0] == 15
            mock_search.assert_called_once()


def test_search_worker_rust_with_excel(qtbot, signal_receiver):
    """Test SearchWorker Rust path with Excel file handling (Hybrid Mode)"""

    # 1. Rust results
    mock_rust_res = {
        "results": [("C:/test/file1.txt", 1, [(1, "match")])],
        "skipped": [],
    }

    with patch(f"{ENGINE_MODULE}.HAS_RUST_ENGINE", True):
        with (
            patch(f"{ENGINE_MODULE}.search_directory_fast", return_value=mock_rust_res),
            patch("core.worker.FileScanner") as mock_scanner_class,
            patch("core.worker.SearchWorker._run_batch_search", return_value=1) as mock_batch,
        ):
            # Mock scanner to return one excel file
            mock_scanner = mock_scanner_class.return_value
            mock_scanner.scan.return_value = ["C:/test/data.xlsx"]

            worker = SearchWorker(
                {
                    "file_list": [],
                    "search_string": "keyword",
                    "special_mode": None,
                    "search_paths": ["C:/test"],
                    "extensions": ["txt", "xlsx"],  # Hybrid: txt (Rust) + xlsx (Python)
                }
            )
            worker.signals.results_found.connect(signal_receiver.on_results_found)
            worker.signals.search_finished.connect(signal_receiver.on_search_finished)

            # Re-adjust mock to emit signal
            def side_effect(*args, **kwargs):
                worker.signals.results_found.emit([("C:/test/data.xlsx", 1, [("Sheet1!A1", "keyword")])])
                return 1

            mock_batch.side_effect = side_effect

            worker.run()

            # Verify both results are received
            assert signal_receiver.search_finished_called
            # Results from Rust (1 file) + Results from Excel batch (1 file)
            # Worker emits them separately
            assert len(signal_receiver.search_results) == 2

            paths = [r[0] for r in signal_receiver.search_results]
            assert "C:/test/file1.txt" in paths
            assert "C:/test/data.xlsx" in paths
