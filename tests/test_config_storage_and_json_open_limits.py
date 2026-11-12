"""설정 저장(임시 파일 소유권)과 JSON 열기 크기 제한 계약."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QMainWindow

from sf_utils import resource_guard
from sf_utils.app_strings import AppStrings
from sf_utils.config_manager import ConfigManager
from sf_utils.constants import Constants as C
from sf_utils.localization import get_language, set_language
from ui.main_window import MainWindow


def test_default_memory_fixture_has_valid_safe_counters(monkeypatch):
    monkeypatch.setattr(resource_guard, "_process_tree_rss_bytes", lambda: 100 * 1024**2)
    snapshot = resource_guard.memory_snapshot()
    assert snapshot["valid"] == 1
    assert snapshot["total"] == 16 * 1024**3
    assert snapshot["available"] == 8 * 1024**3
    assert resource_guard.memory_pressure_reason(snapshot) is None


def test_config_unique_temporary_does_not_touch_old_temporary_file(mock_config_manager):
    manager = mock_config_manager
    legacy = Path(manager.config_path + C.TEMP_FILE_SUFFIX)
    legacy.write_text("owned by another process", encoding="utf-8")
    manager._config["probe"] = "saved"
    assert manager.save_immediately()
    assert legacy.read_text(encoding="utf-8") == "owned by another process"
    assert json.loads(Path(manager.config_path).read_text(encoding="utf-8"))["probe"] == "saved"
    assert not list(Path(manager.config_dir).glob(".config_*.tmp"))


@pytest.mark.parametrize("stage", ["write", "replace"])
def test_config_failure_preserves_original_and_cleans_unique_temps(mock_config_manager, monkeypatch, stage):
    manager = mock_config_manager
    assert manager.save_immediately()
    manager.cancel_start()
    original = Path(manager.config_path).read_bytes()
    paths = []
    real_dump, real_replace = json.dump, os.replace
    monkeypatch.setattr("sf_utils.config_manager.time.sleep", lambda _: None)

    def failed_write(payload, stream, **kwargs):
        if Path(stream.name).parent != Path(manager.config_dir):
            return real_dump(payload, stream, **kwargs)
        paths.append(stream.name)
        stream.write("partial")
        raise OSError("disk full")

    def failed_replace(source, destination):
        if Path(destination) != Path(manager.config_path):
            return real_replace(source, destination)
        paths.append(source)
        raise PermissionError("target locked")

    with monkeypatch.context() as injection:
        if stage == "write":
            injection.setattr("sf_utils.config_manager.json.dump", failed_write)
        else:
            injection.setattr("sf_utils.config_manager.os.replace", failed_replace)
        assert not manager.save_immediately()
    assert len(paths) == len(set(paths)) == 5
    assert all(Path(path).parent == Path(manager.config_dir) for path in paths)
    assert Path(manager.config_path).read_bytes() == original
    assert not list(Path(manager.config_dir).glob(".config_*.tmp"))
    assert manager.save_immediately()


def test_config_retry_survives_unremovable_failed_temporary(mock_config_manager, monkeypatch):
    manager = mock_config_manager
    manager.cancel_start()
    real_dump, real_remove = json.dump, os.remove
    failed_paths = []
    monkeypatch.setattr("sf_utils.config_manager.time.sleep", lambda _: None)

    def first_write_fails(payload, stream, **kwargs):
        if not failed_paths:
            failed_paths.append(stream.name)
            stream.write("partial")
            raise OSError("injected first write failure")
        assert stream.name != failed_paths[0]
        return real_dump(payload, stream, **kwargs)

    def cannot_remove_locked(path):
        if str(path) in failed_paths:
            raise PermissionError("injected temporary lock")
        return real_remove(path)

    with monkeypatch.context() as injection:
        injection.setattr("sf_utils.config_manager.json.dump", first_write_fails)
        injection.setattr("sf_utils.config_manager.os.remove", cannot_remove_locked)
        assert manager.save_immediately()
    # A locked file cannot be forcibly removed, but it no longer blocks saving.
    assert Path(failed_paths[0]).exists()
    Path(failed_paths[0]).unlink()


def test_config_temp_creation_permission_failure_is_bounded(mock_config_manager, monkeypatch):
    manager = mock_config_manager
    manager.cancel_start()
    denied = Mock(side_effect=PermissionError("creation denied"))
    monkeypatch.setattr("sf_utils.config_manager.time.sleep", lambda _: None)
    with monkeypatch.context() as injection:
        injection.setattr("builtins.open", denied)
        assert manager.save_immediately() is False
    assert denied.call_count == 5
    assert all(call.args[1] == "x" for call in denied.call_args_list)
    assert not list(Path(manager.config_dir).glob(".config_*.tmp"))


def test_config_name_collision_never_removes_another_writers_file(mock_config_manager, monkeypatch):
    manager = mock_config_manager
    manager.cancel_start()
    other = Path(manager.config_dir) / ".config_collision.tmp"
    other.write_text("another writer", encoding="utf-8")
    monkeypatch.setattr("sf_utils.config_manager.time.sleep", lambda _: None)
    with monkeypatch.context() as injection:
        injection.setattr("sf_utils.config_manager.uuid.uuid4", lambda: SimpleNamespace(hex="collision"))
        assert not manager.save_immediately()
    assert other.read_text(encoding="utf-8") == "another writer"


@pytest.mark.parametrize("language", ["ko", "en"])
def test_temporary_path_warning_is_localized_and_shown_once(qtbot, monkeypatch, language):
    previous = get_language()
    try:
        set_language(language)
        window = QMainWindow()
        qtbot.addWidget(window)
        window.config_manager = SimpleNamespace(uses_temporary_storage=True, config_dir="D:/temporary")
        window._temporary_storage_warning_shown = False
        warning = Mock()
        monkeypatch.setattr("ui.main_window.QMessageBox.warning", warning)
        MainWindow._warn_temporary_config_storage(window)
        MainWindow._warn_temporary_config_storage(window)
        warning.assert_called_once_with(window, AppStrings.ERROR_TITLE,
                                       AppStrings.CONFIG_TEMP_STORAGE_WARNING.format("D:/temporary"))
        assert ("temporary" if language == "en" else "임시 폴더") in warning.call_args.args[2]
    finally:
        set_language(previous)


def test_normal_storage_has_no_warning(qtbot, monkeypatch):
    window = QMainWindow()
    qtbot.addWidget(window)
    window.config_manager = SimpleNamespace(uses_temporary_storage=False)
    window._temporary_storage_warning_shown = False
    warning = Mock()
    monkeypatch.setattr("ui.main_window.QMessageBox.warning", warning)
    MainWindow._warn_temporary_config_storage(window)
    warning.assert_not_called()


def test_real_startup_schedules_temporary_storage_warning(qtbot, mock_config_manager, monkeypatch):
    mock_config_manager.uses_temporary_storage = True
    warning = Mock()
    monkeypatch.setattr("ui.main_window.QMessageBox.warning", warning)
    monkeypatch.setattr(MainWindow, "_apply_theme", lambda self: None)
    window = MainWindow()
    qtbot.addWidget(window)
    monkeypatch.setattr(window, "_quit_application", lambda: None)
    assert not warning.called
    window.show()
    qtbot.waitUntil(lambda: warning.called)
    warning.assert_called_once_with(window, AppStrings.ERROR_TITLE,
        AppStrings.CONFIG_TEMP_STORAGE_WARNING.format(mock_config_manager.config_dir))


@pytest.mark.parametrize("fallback", [False, True])
def test_storage_flag_only_marks_actual_temp_fallback(tmp_path, monkeypatch, fallback):
    ConfigManager._instance = None
    normal = tmp_path / "normal"
    temporary = tmp_path / "temporary"
    real_makedirs = os.makedirs
    monkeypatch.setenv("APPDATA", str(normal))
    monkeypatch.setattr("sf_utils.config_manager.tempfile.gettempdir", lambda: str(temporary))

    def make_directory(path, **kwargs):
        if fallback and Path(path) == normal / C.APP_NAME:
            raise PermissionError("normal storage denied")
        return real_makedirs(path, **kwargs)

    monkeypatch.setattr("sf_utils.config_manager.os.makedirs", make_directory)
    manager = ConfigManager()
    try:
        assert manager.uses_temporary_storage is fallback
        expected = temporary / C.APPDATA_TEMP_DIR if fallback else normal / C.APP_NAME
        assert Path(manager.config_dir) == expected
    finally:
        manager.stop()
        ConfigManager._instance = None


def test_missing_appdata_home_fallback_is_not_temporary(tmp_path, monkeypatch):
    ConfigManager._instance = None
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr("sf_utils.config_manager.os.path.expanduser", lambda _: str(tmp_path))
    manager = ConfigManager()
    try:
        assert not manager.uses_temporary_storage
        assert Path(manager.config_dir) == tmp_path / C.APPDATA_FALLBACK_DIR / C.APP_NAME
    finally:
        manager.stop()
        ConfigManager._instance = None


