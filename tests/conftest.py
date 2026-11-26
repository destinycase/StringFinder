"""
[conftest.py]

이 테스트 구성 파일은 전체 프로젝트의 테스트 환경 설정 및 공통 피스처(Fixtures)를 정의합니다.

- 구성 목적:
  1. 테스트 실행 전후의 전역적인 리소스(스레드 풀, 프로세스 매니저, 임시 디렉토리) 정리 자동화.
  2. `sys._MEIPASS` 모킹 등을 통한 빌드 환경 시뮬레이션 지원.

- 주요 전역 피스처 및 훅:
  1. `session_worker_cleanup`: 각 테스트 세션 종료 시 전역 실행자 및 잔여 프로세스 강제 정리.
  2. `cleanup_qt_threads`: 각 테스트 뒤 Qt 스레드 풀 대기·이벤트 처리. 메모리 정리(gc)는 `collect_garbage_per_module`이 시험 파일마다 한 번.
  3. `mock_config_manager`: 실제 설정 파일을 오염시키지 않는 독립적인 설정 관리자 환경 제공.
"""

import multiprocessing
import os
import shutil
import sys
import tempfile
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QThreadPool

# 시험이 실제 사용자 설정 폴더(%APPDATA%\StringFinder)를 읽거나 쓰지 않게, 제품 모듈을 읽기 전에
# 설정 폴더를 실행마다 새로 만든 임시 폴더로 바꿉니다. 개별 시험은 필요하면 monkeypatch로 다시 바꿉니다.
_TEST_APPDATA = tempfile.mkdtemp(prefix="sf_test_appdata_")
os.environ["APPDATA"] = _TEST_APPDATA


def _remove_test_appdata():
    # 열린 로그 파일이 있으면 Windows에서 폴더를 지우지 못하므로 먼저 닫습니다.
    import logging

    for log in [logging.getLogger()] + [logging.getLogger(n) for n in list(logging.root.manager.loggerDict)]:
        for handler in list(getattr(log, "handlers", [])):
            if isinstance(handler, logging.FileHandler):
                handler.close()
                log.removeHandler(handler)
    shutil.rmtree(_TEST_APPDATA, ignore_errors=True)


def _remove_stale_test_appdata(max_age_seconds=6 * 3600):
    """이전 실행이 지우지 못하고 남긴 시험용 설정 폴더를 정리합니다(동시에 도는 실행을 건드리지 않게 오래된 것만)."""
    import glob
    import time

    pattern = os.path.join(os.path.dirname(_TEST_APPDATA), "sf_test_appdata_*")
    for path in glob.glob(pattern):
        try:
            if path != _TEST_APPDATA and time.time() - os.path.getmtime(path) > max_age_seconds:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass


_remove_stale_test_appdata()


import atexit  # noqa: E402

atexit.register(_remove_test_appdata)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


@pytest.fixture
def isolated_clipboard(monkeypatch, qapp):
    """Test copy payloads without reading or changing the OS clipboard."""
    from PySide6.QtWidgets import QApplication

    state = {"text": ""}
    clipboard = SimpleNamespace(
        setText=lambda text: state.update(text=text),
        text=lambda: state["text"],
        clear=lambda: state.update(text=""),
    )
    monkeypatch.setattr(QApplication, "clipboard", staticmethod(lambda: clipboard))
    return clipboard


@pytest.fixture(scope="session", autouse=True)
def session_worker_cleanup():
    """session_worker_cleanup 함수."""
    yield
    try:
        # 프로세스 풀(GlobalExecutor)은 제거되었습니다. 남은 자식 프로세스만 정리합니다.
        if os.name == "nt":
            # 전체 python.exe 강제 종료는 현재 pytest 프로세스까지 종료시킬 수 있으므로 금지.
            # 현재 프로세스가 만든 자식 프로세스만 안전하게 정리합니다.
            for child in multiprocessing.active_children():
                try:
                    child.terminate()
                    child.join(timeout=1.0)
                    if child.is_alive():
                        child.kill()
                        child.join(timeout=1.0)
                except Exception:
                    pass
    except Exception:
        pass


@pytest.fixture(autouse=True)
def cleanup_qt_threads():
    """cleanup_qt_threads 함수."""
    yield

    pool = QThreadPool.globalInstance()
    if pool:
        pool.waitForDone(2000)


    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app:
        app.processEvents()


@pytest.fixture(autouse=True, scope="module")
def collect_garbage_per_module():
    """시험 파일(모듈)이 끝날 때 한 번 메모리를 정리합니다.

    시험마다 gc.collect()를 돌리면 전체 시험 시간의 절반 가까이를 차지해(offscreen 85초→46초 측정),
    스레드 풀 대기와 이벤트 처리는 시험마다 두고 메모리 정리만 파일 단위로 옮겼습니다(7.7.5 검증 시간 정리).
    """
    yield
    import gc

    gc.collect()


@pytest.fixture
def temp_dir():
    """temp_dir 함수."""
    d = tempfile.mkdtemp()
    yield d
    try:
        shutil.rmtree(d)
    except OSError:
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def sample_text_file(temp_dir):
    """sample_text_file 함수."""
    path = os.path.join(temp_dir, "test.txt")
    content = "Hello World\nPython Search\nTest Line\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path, "Search"


@pytest.fixture
def mock_config_manager(temp_dir, monkeypatch):
    """mock_config_manager 함수."""
    from sf_utils.config_manager import ConfigManager

    ConfigManager._instance = None

    monkeypatch.setenv("APPDATA", temp_dir)

    manager = ConfigManager()
    yield manager

    try:
        if manager:
            manager.stop()
    except Exception:
        pass
    ConfigManager._instance = None


@pytest.fixture
def patch_sys_meipass_missing():
    """patch_sys_meipass_missing 함수."""
    if hasattr(sys, "_MEIPASS"):
        old_val = getattr(sys, "_MEIPASS")
        delattr(sys, "_MEIPASS")
        yield
        setattr(sys, "_MEIPASS", old_val)
    else:
        yield


@pytest.fixture
def patch_sys_meipass_present():
    """patch_sys_meipass_present 함수."""
    # pylint: disable=no-member
    old_val = getattr(sys, "_MEIPASS", None)
    setattr(sys, "_MEIPASS", "C:/FakeMeipass")
    yield
    if old_val is None:
        if hasattr(sys, "_MEIPASS"):
            delattr(sys, "_MEIPASS")
    else:
        setattr(sys, "_MEIPASS", old_val)


@pytest.fixture(autouse=True)
def mock_memory_usage(monkeypatch):
    """테스트 환경의 높은 메모리 사용량으로 인해 Memory Guard가 작동하는 것을 방지합니다."""
    import psutil
    from unittest.mock import MagicMock

    mock_mem = MagicMock()
    mock_mem.percent = 50.0  # 안전한 범위로 고정
    mock_mem.total = 16 * 1024**3
    mock_mem.available = 8 * 1024**3
    monkeypatch.setattr(psutil, "virtual_memory", lambda: mock_mem)
