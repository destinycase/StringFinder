"""Startup maintenance must not delay paint or race shutdown logging."""

import threading
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

from PySide6.QtWidgets import QWidget

from sf_utils.startup import StartupLogCleanup
from ui.startup import FirstPaintObserver


def test_cleanup_is_nonblocking_runs_once_and_joins():
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def cleanup():
        entered.set()
        assert release.wait(5)
        finished.set()

    failure = Mock()
    job = StartupLogCleanup(cleanup, failure)
    try:
        job.start()
        assert entered.wait(5)
        assert not finished.is_set()
        thread = job._thread
        job.start()
        assert job._thread is thread
    finally:
        release.set()
        job.stop()
    assert finished.is_set()
    assert not thread.is_alive()
    failure.assert_not_called()


def test_shutdown_before_paint_prevents_cleanup():
    cleanup = Mock()
    job = StartupLogCleanup(cleanup, Mock())
    job.stop()
    job.start()
    cleanup.assert_not_called()
    assert job._thread is None


def test_cleanup_failure_is_reported_and_joined():
    error = PermissionError("test")
    failure = Mock()
    job = StartupLogCleanup(Mock(side_effect=error), failure)
    job.start()
    job.stop()
    failure.assert_called_once_with(error)


def test_thread_start_failure_does_not_break_shutdown(monkeypatch):
    error = RuntimeError("cannot start thread")
    monkeypatch.setattr(threading.Thread, "start", Mock(side_effect=error))
    failure = Mock()
    cleanup = Mock()
    job = StartupLogCleanup(cleanup, failure)
    job.start()
    job.stop()
    failure.assert_called_once_with(error)
    cleanup.assert_not_called()


def test_first_paint_callback_runs_once_after_paint(qtbot):
    events = []

    class PaintWindow(QWidget):
        def paintEvent(self, event):
            super().paintEvent(event)
            if not events:
                events.append("paint")

    window = PaintWindow()
    qtbot.addWidget(window)
    observer = FirstPaintObserver(window, lambda: events.append("ready"))
    window.show()
    qtbot.waitUntil(lambda: events == ["paint", "ready"])
    window.repaint()
    assert events == ["paint", "ready"]
    assert observer.parent() is window


def test_real_entry_applies_theme_once_and_cleans_after_paint(tmp_path):
    code = r'''
import threading
import sf_main
import qdarktheme
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from core.system_manager import SystemManager
from ui.main_window import MainWindow
import sf_utils.single_instance
sf_utils.single_instance.ensure_single_instance = lambda: None
loads = []
original_load = qdarktheme.load_stylesheet
def load(*args, **kwargs):
    loads.append(1)
    return original_load(*args, **kwargs)
qdarktheme.load_stylesheet = load
threads = []
painted = threading.Event()
original_paint = MainWindow.paintEvent
def paint(self, event):
    original_paint(self, event)
    painted.set()
MainWindow.paintEvent = paint
def clean_logs(*args):
    if threading.current_thread() is not threading.main_thread():
        assert painted.is_set(), 'Cleanup started before first paint completed'
    threads.append(threading.current_thread())
SystemManager.cleanup_logs = clean_logs
def app_factory(*args):
    app = QApplication(*args)
    QTimer.singleShot(1000, app.quit)
    return app
sf_main.QApplication = app_factory
try:
    sf_main.main()
except SystemExit as result:
    assert result.code == 0
assert len(loads) == 1, loads
assert len(threads) == 2, threads
assert threads[0] is not threading.main_thread()
assert threads[-1] is threading.main_thread()
'''
    env = dict(os.environ, APPDATA=str(tmp_path), QT_QPA_PLATFORM="offscreen")
    env.pop("PYTEST_CURRENT_TEST", None)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    output = (result.stdout + result.stderr).decode(errors="replace")
    assert "first_paint_completed" not in output
    assert "background_log_cleanup" not in output
    assert "[시작 계측]" not in output
    assert "[Startup timing]" not in output
