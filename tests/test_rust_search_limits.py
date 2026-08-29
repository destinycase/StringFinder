from core import search_engine
from sf_utils.constants import Constants


def _configured_limits(monkeypatch):
    limits = {
        Constants.CONFIG_KEY_MAX_PER_FILE_MATCHES: 17,
        Constants.CONFIG_KEY_MAX_CHECK_CELLS: 23,
        Constants.CONFIG_KEY_MAX_JSON_DEPTH: 29,
    }
    monkeypatch.setattr(
        search_engine.ConfigManager,
        "get_advanced_settings",
        lambda _self: limits,
    )
    return limits


def test_single_file_rust_search_receives_configured_limits(tmp_path, monkeypatch):
    file_path = tmp_path / "sample.txt"
    file_path.write_text("needle", encoding="utf-8")
    captured = {}

    class FakeEngine:
        def search_file(self, *args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return []

    _configured_limits(monkeypatch)
    monkeypatch.setattr(search_engine, "HAS_RUST_ENGINE", True)
    monkeypatch.setattr(search_engine, "sf_engine", FakeEngine())

    assert search_engine.search_in_file(str(file_path), "needle") is None
    assert captured["kwargs"] == {
        "stop_event": None,
        "max_per_file": 17,
        "max_check_cells": 23,
        "max_json_depth": 29,
    }


def test_batch_rust_searches_receive_the_same_configured_limits(monkeypatch):
    _configured_limits(monkeypatch)
    captured = {}

    class FakeEngine:
        def search_dir(self, *args, **kwargs):
            captured["dir"] = (args, kwargs)
            return ([], [])

        def search_files_list(self, *args, **kwargs):
            captured["list"] = (args, kwargs)
            return ([], [])

    monkeypatch.setattr(search_engine, "sf_engine", FakeEngine())

    assert search_engine.search_directory_fast(["."], "needle") == {"results": [], "skipped": []}
    assert search_engine.search_files_list_fast(["sample.txt"], "needle") == {
        "results": [],
        "skipped": [],
    }

    dir_args, _dir_kwargs = captured["dir"]
    assert dir_args[-3:] == (17, 23, 29)
    _list_args, list_kwargs = captured["list"]
    assert list_kwargs["max_per_file"] == 17
    assert list_kwargs["max_check_cells"] == 23
    assert list_kwargs["max_json_depth"] == 29


def test_rust_search_limits_fall_back_to_safe_defaults_for_invalid_values(monkeypatch):
    monkeypatch.setattr(
        search_engine.ConfigManager,
        "get_advanced_settings",
        lambda _self: {
            Constants.CONFIG_KEY_MAX_PER_FILE_MATCHES: "invalid",
            Constants.CONFIG_KEY_MAX_CHECK_CELLS: 0,
            Constants.CONFIG_KEY_MAX_JSON_DEPTH: -5,
        },
    )

    assert search_engine._get_rust_search_limits() == (
        Constants.DEFAULT_MAX_PER_FILE_MATCHES,
        1,
        1,
    )
