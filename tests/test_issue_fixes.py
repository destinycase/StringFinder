import os
import sys
from pathlib import Path
import subprocess
import textwrap
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


def test_rust_monitor_thread_leak(tmp_path):
    """Isolate native shutdown and fail on search errors, leaks, or deadlock."""
    pytest.importorskip("rust_engine.sf_engine")
    target = tmp_path / "monitor.txt"
    target.write_text("needle\n", encoding="utf-8")
    code = textwrap.dedent("""
        import faulthandler
        import sys
        import threading
        import time
        import psutil
        from rust_engine import sf_engine

        faulthandler.dump_traceback_later(8)
        path = sys.argv[1]
        def values(matches):
            return [(item.line, item.content, item.offset, item.length) for item in matches]
        expected = values(sf_engine.search_file(path, "needle"))
        assert len(expected) == 1, expected
        process = psutil.Process()
        initial_threads = process.num_threads()
        for _ in range(20):
            actual = values(sf_engine.search_file(path, "needle", stop_event=threading.Event()))
            assert actual == expected, (actual, expected)
        deadline = time.monotonic() + 1
        while process.num_threads() > initial_threads and time.monotonic() < deadline:
            time.sleep(0.01)
        assert process.num_threads() <= initial_threads, (initial_threads, process.num_threads())
        faulthandler.cancel_dump_traceback_later()
        print("DONE", flush=True)
    """)
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(Path(__file__).resolve().parents[1] / "src"), env.get("PYTHONPATH")])
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", code, str(target)],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired as error:
        pytest.fail(f"Native monitor did not shut down: {error.stdout!r}\n{error.stderr!r}")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DONE" in result.stdout


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
