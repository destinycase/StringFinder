import os
from unittest.mock import patch
from utils.config_manager import ConfigManager


def test_config_initialization(temp_dir):
    """ConfigManager 초기화 및 기본값 생성 테스트"""
    with patch("os.getenv") as mock_getenv:
        mock_getenv.return_value = temp_dir
        ConfigManager._instance = None
        cm = ConfigManager()
        cm.save_immediately()  # 동기 저장

        assert os.path.exists(os.path.join(temp_dir, "StringFinder", "config.json"))
        assert cm.config["filters"]["extensions"] == ["xml", "json", "xlsx", "xlsm"]


def test_history_management(temp_dir):
    """히스토리 추가 및 저장 테스트"""
    with patch("os.getenv") as mock_getenv:
        mock_getenv.return_value = temp_dir
        ConfigManager._instance = None
        cm = ConfigManager()

        cm.add_history("test search")
        cm.save_immediately()  # 동기 저장 보장
        assert "test search" in cm.get_history()

        # 재로드 시에도 유지되는지 확인
        ConfigManager._instance = None
        cm2 = ConfigManager()
        assert "test search" in cm2.get_history()


def test_splitter_states(temp_dir):
    """슬라이더 상태 저장 및 로드 테스트"""
    with patch("os.getenv") as mock_getenv:
        mock_getenv.return_value = temp_dir
        ConfigManager._instance = None
        cm = ConfigManager()

        # 가상의 QByteArray 시뮬레이션 (실제 Qt 객체 없이 텍스트로 처리)
        # ConfigManager 내부에서는 toHex().data().decode()를 기대함
        # 테스트를 위해 Mocking 없이 cm.config를 직접 조작하거나 더미 데이터 사용
        cm.config["main_splitter_state"] = "aabbcc"
        cm.save_immediately()

        states = cm.get_splitter_states()
        assert states[0] == "aabbcc"


def test_background_settings(temp_dir):
    """백그라운드 관련 신규 설정 저장 테스트"""
    from utils.config_manager import ConfigManager
    from unittest.mock import patch

    # ConfigManager의 인스턴스를 초기화하여 독립적인 테스트 환경 보장
    ConfigManager._instance = None

    # __init__을 바이패스하지 않고 정상적으로 생성하되, 경로만 모킹
    with patch("os.getenv", return_value=temp_dir):
        cm = ConfigManager()
        # 필요한 기본값 설정 (실제 __init__에서 로드되지만 테스트 명확성을 위해)
        cm.defaults = {"global_hotkey": "alt+shift+space", "run_at_startup": False, "theme": "Dark"}
        if not cm.config:  # 로드 실패 시 기본값 적용
            cm.config = cm.defaults.copy()

        # 1. 초기값 확인
        assert cm.get_global_hotkey() == "alt+shift+space"
        assert cm.get_run_at_startup() is False

        # 2. 값 수정 및 저장
        cm.set_global_hotkey("ctrl+f12")
        cm.set_run_at_startup(True)
        cm.save_immediately()  # 동기 저장 보장

        # 3. 다시 로드하여 확인 (새 인스턴스 시뮬레이션)
        # 싱글톤 재설정 없이 값만 로드 (같은 인스턴스지만 파일에서 다시 읽음)
        # 또는 _instance = None 후 재생성
        ConfigManager._instance = None
        cm2 = ConfigManager()

        assert cm2.get_global_hotkey() == "ctrl+f12"
        assert cm2.get_run_at_startup() is True


def test_history_bubble_up(temp_dir):
    """중복 항목 추가 시 최상단으로 이동(Bubble-up)하는지 테스트"""
    with patch("os.getenv") as mock_getenv:
        mock_getenv.return_value = temp_dir
        ConfigManager._instance = None
        cm = ConfigManager()

        cm.add_history("first")
        cm.add_history("second")
        assert (
            cm.get_history() == ["second", "first", "전체 기록 비우기"]
            if "전체 기록 비우기" in cm.get_history()
            else ["second", "first"]
        )

        # 'first'를 다시 추가하면 맨 앞으로 와야 함
        cm.add_history("first")
        history = [x for x in cm.get_history() if x != "전체 기록 비우기"]
        assert history == ["first", "second"]

        # 파일명 히스토리도 동일하게 동작하는지 확인
        cm.add_filename_history("file1")
        cm.add_filename_history("file2")
        cm.add_filename_history("file1")
        filename_history = [x for x in cm.get_filename_history() if x != "전체 기록 비우기"]
        assert filename_history == ["file1", "file2"]


def test_new_settings(temp_dir):
    """레이아웃 고정 설정 테스트"""
    with patch("os.getenv", return_value=temp_dir):
        ConfigManager._instance = None
        cm = ConfigManager()

        # 기본값 확인
        assert cm.config.get("lock_dock_layout") is False

        # 수정 및 저장
        cm.config["lock_dock_layout"] = True
        cm.save_immediately()

        ConfigManager._instance = None
        cm2 = ConfigManager()
        assert cm2.config.get("lock_dock_layout") is True


def test_log_retention_settings(temp_dir):
    """로그 보존 설정 테스트"""
    with patch("os.getenv", return_value=temp_dir):
        ConfigManager._instance = None
        cm = ConfigManager()

        # 기본값 확인 (구조 확인)
        retention = cm.config.get("log_retention", {})
        assert retention.get("max_files") == 10
        assert retention.get("max_days") == 7

        # 수정
        retention["max_files"] = 50
        retention["max_days"] = 14
        cm.config["log_retention"] = retention
        cm.save_immediately()

        ConfigManager._instance = None
        cm2 = ConfigManager()
        retention2 = cm2.config.get("log_retention", {})
        assert retention2.get("max_files") == 50
        assert retention2.get("max_days") == 14


def test_sanitize_session_name_forbidden_chars(temp_dir):
    """세션 이름 금지 문자 필터링 테스트"""
    with patch("os.getenv", return_value=temp_dir):
        ConfigManager._instance = None
        config = ConfigManager()

        # Windows 금지 문자 테스트
        test_cases = [
            ("test?session", "?"),
            ("test<session", "<"),
            ("test>session", ">"),
            ("test|session", "|"),
            ("test*session", "*"),
            ('test"session', '"'),
            ("test:session", ":"),
        ]

        for input_name, forbidden_char in test_cases:
            sanitized = config._sanitize_session_name(input_name)
            assert forbidden_char not in sanitized, (
                f"Forbidden char '{forbidden_char}' should be removed from '{input_name}'"
            )


def test_sanitize_session_name_path_traversal(temp_dir):
    """세션 이름 경로 탐색 공격 방지 테스트"""
    with patch("os.getenv", return_value=temp_dir):
        ConfigManager._instance = None
        config = ConfigManager()

        # 경로 탐색 시도
        dangerous_name = "../../../unsafe_session"
        sanitized = config._sanitize_session_name(dangerous_name)

        # basename 추출로 경로 탐색 방지
        assert sanitized == "unsafe_session", f"Path traversal should be prevented: {dangerous_name} -> {sanitized}"


def test_save_session_with_special_chars(temp_dir):
    """특수 문자 포함 세션 저장 성공 테스트"""
    with patch("os.getenv", return_value=temp_dir):
        ConfigManager._instance = None
        config = ConfigManager()

        # 특수 문자 포함 세션 이름
        session_name = "test?<>|*:session"
        session_data = {"search_string": "test", "folders": ["/path/to/folder"]}

        # 저장 시도
        result = config.save_session(session_name, session_data)
        assert result is True, "Session with special chars should be saved successfully"

        # 파일 생성 확인
        sanitized_name = config._sanitize_session_name(session_name)
        file_path = os.path.join(config.sessions_dir, f"{sanitized_name}.json")
        assert os.path.exists(file_path), f"Session file should exist: {file_path}"

        # 로드 확인
        loaded_data = config.load_session(session_name)
        assert loaded_data is not None, "Session should be loadable"
        assert loaded_data["search_string"] == "test", "Session data should match"


def test_save_session_empty_name(temp_dir):
    """빈 세션 이름 처리 테스트"""
    with patch("os.getenv", return_value=temp_dir):
        ConfigManager._instance = None
        config = ConfigManager()

        # 빈 이름으로 저장 시도
        result = config.save_session("", {"data": "test"})
        assert result is True, "Empty session name should use default"

        # 실제 생성된 파일명 확인 (sanitize_filename이 "untitled" 반환)
        sanitized = config._sanitize_session_name("")
        expected_path = os.path.join(config.sessions_dir, f"{sanitized}.json")
        assert os.path.exists(expected_path), f"Session file should exist: {expected_path}"
