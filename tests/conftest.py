import pytest
from PySide6.QtCore import QThreadPool
import os
import sys
import tempfile
import shutil

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


@pytest.fixture(scope="session", autouse=True)
def session_worker_cleanup():
    """Ensure global worker resources are cleaned up once at the end of the session."""
    yield
    try:
        from core.worker import shutdown_global_manager, GlobalExecutor

        shutdown_global_manager()
        GlobalExecutor.shutdown(wait=True, cancel_futures=True)

        # Kill any stray python processes spawned by workers during the session
        if os.name == "nt":
            # Very aggressive, but necessary for persistent leaks on Windows
            os.system('taskkill /F /IM python.exe /T /FI "MEMUSAGE lt 5000" >nul 2>&1')
    except Exception:
        pass


@pytest.fixture(autouse=True)
def cleanup_qt_threads():
    """Ensure all global threads and processes are finished after each test to prevent hangs."""
    # Pre-test cleanup to ensure isolation from previous failed tests
    try:
        from core.system_manager import SystemManager

        SystemManager.force_cleanup()
    except Exception:
        pass

    yield

    # post-test cleanup
    try:
        # [v4.33.2 Fix] Do NOT shutdown global manager every test.
        # Shutting down and restarting Manager() on Windows is VERY slow and prone to leaks.
        # Just ensure the keyboard/Qt threads are clean.

        # Unhook all keyboard events to prevent hangs
        from core.system_manager import SystemManager

        SystemManager.force_cleanup()
    except Exception:
        pass

    # 2. Wait for global QThreadPool
    pool = QThreadPool.globalInstance()
    if pool:
        # Wait for background tasks to finish.
        # Increase timeout if many tests are running.
        pool.waitForDone(2000)

    # 3. Process pending GUI events
    from PySide6.QtWidgets import QApplication
    import gc

    app = QApplication.instance()
    if app:
        app.processEvents()

    # 4. Force garbage collection for residual UI / singleton handles
    gc.collect()


@pytest.fixture
def temp_dir():
    """Create a temporary directory and remove it after test."""
    d = tempfile.mkdtemp()
    yield d
    try:
        shutil.rmtree(d)
    except OSError:
        # On Windows, folders can be locked by background threads.
        # Fallback to ignore_errors or retry logic.
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def sample_text_file(temp_dir):
    """Create a sample text file for testing."""
    path = os.path.join(temp_dir, "test.txt")
    content = "Hello World\nPython Search\nTest Line\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path, "Search"


@pytest.fixture
def mock_config_manager(temp_dir, monkeypatch):
    """Mock ConfigManager using a temporary directory."""
    from sf_utils.config_manager import ConfigManager

    # Reset instance to prevent pollution
    ConfigManager._instance = None

    # Mock APPDATA logic
    monkeypatch.setenv("APPDATA", temp_dir)

    manager = ConfigManager()
    yield manager

    # Cleanup confirmation: stop timers and save immediately
    try:
        if manager:
            manager.stop()
    except Exception:
        pass
    ConfigManager._instance = None


@pytest.fixture
def patch_sys_meipass_missing():
    """Simulate dev environment (missing sys._MEIPASS)."""
    if hasattr(sys, "_MEIPASS"):
        old_val = getattr(sys, "_MEIPASS")
        delattr(sys, "_MEIPASS")
        yield
        setattr(sys, "_MEIPASS", old_val)
    else:
        yield


@pytest.fixture
def patch_sys_meipass_present():
    """Simulate build environment (present sys._MEIPASS)."""
    # pylint: disable=no-member
    old_val = getattr(sys, "_MEIPASS", None)
    setattr(sys, "_MEIPASS", "C:/FakeMeipass")
    yield
    if old_val is None:
        if hasattr(sys, "_MEIPASS"):
            delattr(sys, "_MEIPASS")
    else:
        setattr(sys, "_MEIPASS", old_val)
