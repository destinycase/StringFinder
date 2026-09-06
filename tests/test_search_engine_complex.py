"""
[test_search_engine_complex.py]

이 테스트는 복합 검색 엔진의 고도화된 기능 및 결과 처리 알고리즘을 검증합니다.

- 테스트 목적:
  1. 다중 파일 검색, 배치 처리 및 대규모 결과 셋에 대한 정합성 확인.

- 주요 검증 사항:
  1. 검색 결과 정렬 및 중복 제거 로직.
  2. 대용량 텍스트 파일 내의 다중 키워드 매칭 무결성.
"""

import os
import sys
from typing import cast

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.search_engine import (
    SearchResult,
    _search_text_stream,
    search_in_file,
    search_in_files_batch,
)
from sf_utils.constants import Constants


def _require_search_result(result: object) -> SearchResult:
    assert result is not None
    assert isinstance(result, tuple) and len(result) == 3
    return cast(SearchResult, result)


@pytest.fixture
def complex_file(tmp_path):
    file_path = tmp_path / "complex.txt"
    file_path.write_text("Hier ist das Ma\u00dfe aller Dinge.", encoding="utf-8")
    return str(file_path)


def test_search_in_file_complex_off(complex_file):
    result = search_in_file(complex_file, "Masse", use_complex_search=False)
    assert result is None


def test_search_in_file_complex_on(complex_file):
    result = _require_search_result(search_in_file(complex_file, "Masse", use_complex_search=True))
    _file_path, count, matches = result
    assert count == 1
    assert matches[0][1] == "Hier ist das Ma\u00dfe aller Dinge."


def test_batch_search_complex(complex_file):
    file_batch = [(complex_file, os.path.getsize(complex_file))]

    res_off = search_in_files_batch(file_batch, "Masse", use_complex_search=False)
    assert len(res_off["results"]) == 0

    res_on = search_in_files_batch(file_batch, "Masse", use_complex_search=True)
    assert len(res_on["results"]) == 1
    assert res_on["results"][0][0] == complex_file


@pytest.mark.parametrize("exact_match", [False, True])
def test_unicode_canonical_match_is_invariant_across_file_size_paths(tmp_path, exact_match):
    file_path = tmp_path / "canonical-equivalence.txt"
    file_path.write_text("e\u0301\n", encoding="utf-8")
    special_mode = Constants.MODE_EXACT if exact_match else None

    small_result = search_in_file(
        str(file_path),
        "\u00e9",
        file_size=file_path.stat().st_size,
        special_mode=special_mode,
        use_complex_search=True,
    )
    large_result = search_in_file(
        str(file_path),
        "\u00e9",
        file_size=11 * 1024 * 1024,
        special_mode=special_mode,
        use_complex_search=True,
    )

    assert _require_search_result(small_result)[1] == 1
    assert _require_search_result(large_result)[1] == 1


def test_text_stream_stops_reading_after_the_truncation_marker():
    consumed = 0

    def matching_lines():
        nonlocal consumed
        for _ in range(100):
            consumed += 1
            yield "needle\n"

    matches, count, existence_found, stopped = _search_text_stream(
        matching_lines(),
        "needle",
        exact_match=False,
        existence_only=False,
        stop_event=None,
        max_per_file=2,
    )

    assert consumed == 3
    assert count == 3
    assert len(matches) == 3
    assert matches[-1][0] == -1
    assert existence_found is False
    assert stopped is False
