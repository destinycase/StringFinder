from unittest.mock import patch

from core.worker import SearchWorker
from core import search_engine
from sf_utils.app_strings import AppStrings
from sf_utils.config_manager import ConfigManager
from sf_utils.constants import Constants
from sf_utils.resource_guard import memory_pressure_detected
import sf_utils.resource_guard as resource_guard


def test_memory_pressure_uses_available_memory_and_process_rss():
    total = 8 * 1024 * 1024 * 1024

    assert memory_pressure_detected(
        {"available": 511 * 1024 * 1024, "total": total, "process_rss": 1, "system_percent": 20}
    )
    assert memory_pressure_detected(
        {"available": 4 * 1024 * 1024 * 1024, "total": total, "process_rss": int(total * 0.60), "system_percent": 50}
    )
    assert not memory_pressure_detected(
        {"available": 4 * 1024 * 1024 * 1024, "total": total, "process_rss": 1 * 1024 * 1024 * 1024, "system_percent": 90}
    )


def test_unsafe_structured_settings_are_clamped(temp_dir):
    with patch("os.getenv", return_value=temp_dir):
        ConfigManager._instance = None
        manager = ConfigManager()
        manager.set_advanced_settings(
            {
                Constants.CONFIG_KEY_MAX_JSON_DOM_SIZE: 1024,
                Constants.CONFIG_KEY_MAX_JSON_DEPTH: 1_000_000,
            }
        )

        settings = manager.get_advanced_settings()

    assert settings[Constants.CONFIG_KEY_MAX_JSON_DOM_SIZE] == Constants.DEFAULT_MAX_JSON_DOM_SIZE_MB
    assert settings[Constants.CONFIG_KEY_MAX_JSON_DEPTH] == Constants.DEFAULT_MAX_JSON_DEPTH


def test_memory_error_is_emitted_once():
    worker = SearchWorker({})
    errors = []
    worker.signals.error.connect(errors.append)

    worker._stop_for_memory_pressure("test")
    worker._stop_for_memory_pressure("test-again")

    assert errors == [AppStrings.ERROR_MEMORY_CRITICAL]
    assert worker.stop_event.is_set()
    assert not worker.is_running.is_set()


def test_structured_search_preflight_returns_memory_skip(tmp_path, monkeypatch):
    file_path = tmp_path / "guarded.json"
    file_path.write_text('{"value": "needle"}', encoding="utf-8")
    monkeypatch.setattr(resource_guard, "memory_pressure_detected", lambda _snapshot: True)

    result = search_engine.search_in_json_special(str(file_path), "needle")

    assert result == (Constants.STATUS_SKIPPED, AppStrings.ERROR_MEMORY_CRITICAL)
