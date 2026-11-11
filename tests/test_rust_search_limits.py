
import pytest

from core import search_engine
from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants


def _configured_limits(monkeypatch):
    limits = {
        Constants.CONFIG_KEY_MAX_PER_FILE_MATCHES: 17,
        Constants.CONFIG_KEY_MAX_JSON_DEPTH: 29,
        Constants.CONFIG_KEY_MAX_JSON_DOM_SIZE: 31,
    }
    monkeypatch.setattr(
        search_engine.ConfigManager,
        "get_advanced_settings",
        lambda _self: limits,
    )
    return limits


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
    assert dir_kwargs["max_json_depth"] == 29
    assert dir_kwargs["max_json_size"] == 31 * 1024 * 1024
    assert dir_kwargs["_flush_ms"] == Constants.RUST_RESULT_FLUSH_MS
    _list_args, list_kwargs = captured["list"]
    assert list_kwargs["max_per_file"] == 17
    assert list_kwargs["max_json_depth"] == 29
    assert list_kwargs["max_json_size"] == 31 * 1024 * 1024


def test_rust_search_limits_fall_back_to_safe_defaults_for_invalid_values(monkeypatch):
    monkeypatch.setattr(
        search_engine.ConfigManager,
        "get_advanced_settings",
        lambda _self: {
            Constants.CONFIG_KEY_MAX_PER_FILE_MATCHES: "invalid",
            Constants.CONFIG_KEY_MAX_JSON_DEPTH: -5,
            Constants.CONFIG_KEY_MAX_SEARCH_FILE_SIZE_MB: 2048,
        },
    )

    limits = search_engine._get_rust_search_limits()
    assert limits[:2] == (Constants.DEFAULT_MAX_PER_FILE_MATCHES, 1)
    assert limits[2] == Constants.SETTING_MAX_SEARCH_FILE_SIZE_MB * 1024 * 1024


def test_real_rust_engine_enforces_configured_json_size_limit(tmp_path):
    if not search_engine.HAS_RUST_ENGINE:
        pytest.skip("compiled Rust engine is unavailable")

    file_path = tmp_path / "large.json"
    file_path.write_text('{"payload":"' + ("x" * (2 * 1024 * 1024)) + '"}', encoding="utf-8")

    with pytest.raises(RuntimeError, match="ERR_TOO_LARGE") as exc_info:
        search_engine.sf_engine.search_file(  # type: ignore
            str(file_path),
            "needle",
            Constants.RUST_MODE_JSON,
            max_json_size=1024 * 1024,
        )

    assert "2097166 bytes" in str(exc_info.value)


def test_real_rust_json_size_limit_skips_only_large_file(tmp_path, monkeypatch):
    if not search_engine.HAS_RUST_ENGINE:
        pytest.skip("compiled Rust engine is unavailable")

    large_file = tmp_path / "large.json"
    valid_file = tmp_path / "valid.json"
    large_file.write_text(
        '{"payload":"' + ("x" * (2 * 1024 * 1024)) + '"}',
        encoding="utf-8",
    )
    valid_file.write_text('{"payload":"needle"}', encoding="utf-8")

    monkeypatch.setattr(
        search_engine.ConfigManager,
        "get_advanced_settings",
        lambda _self: {
            Constants.CONFIG_KEY_MAX_JSON_DOM_SIZE: 1,
            Constants.CONFIG_KEY_MAX_JSON_DEPTH: 29,
            Constants.CONFIG_KEY_MAX_PER_FILE_MATCHES: 17,
        },
    )

    response = search_engine.search_files_list_fast(
        [str(large_file), str(valid_file)],
        "needle",
        special_mode=Constants.MODE_JSON,
    )

    assert [path for path, _count, _matches in response["results"]] == [
        str(valid_file)
    ]
    assert len(response["skipped"]) == 1
    assert response["skipped"][0][0] == str(large_file)
    assert AppStrings.SKIP_REASON_TOO_LARGE.split("{}")[0] in response["skipped"][0][1]


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


def test_real_rust_shared_budget_skips_only_oversized_structured_file(tmp_path):
    if not search_engine.HAS_RUST_ENGINE:
        pytest.skip("compiled Rust engine is unavailable")

    large_json = tmp_path / "large.json"
    large_json.write_bytes(b'{"value":"needle"}' + b" " * (8 * 1024 * 1024))
    plain_text = tmp_path / "plain.txt"
    plain_text.write_text("needle", encoding="utf-8")

    json_options = search_engine.sf_engine.SearchOptions(  # type: ignore
        mode_bits=Constants.RUST_MODE_JSON,
        structured_memory_budget=1,
    )
    results, skipped = search_engine.sf_engine.search_files_list(  # type: ignore
        [str(large_json)],
        "needle",
        options=json_options,
    )

    assert results == []
    assert len(skipped) == 1
    assert skipped[0][0] == str(large_json)
    assert skipped[0][1].startswith("ERR_RESOURCE_BUDGET|")

    plain_options = search_engine.sf_engine.SearchOptions(  # type: ignore
        mode_bits=Constants.RUST_MODE_NORMAL,
        structured_memory_budget=1,
    )
    results, skipped = search_engine.sf_engine.search_files_list(  # type: ignore
        [str(plain_text)],
        "needle",
        options=plain_options,
    )
    assert results and skipped == []


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
    assert captured["options"]["structured_memory_budget"] > 0


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
        (-1, AppStrings.MSG_MATCH_LIMIT_PER_FILE.format(2), None, None),
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


def test_legacy_excel_pipe_result_preserves_pipe_in_sheet_name():
    matches, binary_count, sheet_skips = search_engine._normalize_rust_matches(
        [(7, "Sales | Europe | A12 | needle", 10, 6)],
        Constants.MODE_EXCEL,
    )

    assert matches == [(7, "Sales | Europe", "A12", "needle", 10, 6)]
    assert binary_count == 0
    assert sheet_skips == []


def test_visible_match_count_excludes_truncation_marker():
    assert search_engine._visible_match_count(
        [(1, "one", None, None), (-1, "(truncated)", None, None)]
    ) == 1


def test_partial_limit_markers_are_combined_into_one_file_notice(monkeypatch):
    monkeypatch.setattr(
        search_engine.ConfigManager,
        "get_advanced_settings",
        lambda _self: {
            Constants.CONFIG_KEY_MAX_PER_FILE_MATCHES: 2,
            Constants.CONFIG_KEY_MAX_JSON_DEPTH: 3,
        },
    )

    reason = search_engine._extract_partial_skip_reason(
        [
            (0, "__SF_TRUNCATED__", None, None),
            (0, "__SF_JSON_DEPTH_LIMIT__|3", None, None),
        ]
    )

    assert reason is not None
    assert AppStrings.SKIP_REASON_FILE_MATCH_LIMIT.format(2) in reason
    assert AppStrings.SKIP_REASON_JSON_DEPTH_LIMIT.format(3) in reason
    assert reason.count("\n") == 1


def test_file_list_wrapper_keeps_matches_and_reports_partial_file(monkeypatch):
    class FakeEngine:
        @staticmethod
        def search_files_list(*args, **kwargs):
            return (
                [
                    (
                        "limited.json",
                        [
                            (1, "/visible\tneedle", None, None),
                            (0, "__SF_TRUNCATED__", None, None),
                            (0, "__SF_JSON_DEPTH_LIMIT__|3", None, None),
                        ],
                    )
                ],
                [],
            )

    _configured_limits(monkeypatch)
    monkeypatch.setattr(search_engine, "sf_engine", FakeEngine())

    response = search_engine.search_files_list_fast(
        ["limited.json"],
        "needle",
        special_mode=Constants.MODE_JSON,
    )

    assert response["results"][0][0] == "limited.json"
    assert response["results"][0][1] == 1
    assert len(response["skipped"]) == 1
    assert response["skipped"][0][0] == "limited.json"
    assert AppStrings.SKIP_REASON_FILE_MATCH_LIMIT.format(17) in response["skipped"][0][1]
    assert AppStrings.SKIP_REASON_JSON_DEPTH_LIMIT.format(3) in response["skipped"][0][1]


def test_real_rust_engine_marks_partial_json_depth_without_dropping_visible_match(tmp_path):
    if not search_engine.HAS_RUST_ENGINE:
        pytest.skip("compiled Rust engine is unavailable")

    file_path = tmp_path / "partial.json"
    file_path.write_text(
        '{"visible":"needle","nested":{"value":"needle"}}',
        encoding="utf-8",
    )

    matches = search_engine.sf_engine.search_file(  # type: ignore
        str(file_path),
        "needle",
        Constants.RUST_MODE_JSON,
        max_json_depth=1,
    )

    assert any(match.kind == "match" for match in matches)
    depth_notice = next(match for match in matches if match.code == "JSON_DEPTH_LIMIT")
    assert depth_notice.kind == "partial"
    assert depth_notice.detail == "1"


def test_real_file_list_wrapper_reports_match_limit_and_keeps_results(tmp_path, monkeypatch):
    if not search_engine.HAS_RUST_ENGINE:
        pytest.skip("compiled Rust engine is unavailable")

    file_path = tmp_path / "many.txt"
    file_path.write_text("needle\nneedle\nneedle\n", encoding="utf-8")
    monkeypatch.setattr(
        search_engine.ConfigManager,
        "get_advanced_settings",
        lambda _self: {Constants.CONFIG_KEY_MAX_PER_FILE_MATCHES: 2},
    )

    response = search_engine.search_files_list_fast([str(file_path)], "needle")

    assert response["results"][0][1] == 2
    assert len(response["results"][0][2]) == 3  # two matches plus the result-row notice
    assert response["skipped"] == [
        (str(file_path), search_engine.AppStrings.SKIP_REASON_FILE_MATCH_LIMIT.format(2))
    ]


def test_real_file_list_wrapper_reports_json_depth_and_keeps_shallow_match(tmp_path, monkeypatch):
    if not search_engine.HAS_RUST_ENGINE:
        pytest.skip("compiled Rust engine is unavailable")

    file_path = tmp_path / "partial.json"
    file_path.write_text(
        '{"visible":"needle","nested":{"value":"needle"}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        search_engine.ConfigManager,
        "get_advanced_settings",
        lambda _self: {Constants.CONFIG_KEY_MAX_JSON_DEPTH: 1},
    )

    response = search_engine.search_files_list_fast(
        [str(file_path)],
        "needle",
        special_mode=Constants.MODE_JSON,
    )

    assert response["results"][0][1] == 1
    assert response["skipped"] == [
        (str(file_path), search_engine.AppStrings.SKIP_REASON_JSON_DEPTH_LIMIT.format(1))
    ]


def test_real_excel_file_limit_is_reported_as_partial_file(tmp_path, monkeypatch):
    if not search_engine.HAS_RUST_ENGINE:
        pytest.skip("compiled Rust engine is unavailable")

    from openpyxl import Workbook

    file_path = tmp_path / "many.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["needle", "needle", "needle"])
    workbook.save(file_path)
    monkeypatch.setattr(
        search_engine.ConfigManager,
        "get_advanced_settings",
        lambda _self: {Constants.CONFIG_KEY_MAX_PER_FILE_MATCHES: 2},
    )

    response = search_engine.search_files_list_fast(
        [str(file_path)],
        "needle",
        special_mode=Constants.MODE_EXCEL,
    )

    assert response["results"][0][1] == 2
    assert response["skipped"] == [
        (str(file_path), search_engine.AppStrings.SKIP_REASON_FILE_MATCH_LIMIT.format(2))
    ]


def test_real_rust_engine_accepts_named_search_options(tmp_path):
    if not search_engine.HAS_RUST_ENGINE:
        pytest.skip("compiled Rust engine is unavailable")

    file_path = tmp_path / "options.txt"
    file_path.write_text("needle\nneedle\n", encoding="utf-8")
    options = search_engine.sf_engine.SearchOptions(  # type: ignore
        mode_bits=Constants.RUST_MODE_NORMAL,
        extensions=["txt"],
        max_per_file=1,
        structured_memory_budget=256 * 1024 * 1024,
    )
    assert options.structured_memory_budget == 256 * 1024 * 1024

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
