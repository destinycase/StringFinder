import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.search_engine import HAS_RUST_ENGINE, search_in_file

"""
[test_engine_policy_guide.py]

이 테스트는 StringFinder 하이브리드 엔진(Rust + Python)의 유니코드 처리 정책을 검증하고 개발 가이드를 제공합니다.

- 테스트 목적:
  1. Rust 엔진이 '성능'을 위해 단순 대소문자 변환만 수행함을 입증 (독일어 ß 미지원 의도 확인).
  2. Python 엔진이 '무결성'을 위해 Full Case Folding을 지원함을 입증 (독일어 ß 지원 확인).

- 주요 검증 사항:
  1. Rust 엔진 검색 시 'ß' 문자에 대해 'ss' 검색 결과가 나오지 않아야 함 (Simple Case Folding 정책).
  2. Python 복합 검색 시 'ß' 문자에 대해 'ss' 검색 결과가 정상적으로 도출되어야 함 (Full Case Folding 정책).
"""


@pytest.fixture
def german_content_file(tmp_path):
    """독일어 'Straße' (Street)가 포함된 테스트 파일을 생성합니다."""
    file_path = tmp_path / "german_test.txt"
    # 'Straße'에는 'ß'가 포함되어 있으며, 유니코드 상 'ss'와 대응됩니다.
    file_path.write_text("Die Straße ist sehr lang.", encoding="utf-8")
    return str(file_path)


@pytest.mark.skipif(not HAS_RUST_ENGINE, reason="Rust 엔진이 로드된 환경에서만 정확한 정책 검증이 가능합니다.")
def test_rust_engine_performance_policy(german_content_file):
    """
    [안내] Rust 엔진은 고속 처리를 위해 단순 대소문자 변환만 수행합니다.
    따라서 'ß'가 포함된 파일에서 'ss'를 검색할 때 검색 결과가 나오지 않는 것이 '정상'입니다.
    이는 한글/영어 검색의 성능을 보장하기 위한 정책적 선택입니다.
    """
    # 'Straße'가 포함된 파일에서 'strasse' (ss)를 검색합니다.
    # Rust 엔진(use_complex_search=False) 사용 시.
    result = search_in_file(german_content_file, "strasse", use_complex_search=False)

    # 결과가 None인 것은 Rust 엔진이 이 복합 변환을 지원하지 않고 건너뛰었음을 의미합니다.
    assert result is None, "Rust 엔진은 성능을 위해 'ß' <-> 'ss' 변환 검색을 수행하지 않아야 합니다."


def test_python_engine_integrity_policy(german_content_file):
    """
    [안내] Python 엔진(특별한 문자열 검색)은 유니코드 무결성을 보장합니다.
    'casefold()'를 사용하여 'ß'와 'ss'를 동일하게 처리하므로 정확한 검색이 가능합니다.
    """
    # 'Straße'가 포함된 파일에서 'strasse' (ss)를 검색합니다.
    # Python 엔진(use_complex_search=True) 사용 시.
    result = search_in_file(german_content_file, "strasse", use_complex_search=True)

    # 결과가 존재해야 하며, 매치된 내용이 'Straße'임을 확인합니다.
    assert result is not None, "Python 엔진은 'casefold'를 통해 'ß' <-> 'ss' 검색에 성공해야 합니다."

    # [수정] search_in_file은 SearchResult(3-tuple) 또는 SkippedResult(2-tuple)를 반환할 수 있음
    # 여기서는 성공을 기대하므로 3-tuple 데이터 정합성 확인
    if isinstance(result, tuple) and len(result) == 3:
        _path, count, matches = result
        assert count == 1
        assert "Straße" in matches[0][1]
    else:
        pytest.fail(f"Expected SearchResult (3-tuple), but got {type(result)}: {result}")
