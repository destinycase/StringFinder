"""검색 비교·상한: 전체 상한 종료, 바이너리 제외, 정확히 일치 공백, JSON 키 경로."""
import json
import os
import sys

import pytest

from core.worker import SearchWorker
from sf_utils.constants import Constants as C

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
from rust_engine import sf_engine  # noqa: E402


def test_total_limit_is_a_stop_not_an_error():
    worker = SearchWorker({"file_list": [], "search_string": "needle"})
    worker.search_settings_snapshot[C.CONFIG_KEY_MAX_TOTAL_MATCHES] = 1000
    errors, limits = [], []
    worker.signals.error.connect(errors.append)
    worker.signals.total_match_limit_reached.connect(limits.append)
    worker._stop_for_total_match_limit()
    assert limits == [1000]
    assert errors == []
    assert worker.stop_event.is_set()


def test_exactly_reaching_the_limit_is_not_reported_as_reached():
    worker = SearchWorker({"file_list": [], "search_string": "needle"})
    worker._total_matches_accumulated = 7
    worker.search_settings_snapshot[C.CONFIG_KEY_MAX_TOTAL_MATCHES] = 10
    accepted, count, reached = worker._fit_results_to_total_limit([("a.txt", 3, [(1, "a"), (2, "b"), (3, "c")])])
    assert count == 3 and reached is False
    worker._total_matches_accumulated = 10
    _accepted, _count, reached = worker._fit_results_to_total_limit([("b.txt", 1, [(1, "d")])])
    assert reached is True


def test_exact_match_ignores_spaces_around_the_query(tmp_path):
    path = tmp_path / "a.json"
    path.write_text(json.dumps({"k": "needle"}), encoding="utf-8")
    found = sf_engine.search_file(str(path), "  needle ", C.RUST_MODE_JSON | C.RUST_MODE_EXACT)
    assert [m.line for m in found if m.kind == "match"]
    assert not sf_engine.search_file(str(path), "   ", C.RUST_MODE_JSON | C.RUST_MODE_EXACT)


@pytest.mark.parametrize("query", ["needle", "한글"])
def test_binary_files_are_excluded_quickly_for_any_query(tmp_path, query):
    path = tmp_path / "big.bin"
    with open(path, "wb") as handle:
        handle.write(b"\x00\xff" * 512)
        handle.write(bytes(range(256)) * 4096 * 8)
    assert sf_engine.search_file(str(path), query, C.RUST_MODE_EXCLUDE_BINARY) == []


def test_engine_stops_producing_after_the_total_limit(tmp_path):
    from core import search_engine as se

    for index in range(300):
        (tmp_path / f"f{index:03d}.txt").write_text("e\n" * 50, encoding="utf-8")
    batches = []
    result = se.search_directory_fast([str(tmp_path)], "e", ["txt"], results_callback=batches.append,
                                      batch_size=1, max_total_matches=100)
    delivered = sum(len(rows) for batch in batches for _path, rows in batch) + sum(
        count for _path, count, _rows in result["results"])
    # 상한(100)을 넘긴 뒤에는 엔진이 멈추므로 300파일 × 50건을 다 만들지 않습니다.
    assert 100 < delivered < 300 * 50


def test_json_keys_with_slashes_stay_distinguishable(tmp_path):
    from core import search_engine as se

    (tmp_path / "a.json").write_text(json.dumps(
        {"a/b": "needle1", "a": {"b": "needle2"}, "/lead": "needle3", "t~x": "needle4"}), encoding="utf-8")
    result = se.search_directory_fast([str(tmp_path)], "needle", ["json"], special_mode=C.MODE_JSON + " (부분 일치)")
    keys = sorted(row[1] for _path, _count, rows in result["results"] for row in rows)
    assert keys == ["/lead", "a.b", "a/b", "t~x"]


@pytest.mark.parametrize("encoding,word", [("utf-8", "a"), ("cp949", "가"), ("utf-16", "a")])
@pytest.mark.parametrize("query_form", ["{w}", " {w}", "{w} ", "\t{w}"])
def test_exact_query_spaces_on_text_files(tmp_path, encoding, word, query_form):
    from core import search_engine as se

    other = "A" if word == "a" else word
    lines = [word, " " + word, "  " + word + " ", other, "x" + word, "b " + word]
    path = tmp_path / "t.txt"
    path.write_bytes("\r\n".join(lines).encode(encoding))
    query = query_form.format(w=word)
    found = sorted(m.line for m in sf_engine.search_file(str(path), query, C.RUST_MODE_EXACT) if m.kind == "match")
    assert found == [1, 2, 3, 4]
    result = se.search_directory_fast([str(tmp_path)], query, ["txt"], special_mode=C.MODE_EXACT)
    assert sorted(row[0] for _p, _c, rows in result["results"] for row in rows) == [1, 2, 3, 4]


def test_partial_mode_and_blank_exact_query_unchanged(tmp_path):
    path = tmp_path / "t.txt"
    path.write_text("a\n a\n  a \nA\nxa\nb a\n", encoding="utf-8")
    assert sorted(m.line for m in sf_engine.search_file(str(path), " a", 0) if m.kind == "match") == [2, 3, 6]
    assert not sf_engine.search_file(str(path), "   ", C.RUST_MODE_EXACT)
