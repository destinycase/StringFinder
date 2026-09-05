from unittest.mock import patch

import pytest

from core.worker import SearchWorker
from core import search_engine
from sf_utils.app_strings import AppStrings
from sf_utils.config_manager import ConfigManager
from sf_utils.constants import Constants
from sf_utils.resource_guard import (
    available_processing_budget_bytes,
    calculate_memory_limits,
    estimate_structured_memory_bytes,
    memory_pressure_detected,
    memory_pressure_reason,
    projected_memory_pressure_reason,
)
import sf_utils.resource_guard as resource_guard


MIB = 1024 * 1024
GIB = 1024 * MIB


@pytest.mark.parametrize("total_gib", [4, 8, 16, 32, 64, 128])
def test_memory_limits_scale_and_remain_bounded(total_gib):
    total = total_gib * GIB
    limits = calculate_memory_limits(total)

    expected_reserve = min(
        max(Constants.MIN_AVAILABLE_MEMORY_BYTES, total * 5 // 100),
        Constants.MAX_AVAILABLE_MEMORY_RESERVE_BYTES,
    )
    expected_process_limit = min(
        total * 60 // 100,
        Constants.MAX_PROCESS_MEMORY_BYTES,
    )
    assert limits.reserve_bytes == expected_reserve
    assert limits.process_limit_bytes == expected_process_limit


def test_memory_pressure_boundaries_and_system_percent_are_independent():
    total = 16 * GIB
    limits = calculate_memory_limits(total)
    safe = {
        "available": limits.reserve_bytes,
        "total": total,
        "process_rss": limits.process_limit_bytes - 1,
        "system_percent": 99,
    }

    assert memory_pressure_reason(safe) is None
    assert not memory_pressure_detected(safe)
    assert memory_pressure_reason({**safe, "available": limits.reserve_bytes - 1}) == (
        "low_available_memory"
    )
    assert memory_pressure_reason({**safe, "process_rss": limits.process_limit_bytes}) == (
        "process_tree_limit"
    )


@pytest.mark.parametrize(
    "snapshot",
    [
        {},
        {"total": 8 * GIB},
        {"available": 1 * GIB},
        {"available": 1 * GIB, "total": 0, "process_rss": 1},
        {"available": 1 * GIB, "total": 8 * GIB, "valid": 0},
    ],
)
def test_invalid_memory_telemetry_never_stops_a_search(snapshot):
    assert memory_pressure_reason(snapshot) is None
    assert projected_memory_pressure_reason(snapshot, 2 * GIB) is None


def test_projected_memory_budget_uses_available_and_process_headroom():
    total = 8 * GIB
    limits = calculate_memory_limits(total)
    required = 256 * MIB
    safe = {
        "available": limits.reserve_bytes + required,
        "total": total,
        "process_rss": limits.process_limit_bytes - required - 1,
        "system_percent": 95,
    }

    assert projected_memory_pressure_reason(safe, required) is None
    assert projected_memory_pressure_reason({**safe, "available": safe["available"] - 1}, required) == (
        "projected_available_memory"
    )
    assert projected_memory_pressure_reason(
        {**safe, "process_rss": limits.process_limit_bytes - required}, required
    ) == "projected_process_tree_limit"


def test_available_processing_budget_uses_the_tighter_headroom():
    total = 16 * GIB
    limits = calculate_memory_limits(total)
    snapshot = {
        "available": limits.reserve_bytes + 3 * GIB,
        "total": total,
        "process_rss": limits.process_limit_bytes - 2 * GIB,
    }

    assert available_processing_budget_bytes(snapshot) == 2 * GIB


def test_structured_memory_estimates_are_stable_and_engine_specific():
    file_size = 10 * MIB

    assert estimate_structured_memory_bytes(file_size, "json", "rust") == 89 * MIB
    assert estimate_structured_memory_bytes(file_size, "xml", "rust") == 89 * MIB
    assert estimate_structured_memory_bytes(file_size, "xml", "python") == 104 * MIB
    assert estimate_structured_memory_bytes(file_size, "json", "python") == 188 * MIB
    assert estimate_structured_memory_bytes(file_size, "excel", "rust") == 208 * MIB
    assert estimate_structured_memory_bytes(file_size, "excel", "python") == 208 * MIB
    with pytest.raises(ValueError):
        estimate_structured_memory_bytes(file_size, "yaml", "python")  # type: ignore[arg-type]


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


def test_projected_structured_memory_is_a_local_skip(tmp_path, monkeypatch):
    file_path = tmp_path / "projected.json"
    file_path.write_text('{"value": "needle"}', encoding="utf-8")
    monkeypatch.setattr(
        resource_guard,
        "memory_snapshot",
        lambda: {
            "available": 600 * MIB,
            "total": 8 * GIB,
            "process_rss": 100 * MIB,
            "system_percent": 93,
            "valid": 1,
        },
    )

    result = search_engine.search_in_json_special(
        str(file_path),
        "needle",
        use_complex_search=True,
    )

    assert result is not None
    assert result[0] == Constants.STATUS_SKIPPED
    assert result[1].startswith(AppStrings.SKIP_REASON_RESOURCE_BUDGET.split("{", 1)[0])
    assert AppStrings.SKIP_DETAIL_RESOURCE_BUDGET in result[1]
    assert result[1] != AppStrings.ERROR_MEMORY_CRITICAL
    assert not SearchWorker._is_memory_skip([(str(file_path), result[1])])


def test_configured_json_size_limit_precedes_projected_budget(tmp_path, monkeypatch):
    file_path = tmp_path / "configured-limit.json"
    file_path.write_text('{"value": "needle"}', encoding="utf-8")
    monkeypatch.setattr(search_engine.os.path, "getsize", lambda _path: 600 * MIB)
    monkeypatch.setattr(
        resource_guard,
        "memory_snapshot",
        lambda: {
            "available": 6 * GIB,
            "total": 8 * GIB,
            "process_rss": 100 * MIB,
            "system_percent": 25,
            "valid": 1,
        },
    )

    result = search_engine.search_in_json_special(
        str(file_path),
        "needle",
        use_complex_search=True,
    )

    assert result is not None
    assert result[0] == Constants.STATUS_SKIPPED
    assert result[1].startswith(AppStrings.SKIP_REASON_TOO_LARGE.split("{", 1)[0])
    assert "메모리 예산" not in result[1]


def test_json_size_limit_does_not_trigger_global_memory_stop():
    new_reason = search_engine.format_skip_reason(
        "ERR_JSON_SIZE_LIMIT|1048576 bytes"
    )
    legacy_reason = search_engine.format_skip_reason(
        "ERR_MEMORY_GUARD|Large JSON"
    )

    assert not SearchWorker._is_memory_skip([("large.json", new_reason)])
    assert not SearchWorker._is_memory_skip([("legacy.json", legacy_reason)])
    assert not SearchWorker._is_memory_skip(
        [("legacy-raw.json", "ERR_MEMORY_GUARD|Large JSON")]
    )


def test_only_system_memory_pressure_reason_triggers_global_stop():
    assert SearchWorker._is_memory_skip(
        [("guarded.json", AppStrings.ERROR_MEMORY_CRITICAL)]
    )
