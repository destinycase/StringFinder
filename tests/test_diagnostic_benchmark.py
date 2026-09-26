import json
import sys
from pathlib import Path

from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import diagnostic_benchmark as diagnostic


def test_report_includes_actionable_aggregates_and_private_file_ids(tmp_path, monkeypatch):
    (tmp_path / "confidential-project-name.xlsx").write_bytes(b"fake workbook")
    (tmp_path / "secret.customerproject").write_bytes(b"custom extension")
    (tmp_path / "source.txt").write_text("plain text", encoding="utf-8")
    progress = []
    observed_queries = []
    policy = {
        "exclude_hidden": True,
        "exclude_binary": True,
        "advanced_settings": {
            Constants.CONFIG_KEY_MAX_PER_FILE_MATCHES: 10_000,
            Constants.CONFIG_KEY_MAX_SEARCH_FILE_SIZE_MB: 1024,
        },
    }
    monkeypatch.setattr(diagnostic, "_diagnostic_search_policy", lambda: policy)

    def fake_search(path, _query, **_kwargs):
        observed_queries.append(_query)
        if path.endswith(".xlsx"):
            return (Constants.STATUS_SKIPPED, AppStrings.SKIP_REASON_EXCEL_CELL_LIMIT.format(500_000))
        return None

    monkeypatch.setattr(diagnostic, "search_in_file", fake_search)
    report = diagnostic.run(tmp_path, repeats=1, progress_callback=lambda *args: progress.append(args), max_seconds=None)

    assert report["diagnostic_version"] == 5
    assert report["status"] == "completed"
    assert report["verdict"] == "REVIEW"
    assert report["target"]["file_count"] == 3
    assert report["diagnostic_config"]["completed_work_units"] == 12
    assert len(progress) == 12
    assert report["scenarios"][1]["skip_reason_codes"] == {"INFO_EXCEL_CELL_LIMIT": 1}
    assert report["search_policy"] == policy
    assert len(set(observed_queries)) == 1
    assert len(observed_queries[0]) == 32
    assert report["scenarios"][0]["measurement_coverage_percent"] == 100.0
    assert report["scenarios"][0]["resource"]["samples"]
    assert "(other)" in report["target"]["extensions"]
    encoded = json.dumps(report, ensure_ascii=False)
    markdown = diagnostic._markdown(report)
    assert "confidential-project-name" not in encoded
    assert "customerproject" not in encoded
    assert str(tmp_path) not in encoded
    assert "Normal search - no match" in markdown
    assert "Effective search settings" in markdown
    assert "Resource samples" in markdown
    assert "Files larger than 1 GiB: **0**" in markdown
    assert "query text redacted" in report["workload"]["query"]
    assert observed_queries[0] not in encoded


def test_timeout_returns_a_readable_partial_report(tmp_path, monkeypatch):
    (tmp_path / "not-searched.txt").write_text("data", encoding="utf-8")
    monkeypatch.setattr(diagnostic, "search_in_file", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        diagnostic,
        "_diagnostic_search_policy",
        lambda: {"exclude_hidden": True, "exclude_binary": True, "advanced_settings": {}},
    )
    report = diagnostic.run(tmp_path, repeats=5, max_seconds=0)
    assert report["status"] == "timed_out"
    assert report["verdict"] == "INCOMPLETE"
    assert report["scenarios"][0]["status"] == "timed_out"
    assert len(report["scenarios"]) == 4
    assert all(item["status"] == "timed_out" for item in report["scenarios"])
    assert all(item["measurement_coverage_percent"] == 0 for item in report["scenarios"])
    assert "INCOMPLETE" in diagnostic._markdown(report)


def test_time_slicing_samples_every_mode_before_global_timeout(tmp_path, monkeypatch):
    for index in range(3):
        (tmp_path / f"sample-{index}.txt").write_text("data", encoding="utf-8")
    clock = {"now": 0.0}

    def fake_clock():
        return clock["now"]

    def fake_search(*_args, **_kwargs):
        clock["now"] += 1.0
        return None

    monkeypatch.setattr(diagnostic.time, "perf_counter", fake_clock)
    monkeypatch.setattr(diagnostic, "search_in_file", fake_search)
    monkeypatch.setattr(
        diagnostic,
        "_diagnostic_search_policy",
        lambda: {"exclude_hidden": True, "exclude_binary": True, "advanced_settings": {}},
    )

    report = diagnostic.run(tmp_path, repeats=1, max_seconds=8)

    assert report["status"] == "timed_out"
    assert len(report["scenarios"]) == 4
    assert all(item["files_processed"] == 2 for item in report["scenarios"])
    assert all(item["measurement_coverage_percent"] == 66.67 for item in report["scenarios"])


def test_short_durations_are_reported_in_milliseconds():
    assert diagnostic._duration(0.015) == "15.00ms"
    assert diagnostic._duration(1.25) == "1.25s"
    assert diagnostic._format_bytes(12) == "12 bytes"
    assert diagnostic._format_bytes(1024) == "1.00 KiB"


def test_unexpected_fixed_query_match_requires_review(tmp_path, monkeypatch):
    (tmp_path / "needle.txt").write_text("data", encoding="utf-8")
    monkeypatch.setattr(diagnostic, "search_in_file", lambda *_args, **_kwargs: ("OK", 1))
    monkeypatch.setattr(
        diagnostic,
        "_diagnostic_search_policy",
        lambda: {"exclude_hidden": True, "exclude_binary": True, "advanced_settings": {}},
    )
    report = diagnostic.run(tmp_path, repeats=1, max_seconds=None)
    assert report["verdict"] == "REVIEW"
    assert report["scenarios"][0]["matches"] == 1
    assert "unexpected diagnostic-query matches (1)" in diagnostic._markdown(report)


def test_unknown_skip_details_are_reduced_to_safe_categories(tmp_path, monkeypatch):
    source = tmp_path / "access-denied.txt"
    source.write_text("data", encoding="utf-8")
    monkeypatch.setattr(
        diagnostic,
        "search_in_file",
        lambda *_args, **_kwargs: (
            Constants.STATUS_SKIPPED,
            "ERR_UNKNOWN|PermissionError: access denied C:/private/secret.txt",
        ),
    )
    monkeypatch.setattr(
        diagnostic,
        "_diagnostic_search_policy",
        lambda: {"exclude_hidden": True, "exclude_binary": True, "advanced_settings": {}},
    )

    report = diagnostic.run(tmp_path, repeats=1, max_seconds=None)
    encoded = json.dumps(report, ensure_ascii=False)

    assert report["scenarios"][0]["skip_reason_codes"] == {"ERR_UNKNOWN": 1}
    assert report["scenarios"][0]["unknown_skip_categories"] == {"permission_or_access": 1}
    assert "C:/private/secret.txt" not in encoded
    assert "permission_or_access" in diagnostic._markdown(report)


def test_report_counts_files_larger_than_one_gib(tmp_path, monkeypatch):
    large = tmp_path / "large.dat"
    with large.open("wb") as stream:
        stream.seek(1024**3)
        stream.write(b"x")
    monkeypatch.setattr(diagnostic, "search_in_file", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        diagnostic,
        "_diagnostic_search_policy",
        lambda: {"exclude_hidden": True, "exclude_binary": False, "advanced_settings": {}},
    )

    report = diagnostic.run(tmp_path, repeats=1, max_seconds=None)

    assert report["target"]["files_over_1gib"] == 1
    assert report["target"]["size_buckets"][">1GiB"] == 1
    assert report["scenarios"][0]["size_bucket_metrics"][">1GiB"]["files"] == 1
    assert "Files larger than 1 GiB: **1**" in diagnostic._markdown(report)


def test_unknown_skip_categories_do_not_export_raw_details():
    assert diagnostic._unknown_skip_category("ERR_UNKNOWN|Sharing violation (WinError 32)") == "file_in_use"
    assert diagnostic._unknown_skip_category("ERR_UNKNOWN|No such file or directory") == "not_found"
    assert diagnostic._unknown_skip_category("ERR_UNKNOWN|opaque private payload") == "unclassified"
    assert diagnostic._unknown_skip_category("ERR_OPEN|access denied") is None


def test_hidden_files_and_directories_follow_effective_policy(tmp_path, monkeypatch):
    (tmp_path / "visible.txt").write_text("visible", encoding="utf-8")
    (tmp_path / ".hidden.txt").write_text("hidden", encoding="utf-8")
    hidden_dir = tmp_path / ".private"
    hidden_dir.mkdir()
    (hidden_dir / "nested.txt").write_text("hidden", encoding="utf-8")
    monkeypatch.setattr(diagnostic, "search_in_file", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        diagnostic,
        "_diagnostic_search_policy",
        lambda: {"exclude_hidden": True, "exclude_binary": False, "advanced_settings": {}},
    )

    report = diagnostic.run(tmp_path, repeats=1, max_seconds=None)

    assert report["target"]["file_count"] == 1
    assert report["target"]["hidden_files_excluded"] == 2
    assert "visible.txt" not in json.dumps(report)
