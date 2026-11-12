"""유니코드 비교(ß = ss, 결합형 é = 완성형 é)가 파일 크기에 따른 읽기 경로와 관계없이 같은지 확인합니다."""

import os
import sys
from typing import cast

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.search_engine import SearchResult
from folder_search_helper import search_in_file
from sf_utils.constants import Constants


def _require_search_result(result: object) -> SearchResult:
    assert result is not None
    assert isinstance(result, tuple) and len(result) == 3
    return cast(SearchResult, result)


@pytest.fixture
def sharp_s_file(tmp_path):
    file_path = tmp_path / "sharp_s.txt"
    file_path.write_text("Hier ist das Ma\u00dfe aller Dinge.", encoding="utf-8")
    return str(file_path)


def test_full_case_folding_matches_sharp_s(sharp_s_file):
    result = _require_search_result(search_in_file(sharp_s_file, "Masse"))
    _file_path, count, matches = result
    assert count == 1
    assert matches[0][1] == "Hier ist das Ma\u00dfe aller Dinge."



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
    )
    large_result = search_in_file(
        str(file_path),
        "\u00e9",
        file_size=11 * 1024 * 1024,
        special_mode=special_mode,
    )

    assert _require_search_result(small_result)[1] == 1
    assert _require_search_result(large_result)[1] == 1


