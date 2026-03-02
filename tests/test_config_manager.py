"""
[test_config_manager.py]

이 테스트는 애플리케이션의 설정 관리자(ConfigManager)의 설정 로드, 저장 및 무결성을 검증합니다.

- 테스트 목적:
  1. JSON 기반 설정 파일의 직렬화/역직렬화 및 기본값 복구 로직 확인.
  2. 검색 히스토리, 세션 관리, 필터 상태 등 영속 데이터의 정확한 관리 검증.
  3. 세션 이름 내 금지 문자 및 경로 트래버스(Path Traversal) 공격 방어 로직 확인.

- 주요 검증 사항:
  1. 설정 파일 초기화 및 즉시 저장 기능.
  2. 중복 방지 및 최신순 정렬이 적용된 검색 히스토리 관리.
  3. 세션 저장 시 파일명 샌니타이징 및 이름 충돌 격리 처리.
  4. 손상된 설정 구조의 자동 복구(Self-healing).
"""

import os
from unittest.mock import patch

from sf_utils.config_manager import ConfigManager
from sf_utils.constants import Constants


def test_config_initialization(temp_dir):
    """test_config_initialization 함수."""
    with patch("os.getenv") as mock_getenv:
        mock_getenv.return_value = temp_dir
        ConfigManager._instance = None
        cm = ConfigManager()
        cm.save_immediately()

        assert os.path.exists(os.path.join(temp_dir, "StringFinder", "config.json"))
        assert cm.config["filters"]["extensions"] == ["xml", "json", "xlsx", "xlsm"]


def test_history_management(temp_dir):
    """test_history_management 함수."""
    with patch("os.getenv") as mock_getenv:
        mock_getenv.return_value = temp_dir
        ConfigManager._instance = None
        cm = ConfigManager()

        cm.add_history("test search")
        cm.save_immediately()
        assert "test search" in cm.get_history()

        ConfigManager._instance = None
        cm2 = ConfigManager()
        assert "test search" in cm2.get_history()


def test_splitter_states(temp_dir):
    """test_splitter_states 함수."""
    with patch("os.getenv") as mock_getenv:
        mock_getenv.return_value = temp_dir
        ConfigManager._instance = None
        cm = ConfigManager()

        cm.config["main_splitter_state"] = "aabbcc"
        cm.save_immediately()

        states = cm.get_splitter_states()
        assert states[0] == "aabbcc"





def test_history_bubble_up(temp_dir):
    """test_history_bubble_up 함수."""
    with patch("os.getenv") as mock_getenv:
        mock_getenv.return_value = temp_dir
        ConfigManager._instance = None
        cm = ConfigManager()

        cm.add_history("first")
        cm.add_history("second")

        hist = cm.get_history()
        hist = [x for x in hist if "Clear" not in x and "---" not in x]

        assert hist == ["second", "first"]

        cm.add_history("first")
        hist = [x for x in cm.get_history() if "Clear" not in x and "---" not in x]
        assert hist == ["first", "second"]

        cm.add_filename_history("file1")
        cm.add_filename_history("file2")
        cm.add_filename_history("file1")
        filename_history = [x for x in cm.get_filename_history() if "Clear" not in x and "---" not in x]
        assert filename_history == ["file1", "file2"]


def test_new_settings(temp_dir):
    """test_new_settings 함수."""
    with patch("os.getenv", return_value=temp_dir):
        ConfigManager._instance = None
        cm = ConfigManager()

        assert cm.config.get("lock_dock_layout") is False

        cm.config["lock_dock_layout"] = True
        cm.save_immediately()

        ConfigManager._instance = None
        cm2 = ConfigManager()
        assert cm2.config.get("lock_dock_layout") is True


def test_log_retention_settings(temp_dir):
    """test_log_retention_settings 함수."""
    with patch("os.getenv", return_value=temp_dir):
        ConfigManager._instance = None
        cm = ConfigManager()

        retention = cm.config.get("log_retention", {})
        assert retention.get("max_files") == 10
        assert retention.get("max_days") == 3

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
    """test_sanitize_session_name_forbidden_chars 함수."""
    with patch("os.getenv", return_value=temp_dir):
        ConfigManager._instance = None
        config = ConfigManager()

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
            assert forbidden_char not in sanitized


def test_sanitize_session_name_path_traversal(temp_dir):
    """test_sanitize_session_name_path_traversal 함수."""
    with patch("os.getenv", return_value=temp_dir):
        ConfigManager._instance = None
        config = ConfigManager()

        dangerous_name = "../../../unsafe_session"
        sanitized = config._sanitize_session_name(dangerous_name)

        assert sanitized == "unsafe_session"


def test_save_session_with_special_chars(temp_dir):
    """test_save_session_with_special_chars 함수."""
    with patch("os.getenv", return_value=temp_dir):
        ConfigManager._instance = None
        config = ConfigManager()

        session_name = "test?<>|*:session"
        session_data = {"search_string": "test", "folders": ["/path/to/folder"]}

        result = config.save_session(session_name, session_data)
        assert result is True

        sanitized_name = config._sanitize_session_name(session_name)
        file_path = os.path.join(config.sessions_dir, f"{sanitized_name}.json")
        assert os.path.exists(file_path)

        loaded_data = config.load_session(session_name)
        assert loaded_data is not None
        assert loaded_data["search_string"] == "test"


def test_save_session_empty_name(temp_dir):
    """test_save_session_empty_name 함수."""
    with patch("os.getenv", return_value=temp_dir):
        ConfigManager._instance = None
        config = ConfigManager()

        result = config.save_session("", {"data": "test"})
        assert result is True

        sanitized = config._sanitize_session_name("")
        expected_path = os.path.join(config.sessions_dir, f"{sanitized}.json")
        assert os.path.exists(expected_path)


def test_save_session_sanitized_name_collision_isolated(temp_dir):
    with patch("os.getenv", return_value=temp_dir):
        ConfigManager._instance = None
        config = ConfigManager()

        first_name = "tab?name"
        second_name = "tab*name"
        first_data = {"value": "A"}
        second_data = {"value": "B"}

        assert config._sanitize_session_name(first_name) == config._sanitize_session_name(second_name)

        assert config.save_session(first_name, first_data) is True
        assert config.save_session(second_name, second_data) is True

        loaded_first = config.load_session(first_name)
        loaded_second = config.load_session(second_name)

        assert loaded_first is not None and loaded_first["value"] == "A"
        assert loaded_second is not None and loaded_second["value"] == "B"

        sessions = config.get_all_session_names()
        assert first_name in sessions
        assert second_name in sessions

        safe_stem = config._sanitize_session_name(first_name)
        session_files = [
            filename
            for filename in os.listdir(config.sessions_dir)
            if filename.startswith(safe_stem) and filename.endswith(Constants.JSON_EXTENSION)
        ]
        assert len(session_files) == 2


def test_update_filters_does_not_alias_defaults(temp_dir):
    with patch("os.getenv", return_value=temp_dir):
        ConfigManager._instance = None
        config = ConfigManager()

        config.config["filters"] = "broken"
        folders = {"C:/Project": True}
        extensions = {"special_mode": "Normal", "extensions": {"txt": True}}
        filenames = {"filename_filter": "needle", "filenames": {"target.txt": True}}

        config.update_filters(folders, extensions, filenames)

        folders["C:/Project"] = False
        assert config.config["filters"]["folders"]["C:/Project"] is True

        config.config["filters"]["folders"]["C:/Project"] = False
        assert config.defaults["filters"]["folders"] == []


def test_get_filters_self_heals_invalid_structure(temp_dir):
    with patch("os.getenv", return_value=temp_dir):
        ConfigManager._instance = None
        config = ConfigManager()

        config.config["filters"] = "invalid-type"
        healed = config.get_filters()

        assert isinstance(healed, dict)
        assert "folders" in healed
        assert "extensions" in healed
        assert "filenames" in healed
