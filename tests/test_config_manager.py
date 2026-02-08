import os
from unittest.mock import patch
from utils.config_manager import ConfigManager


def test_config_initialization(temp_dir):
    """ConfigManager 초기화 및 기본값 생성 테스트"""
    with patch("os.getenv") as mock_getenv:
        mock_getenv.return_value = temp_dir
        cm = ConfigManager()
        cm.save()  # 파일을 명시적으로 저장해야 존재함

        assert os.path.exists(os.path.join(temp_dir, "StringFinder", "config.json"))
        assert cm.config["filters"]["extensions"] == ["xml", "json", "xlsx", "xlsm", "log", "txt"]


def test_history_management(temp_dir):
    """히스토리 추가 및 저장 테스트"""
    with patch("os.getenv") as mock_getenv:
        mock_getenv.return_value = temp_dir
        cm = ConfigManager()

        cm.add_history("test search")
        assert "test search" in cm.get_history()

        # 재로드 시에도 유지되는지 확인
        cm2 = ConfigManager()
        assert "test search" in cm2.get_history()


def test_splitter_states(temp_dir):
    """슬라이더 상태 저장 및 로드 테스트"""
    with patch("os.getenv") as mock_getenv:
        mock_getenv.return_value = temp_dir
        cm = ConfigManager()

        # 가상의 QByteArray 시뮬레이션 (실제 Qt 객체 없이 텍스트로 처리)
        # ConfigManager 내부에서는 toHex().data().decode()를 기대함
        # 테스트를 위해 Mocking 없이 cm.config를 직접 조작하거나 더미 데이터 사용
        cm.config["main_splitter_state"] = "aabbcc"
        cm.save()

        states = cm.get_splitter_states()
        assert states[0] == "aabbcc"


def test_background_settings(temp_dir):
    """백그라운드 관련 신규 설정 저장 테스트"""
    from utils.config_manager import ConfigManager
    from unittest.mock import patch

    with patch.object(ConfigManager, "__init__", lambda x: None):
        cm = ConfigManager()
        cm.defaults = {"global_hotkey": "alt+shift+space", "run_at_startup": False, "theme": "Dark"}
        cm.config_dir = temp_dir
        cm.config_path = os.path.join(temp_dir, "config.json")
        cm.config = cm.defaults.copy()

        # 1. 초기값 확인
        assert cm.get_global_hotkey() == "alt+shift+space"
        assert cm.get_run_at_startup() is False

        # 2. 값 수정 및 저장
        cm.set_global_hotkey("ctrl+f12")
        cm.set_run_at_startup(True)

        # 3. 다시 로드하여 확인 (새 인스턴스 시뮬레이션)
        cm2 = ConfigManager()
        cm2.defaults = cm.defaults.copy()  # defaults 추가
        cm2.config_dir = temp_dir
        cm2.config_path = os.path.join(temp_dir, "config.json")
        cm2.config = cm2._load()

        assert cm2.get_global_hotkey() == "ctrl+f12"
        assert cm2.get_run_at_startup() is True
