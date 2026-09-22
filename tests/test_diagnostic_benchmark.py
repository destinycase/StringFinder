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

    def fake_search(path, _query, **_kwargs):
        if path.endswith(".xlsx"):
            return (Constants.STATUS_SKIPPED, AppStrings.SKIP_REASON_EXCEL_CELL_LIMIT.format(500_000))
        return None

    monkeypatch.setattr(diagnostic, "search_in_file", fake_search)
    report = diagnostic.run(tmp_path, repeats=1, progress_callback=lambda *args: progress.append(args), max_seconds=None)

    assert report["diagnostic_version"] == 3
    assert report["status"] == "completed"
    assert report["verdict"] == "REVIEW"
    assert report["target"]["file_count"] == 3
    assert report["diagnostic_config"]["completed_work_units"] == 12
    assert len(progress) == 12
    assert report["scenarios"][1]["skip_reason_codes"] == {"INFO_EXCEL_CELL_LIMIT": 1}
    assert "(other)" in report["target"]["extensions"]
    encoded = json.dumps(report, ensure_ascii=False)
    markdown = diagnostic._markdown(report)
    assert "confidential-project-name" not in encoded
    assert "customerproject" not in encoded
    assert str(tmp_path) not in encoded
    assert "Normal search - no match" in markdown


def test_timeout_returns_a_readable_partial_report(tmp_path, monkeypatch):
    (tmp_path / "not-searched.txt").write_text("data", encoding="utf-8")
    monkeypatch.setattr(diagnostic, "search_in_file", lambda *_args, **_kwargs: None)
    report = diagnostic.run(tmp_path, repeats=5, max_seconds=0)
    assert report["status"] == "timed_out"
    assert report["verdict"] == "INCOMPLETE"
    assert report["scenarios"][0]["status"] == "timed_out"
    assert "INCOMPLETE" in diagnostic._markdown(report)


def test_short_durations_are_reported_in_milliseconds():
    assert diagnostic._duration(0.015) == "15.00ms"
    assert diagnostic._duration(1.25) == "1.25s"
    assert diagnostic._format_bytes(12) == "12 bytes"
    assert diagnostic._format_bytes(1024) == "1.00 KiB"


def test_unexpected_fixed_query_match_requires_review(tmp_path, monkeypatch):
    (tmp_path / "needle.txt").write_text("data", encoding="utf-8")
    monkeypatch.setattr(diagnostic, "search_in_file", lambda *_args, **_kwargs: ("OK", 1))
    report = diagnostic.run(tmp_path, repeats=1, max_seconds=None)
    assert report["verdict"] == "REVIEW"
    assert report["scenarios"][0]["matches"] == 1
    assert "fixed query match 1" in diagnostic._markdown(report)
