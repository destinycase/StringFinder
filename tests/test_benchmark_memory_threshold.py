"""
벤치마크 메모리 기준 테스트.

메모리 기준은 프로그램 전용 메모리(Peak Private)로 판정합니다. 작업 집합(RSS)은
메모리 매핑한 파일 페이지까지 포함해 큰 파일 검색에서 파일 크기만큼 커지므로,
참고용으로만 기록합니다.
"""

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.benchmark_performance as benchmark_performance  # noqa: E402

SET_D = "Set D: Large Full Scan"


def _result(peak_rss: float, peak_private: float) -> dict:
    return {
        "dataset": SET_D,
        "total_time": 0.2,
        "latency": 0.2,
        "jitter": 0.0,
        "results_count": 1,
        "skipped_count": 0,
        "skipped_reasons": {},
        "peak_rss": peak_rss,
        "peak_private": peak_private,
    }


def test_large_working_set_with_small_private_memory_passes(capsys) -> None:
    # 500MiB 파일 매핑으로 작업 집합만 커진 경우는 실패가 아닙니다.
    benchmark_performance.check_thresholds([_result(peak_rss=580.0, peak_private=30.0)])
    assert "[FAIL]" not in capsys.readouterr().out


def test_private_memory_over_threshold_fails(capsys) -> None:
    with pytest.raises(SystemExit):
        benchmark_performance.check_thresholds([_result(peak_rss=200.0, peak_private=151.0)])
    assert "Peak Private 151.0 MB > 150.0 MB" in capsys.readouterr().out


def test_history_parses_old_and_new_rows() -> None:
    old = benchmark_performance._parse_history_line(
        "| 2026-11-02 00:29:36 | v6.2.3-r1 | Set D: Large Full Scan | 0.232s | 0.231s | 0.0000 | 540.1 MB | 1 | 0 |  |"
    )
    assert old is not None
    assert old["Peak RSS"] == "540.1 MB"
    assert old["Peak Private"] == ""
    assert old["Hits"] == "1"

    new = benchmark_performance._parse_history_line(
        "| 2026-11-06 09:00:00 | v6.4.1 | Set D: Large Full Scan | 0.204s | 0.203s | 0.0000 | 553.3 MB | 30.2 MB | 1 | 0 |  |"
    )
    assert new is not None
    assert new["Peak RSS"] == "553.3 MB"
    assert new["Peak Private"] == "30.2 MB"
    assert new["Hits"] == "1"
    assert new["Skipped"] == "0"


def test_history_save_keeps_old_rows_and_adds_private_column(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(benchmark_performance, "project_root", tmp_path)
    history = tmp_path / "docs" / "benchmark_history.md"
    history.parent.mkdir()
    history.write_text(
        "# History\n\n"
        "| Time | Tag | Dataset | Total Time | Latency | Jitter | Peak RSS | Hits | Skipped | Skip Reasons |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| 2026-11-02 00:29:36 | old | Set D: Large Full Scan | 0.232s | 0.231s | 0.0000 | 540.1 MB | 1 | 0 |  |\n",
        encoding="utf-8",
    )

    benchmark_performance.save_benchmark_history([_result(peak_rss=553.3, peak_private=30.2)], tag="new")

    _, rows = benchmark_performance._load_history_rows(history)
    assert [row["Tag"] for row in rows] == ["old", "new"]
    assert rows[0]["Peak RSS"] == "540.1 MB" and rows[0]["Peak Private"] == ""
    assert rows[1]["Peak RSS"] == "553.3 MB" and rows[1]["Peak Private"] == "30.2 MB"
    assert "| Peak RSS | Peak Private | Hits |" in history.read_text(encoding="utf-8")


def test_history_save_without_private_value_leaves_column_blank(tmp_path, monkeypatch) -> None:
    # 다른 측정 도구는 전용 메모리 없이 기록할 수 있습니다.
    monkeypatch.setattr(benchmark_performance, "project_root", tmp_path)
    result = _result(peak_rss=100.0, peak_private=0.0)
    del result["peak_private"]

    benchmark_performance.save_benchmark_history([result], tag="excel")

    _, rows = benchmark_performance._load_history_rows(tmp_path / "docs" / "benchmark_history.md")
    assert rows[0]["Peak RSS"] == "100.0 MB"
    assert rows[0]["Peak Private"] == ""
