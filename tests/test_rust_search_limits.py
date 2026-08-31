import json

import pytest

from core import search_engine
from sf_utils.constants import Constants


def _configured_limits(monkeypatch):
    limits = {
        Constants.CONFIG_KEY_MAX_PER_FILE_MATCHES: 17,
        Constants.CONFIG_KEY_MAX_CHECK_CELLS: 23,
        Constants.CONFIG_KEY_MAX_JSON_DEPTH: 29,
        Constants.CONFIG_KEY_MAX_JSON_DOM_SIZE: 31,
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
        "max_json_size": 31 * 1024 * 1024,
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

    dir_args, dir_kwargs = captured["dir"]
    assert dir_args == ()
    assert dir_kwargs["max_per_file"] == 17
    assert dir_kwargs["max_check_cells"] == 23
    assert dir_kwargs["max_json_depth"] == 29
    assert dir_kwargs["max_json_size"] == 31 * 1024 * 1024
    assert dir_kwargs["_flush_ms"] == Constants.RUST_RESULT_FLUSH_MS
    _list_args, list_kwargs = captured["list"]
    assert list_kwargs["max_per_file"] == 17
    assert list_kwargs["max_check_cells"] == 23
    assert list_kwargs["max_json_depth"] == 29
    assert list_kwargs["max_json_size"] == 31 * 1024 * 1024


def test_smart_scan_receives_json_limits(monkeypatch):
    _configured_limits(monkeypatch)
    captured = {}

    class FakeEngine:
        def find_files_with_keyword(self, *args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return ([], [])

    monkeypatch.setattr(search_engine, "sf_engine", FakeEngine())

    assert search_engine.find_files_with_keyword_fast(["."], "needle", return_skipped=True) == ([], [])
    assert captured["args"] == ()
    assert captured["kwargs"]["paths"] == ["."]
    assert captured["kwargs"]["keyword"] == "needle"
    assert captured["kwargs"]["max_json_depth"] == 29
    assert captured["kwargs"]["max_json_size"] == 31 * 1024 * 1024


def test_rust_search_limits_fall_back_to_safe_defaults_for_invalid_values(monkeypatch):
    monkeypatch.setattr(
        search_engine.ConfigManager,
        "get_advanced_settings",
        lambda _self: {
            Constants.CONFIG_KEY_MAX_PER_FILE_MATCHES: "invalid",
            Constants.CONFIG_KEY_MAX_CHECK_CELLS: 0,
            Constants.CONFIG_KEY_MAX_JSON_DEPTH: -5,
            Constants.CONFIG_KEY_MAX_JSON_DOM_SIZE: 999,
        },
    )

    assert search_engine._get_rust_search_limits() == (
        Constants.DEFAULT_MAX_PER_FILE_MATCHES,
        1,
        1,
        Constants.DEFAULT_MAX_JSON_DOM_SIZE_MB * 1024 * 1024,
    )


def test_real_rust_engine_enforces_configured_json_size_limit(tmp_path):
    if not search_engine.HAS_RUST_ENGINE:
        pytest.skip("compiled Rust engine is unavailable")

    file_path = tmp_path / "large.json"
    file_path.write_text('{"payload":"' + ("x" * (2 * 1024 * 1024)) + '"}', encoding="utf-8")

    result = search_engine.sf_engine.search_file(  # type: ignore
        str(file_path),
        "needle",
        Constants.RUST_MODE_JSON,
        max_json_size=1024 * 1024,
    )

    assert result
    assert result[0][1] == "ERR_MEMORY_GUARD|Large JSON"


def test_real_rust_engine_exposes_structured_match_metadata(tmp_path):
    if not search_engine.HAS_RUST_ENGINE:
        pytest.skip("compiled Rust engine is unavailable")

    file_path = tmp_path / "structured.txt"
    file_path.write_text("needle", encoding="utf-8")

    result = search_engine.sf_engine.search_file(  # type: ignore
        str(file_path),
        "needle",
        Constants.RUST_MODE_NORMAL,
    )

    assert result
    match = result[0]
    assert match.kind == "match"
    assert match.code is None
    assert match.detail is None
    assert (match[0], match[1]) == (1, "needle")


def test_real_rust_engine_accepts_named_search_options(tmp_path):
    if not search_engine.HAS_RUST_ENGINE:
        pytest.skip("compiled Rust engine is unavailable")

    file_path = tmp_path / "options.txt"
    file_path.write_text("needle\nneedle\n", encoding="utf-8")
    options = search_engine.sf_engine.SearchOptions(  # type: ignore
        mode_bits=Constants.RUST_MODE_NORMAL,
        extensions=["txt"],
        max_per_file=1,
    )

    single = search_engine.sf_engine.search_file(str(file_path), "needle", options=options)  # type: ignore
    assert len(single) == 2  # one visible result plus the truncation marker

    listed, skipped = search_engine.sf_engine.search_files_list(
        [str(file_path)], "needle", options=options  # type: ignore
    )
    assert listed and not skipped

    walked, skipped = search_engine.sf_engine.search_dir(
        [str(tmp_path)], "needle", options=options  # type: ignore
    )
    assert walked and not skipped

    found, skipped = search_engine.sf_engine.find_files_with_keyword(
        [str(tmp_path)], "needle", options=options  # type: ignore
    )
    assert found and not skipped


def test_python_batch_wrapper_uses_named_options_when_available(monkeypatch):
    captured = {}

    class FakeOptions:
        def __init__(self, **values):
            self.values = values

    class FakeEngine:
        SearchOptions = FakeOptions

        @staticmethod
        def search_files_list(*args, **kwargs):
            captured["args"] = args
            captured["options"] = kwargs["options"].values
            return [], []

    monkeypatch.setattr(search_engine, "sf_engine", FakeEngine)
    assert search_engine.search_files_list_fast(["sample.txt"], "needle") == {
        "results": [],
        "skipped": [],
    }
    assert captured["args"] == ()
    assert captured["options"]["max_per_file"] is not None
    assert captured["options"]["flush_ms"] is not None


def test_rust_batch_callback_errors_are_returned(tmp_path):
    if not search_engine.HAS_RUST_ENGINE:
        pytest.skip("compiled Rust engine is unavailable")

    file_path = tmp_path / "callback.txt"
    file_path.write_text("needle", encoding="utf-8")

    def failing_callback(_batch):
        raise RuntimeError("callback exploded")

    with pytest.raises(RuntimeError, match="results callback failed"):
        search_engine.sf_engine.search_files_list(  # type: ignore
            [str(file_path)],
            "needle",
            Constants.RUST_MODE_NORMAL,
            results_callback=failing_callback,
        )


def test_real_rust_smart_scan_finds_escaped_json_unicode(tmp_path):
    if not search_engine.HAS_RUST_ENGINE:
        pytest.skip("compiled Rust engine is unavailable")

    matching_file = tmp_path / "matching.json"
    partial_file = tmp_path / "partial.json"
    matching_file.write_text(json.dumps({"value": "한국어"}, ensure_ascii=True), encoding="utf-8")
    partial_file.write_text(json.dumps({"value": "한"}, ensure_ascii=True), encoding="utf-8")

    found = search_engine.find_files_with_keyword_fast(
        [str(tmp_path)],
        "한국어",
        extensions=["json"],
        special_mode=Constants.MODE_JSON,
    )

    found_paths = {path for path, _size in found}
    assert str(matching_file) in found_paths
    assert str(partial_file) not in found_paths


def test_rust_truncation_marker_is_normalized(monkeypatch):
    monkeypatch.setattr(
        search_engine.ConfigManager,
        "get_advanced_settings",
        lambda _self: {Constants.CONFIG_KEY_MAX_PER_FILE_MATCHES: 2},
    )

    matches, binary_count, sheet_skips = search_engine._normalize_rust_matches(
        [
            (1, "one", None, None),
            (2, "two", None, None),
            (0, "__SF_TRUNCATED__", None, None),
        ]
    )

    assert matches == [
        (1, "one", None, None),
        (2, "two", None, None),
        (-1, "(파일당 최대 매치 도달: 2건)", None, None),
    ]
    assert binary_count == 0
    assert sheet_skips == []


def test_literal_truncation_text_is_not_treated_as_metadata(monkeypatch):
    monkeypatch.setattr(
        search_engine.ConfigManager,
        "get_advanced_settings",
        lambda _self: {Constants.CONFIG_KEY_MAX_PER_FILE_MATCHES: 2},
    )

    matches, _, _ = search_engine._normalize_rust_matches(
        [(1, "__SF_TRUNCATED__", None, None)]
    )

    assert matches == [(1, "__SF_TRUNCATED__", None, None)]


def test_visible_match_count_excludes_truncation_marker():
    assert search_engine._visible_match_count(
        [(1, "one", None, None), (-1, "(truncated)", None, None)]
    ) == 1
