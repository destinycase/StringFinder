
import os
import sys
import pytest
from unittest.mock import MagicMock

# Add src to path
sys.path.append(os.path.abspath("src"))

from core.worker import SearchWorker
from sf_utils.constants import Constants

class MockSignals:
    def __init__(self):
        self.progress_updated = MagicMock()
        self.results_found = MagicMock()
        self.skipped_found = MagicMock()
        self.search_finished = MagicMock()
        self.scan_finished = MagicMock()
        self.error = MagicMock()
        self.finished = MagicMock()
        self.scan_started = MagicMock()

@pytest.fixture
def mock_worker():
    # SearchWorker는 params 딕셔너리를 인자로 받음
    params = {
        Constants.PAYLOAD_SEARCH_PATHS: ["dummy_path"],
        Constants.PAYLOAD_SEARCH_STRING: "search_term",
        Constants.PAYLOAD_USE_COMPLEX_SEARCH: True  # Python/Batch 경로 강제 진입
    }
    worker = SearchWorker(params)
    worker.signals = MockSignals()  # type: ignore
    return worker


