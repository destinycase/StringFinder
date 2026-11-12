"""벤치마크 도구(tools/benchmark.py)의 측정·비교 계약.

실제 표본(수 GB)은 만들지 않고, 작은 폴더로 측정 경로와 기준선 비교 판정만 확인합니다.
"""
import json

import pytest

from core import search_engine as se
from tools import benchmark


def test_sample_ids_are_unique_and_described():
    ids = [s.sid for s in benchmark.SAMPLES]
    assert len(ids) == len(set(ids))
    assert all(s.what and s.paths and s.query for s in benchmark.SAMPLES)


@pytest.fixture
def tiny_data(tmp_path, monkeypatch):
    (tmp_path / "set_a").mkdir()
    (tmp_path / "set_a" / "one.txt").write_text("앞 target_keyword_a 뒤\n둘째 줄\n", encoding="utf-8")
    (tmp_path / "set_a" / "two.txt").write_text("없음\n", encoding="utf-8")
    sample = benchmark.Sample("A", "작은 폴더", ("set_a",), "target_keyword_a")
    monkeypatch.setattr(benchmark, "SAMPLES", (sample,))
    return tmp_path


def test_run_discards_warmup_and_reports_stable_digest(tiny_data):
    result = benchmark.run(3, None, data_dir=tiny_data, engine=se.sf_engine)
    row = result["samples"]["A"]
    assert result["rounds"] == 3 and row["hits"] == 1
    assert row["min_ms"] <= row["median_ms"] <= row["max_ms"]
    # 지문은 데이터 폴더 위치와 무관합니다(상대 경로로 계산).
    _ms, hits, digest = benchmark.measure_once(se.sf_engine, benchmark.SAMPLES[0], tiny_data)
    assert (hits, digest) == (row["hits"], row["digest"])


def test_missing_samples_stop_before_measuring(tmp_path):
    with pytest.raises(SystemExit, match="prepare"):
        benchmark.run(2, ["A"], data_dir=tmp_path, engine=se.sf_engine)


def test_compare_flags_slowdown_and_result_change():
    base = {"samples": {"X": {"median_ms": 100.0, "hits": 5, "digest": "d"},
                        "Y": {"median_ms": 100.0, "hits": 5, "digest": "d"}}}
    now = {"samples": {"X": {"median_ms": 110.0, "hits": 5, "digest": "d"},
                       "Y": {"median_ms": 130.0, "hits": 4, "digest": "e"},
                       "Z": {"median_ms": 1.0, "hits": 1, "digest": "z"}}}
    lines = benchmark.compare(now, base, 1.15)
    assert not lines[0].endswith(("느려짐", "결과 다름"))
    assert "결과 다름" in lines[1] and lines[1].endswith("느려짐")
    assert "기준선 없음" in lines[2]


def test_baseline_file_matches_sample_set():
    """기준선 파일이 있으면 모든 표본의 값을 담고 있어야 합니다."""
    if not benchmark.BASELINE.exists():
        pytest.skip("기준선 파일이 아직 없습니다")
    baseline = json.loads(benchmark.BASELINE.read_text(encoding="utf-8"))
    assert set(baseline["samples"]) == {s.sid for s in benchmark.SAMPLES}
