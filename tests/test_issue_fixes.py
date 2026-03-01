import os
import sys
import time
import threading
import psutil
import pytest
from unittest.mock import patch
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

# 프로젝트 경로 추가
sys.path.append(os.path.join(os.getcwd(), "src"))


def test_signal_integrity_on_error():
    """이슈 #1, #4 검증: 에러 발생 시 시그널 인자 개수 및 순서 확인"""
    from core.worker import SearchWorker
    from sf_utils.constants import Constants

    QCoreApplication.instance() or QCoreApplication(sys.argv)

    params = {
        Constants.PAYLOAD_SEARCH_PATHS: ["/some/path"],
        Constants.PAYLOAD_SEARCH_STRING: "test",
        Constants.PAYLOAD_EXTENSIONS: ["txt"],
    }

    worker = SearchWorker(params)
    signals_received: list[tuple] = []

    def on_error(msg):
        signals_received.append(("error", msg))

    def on_search_finished(found, matches, skipped):
        signals_received.append(("search_finished", found, matches, skipped))

    def on_finished():
        signals_received.append(("finished",))
        loop.quit()

    worker.signals.error.connect(on_error)
    worker.signals.search_finished.connect(on_search_finished)
    worker.signals.finished.connect(on_finished)

    loop = QEventLoop()

    with patch("core.search_engine.search_directory_fast", side_effect=RuntimeError("Mock Rust Error")):
        QTimer.singleShot(100, worker.run)
        loop.exec()

    types = [s[0] for s in signals_received]
    assert "error" in types
    assert "search_finished" in types
    assert "finished" in types

    sf_signal = next(s for s in signals_received if s[0] == "search_finished")
    assert len(sf_signal) == 4
    assert sf_signal[1:] == (0, 0, 0)


def test_rust_monitor_thread_leak():
    """이슈 #2, #3 검증: 반복 호출 후 스레드 누수 여부 확인"""
    print("\n--- Starting Thread Leak Test ---")
    try:
        from rust_engine import sf_engine  # type: ignore

        print(f"sf_engine version: {getattr(sf_engine, 'API_VERSION', 'unknown')}")
    except ImportError as e:
        print(f"Import failed: {e}")
        pytest.skip("Rust engine (sf_engine) not found")

    process = psutil.Process()
    initial_threads = process.num_threads()
    print(f"Initial threads: {initial_threads}")

    dummy_event = threading.Event()
    test_file = os.path.abspath(__file__)

    # 5번만 호출하여 행 걸림 여부 확인
    for i in range(5):
        print(f"Calling search_file {i + 1}/5...")
        try:
            # sf_engine.search_file(path, pattern, mode_bits, stop_event)
            res = sf_engine.search_file(test_file, "dummy", None, dummy_event)
            print(f"Call {i + 1} returned {len(res)} matches")
        except Exception as e:
            print(f"Call {i + 1} failed: {e}")

    print("Waiting for monitor threads to cleanup...")
    time.sleep(1)

    final_threads = process.num_threads()
    print(f"Final threads: {final_threads}")

    assert final_threads <= initial_threads + 3


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
