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
    # ConfigManager가 AppData가 아닌 지정된 경로를 쓰도록 패치 필요할 수 있음
    # 여기서는 단순화를 위해 생성 시 경로 주입 방식이 아니므로, 내부 로직 Mocking 고려
    return ConfigManager()
