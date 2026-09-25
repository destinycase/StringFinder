from __future__ import annotations

from pathlib import Path

from core import search_engine
from core.worker import SearchWorker
from sf_utils.config_manager import ConfigManager
from sf_utils.constants import Constants


def test_python_batch_uses_snapshot_without_reloading_advanced_settings(
    monkeypatch, tmp_path: Path
) -> None:
    target = tmp_path / "many_matches.txt"
    target.write_text("needle needle", encoding="utf-8")
    snapshot = {
        Constants.CONFIG_KEY_MAX_SEARCH_FILE_SIZE_MB: 1,
        Constants.CONFIG_KEY_MAX_PER_FILE_MATCHES: 1,
        Constants.CONFIG_KEY_MAX_SMALL_FILE_SIZE: 10,
    }

    def unexpected_settings_read(_self):
        raise AssertionError("search must use the captured settings snapshot")

    monkeypatch.setattr(ConfigManager, "get_advanced_settings", unexpected_settings_read)

    result = search_engine.search_in_files_batch(
        [(str(target), target.stat().st_size)],
        "needle",
        force_python=True,
        search_settings_snapshot=snapshot,
    )

    assert result[Constants.PAYLOAD_RESULTS]


def test_rust_limits_use_the_search_snapshot(monkeypatch) -> None:
    snapshot = {
        Constants.CONFIG_KEY_MAX_SEARCH_FILE_SIZE_MB: 321,
        Constants.CONFIG_KEY_MAX_PER_FILE_MATCHES: 1234,
        Constants.CONFIG_KEY_MAX_CHECK_CELLS: 2345,
        Constants.CONFIG_KEY_MAX_JSON_DEPTH: 3456,
    }

    def unexpected_settings_read(_self):
        raise AssertionError("Rust limits must use the captured settings snapshot")

    monkeypatch.setattr(ConfigManager, "get_advanced_settings", unexpected_settings_read)

    with search_engine.use_search_settings_snapshot(snapshot):
        limits = search_engine._get_rust_search_limits()

    assert limits == (1234, 2345, 3456, 321 * 1024 * 1024)


def test_search_worker_keeps_settings_captured_at_start(monkeypatch) -> None:
    initial_settings = {Constants.CONFIG_KEY_MAX_TOTAL_MATCHES: 2}
    monkeypatch.setattr(
        ConfigManager,
        "get_advanced_settings",
        lambda _self: dict(initial_settings),
    )
    worker = SearchWorker({Constants.PAYLOAD_SEARCH_STRING: "needle"})

    monkeypatch.setattr(
        ConfigManager,
        "get_advanced_settings",
        lambda _self: {Constants.CONFIG_KEY_MAX_TOTAL_MATCHES: 20},
    )
    accepted, accepted_count, limit_reached = worker._fit_results_to_total_limit(
        [("file.txt", 5, [(1, "a"), (2, "b"), (3, "c"), (4, "d"), (5, "e")])]
    )

    assert worker.search_settings_snapshot == initial_settings
    assert accepted_count == 2
    assert accepted[0][1] == 2
    assert limit_reached
