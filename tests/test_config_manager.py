import os
from unittest.mock import patch
from sf_utils.config_manager import ConfigManager


def test_config_initialization(temp_dir):
    """Test ConfigManager initialization and defaults"""
    with patch("os.getenv") as mock_getenv:
        mock_getenv.return_value = temp_dir
        ConfigManager._instance = None
        cm = ConfigManager()
        cm.save_immediately()

        assert os.path.exists(os.path.join(temp_dir, "StringFinder", "config.json"))
        assert cm.config["filters"]["extensions"] == ["xml", "json", "xlsx", "xlsm"]


def test_history_management(temp_dir):
    """Test history addition and limits"""
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
    """Test splitter state save/load"""
    with patch("os.getenv") as mock_getenv:
        mock_getenv.return_value = temp_dir
        ConfigManager._instance = None
        cm = ConfigManager()

        cm.config["main_splitter_state"] = "aabbcc"
        cm.save_immediately()

        states = cm.get_splitter_states()
        assert states[0] == "aabbcc"


def test_background_settings(temp_dir):
    """Test background settings"""
    from sf_utils.config_manager import ConfigManager

    ConfigManager._instance = None
    with patch("os.getenv", return_value=temp_dir):
        cm = ConfigManager()
        cm.defaults = {"global_hotkey": "alt+shift+space", "run_at_startup": False, "theme": "Dark"}
        if not cm.config:
            cm.config = cm.defaults.copy()

        assert cm.get_global_hotkey() == "alt+shift+space"
        assert cm.get_run_at_startup() is False

        cm.set_global_hotkey("ctrl+f12")
        cm.set_run_at_startup(True)
        cm.save_immediately()

        ConfigManager._instance = None
        cm2 = ConfigManager()

        assert cm2.get_global_hotkey() == "ctrl+f12"
        assert cm2.get_run_at_startup() is True


def test_history_bubble_up(temp_dir):
    """Test history bubble up"""
    with patch("os.getenv") as mock_getenv:
        mock_getenv.return_value = temp_dir
        ConfigManager._instance = None
        cm = ConfigManager()

        cm.add_history("first")
        cm.add_history("second")

        hist = cm.get_history()
        # Remove "Clear History" separator if present
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
    """Test layout lock settings"""
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
    """Test log retention settings"""
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
    """Test forbidden chars in session name"""
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
    """Test path traversal prevention"""
    with patch("os.getenv", return_value=temp_dir):
        ConfigManager._instance = None
        config = ConfigManager()

        dangerous_name = "../../../unsafe_session"
        sanitized = config._sanitize_session_name(dangerous_name)

        assert sanitized == "unsafe_session"


def test_save_session_with_special_chars(temp_dir):
    """Test session save with special chars"""
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
    """Test empty session name"""
    with patch("os.getenv", return_value=temp_dir):
        ConfigManager._instance = None
        config = ConfigManager()

        result = config.save_session("", {"data": "test"})
        assert result is True

        sanitized = config._sanitize_session_name("")
        expected_path = os.path.join(config.sessions_dir, f"{sanitized}.json")
        assert os.path.exists(expected_path)
