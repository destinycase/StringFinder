import pytest
import os
import sys
import tempfile
import shutil

# 프로젝트 루트의 src 디렉토리를 경로에 추가
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


@pytest.fixture
def temp_dir():
    """임시 디렉토리 생성 및 삭제 피처"""
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


@pytest.fixture
def sample_text_file(temp_dir):
    """테스트용 샘플 텍스트 파일 생성"""
    path = os.path.join(temp_dir, "test.txt")
    content = "Hello World\nPython Search\nTest Line\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path, "Search"


@pytest.fixture
def mock_config_manager(temp_dir):
    """임시 경로를 사용하는 ConfigManager 피처"""
    from utils.config_manager import ConfigManager

    os.makedirs(temp_dir, exist_ok=True)
    return ConfigManager()


@pytest.fixture
def patch_sys_meipass_missing():
    """sys._MEIPASS가 없는 환경(개발 환경) 시뮬레이션"""
    if hasattr(sys, "_MEIPASS"):
        old_val = sys._MEIPASS
        del sys._MEIPASS
        yield
        sys._MEIPASS = old_val
    else:
        yield


@pytest.fixture
def patch_sys_meipass_present():
    """sys._MEIPASS가 있는 환경(빌드 환경) 시뮬레이션"""
    old_val = getattr(sys, "_MEIPASS", None)
    sys._MEIPASS = "C:/FakeMeipass"
    yield
    if old_val is None:
        if hasattr(sys, "_MEIPASS"):
            del sys._MEIPASS
    else:
        sys._MEIPASS = old_val
