import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.search_engine import search_in_file

"""
[test_engine_policy_guide.py]

이 테스트는 StringFinder 하이브리드 엔진(Rust + Python)의 통합 운영 정책을 검증하고 개발 가이드를 제공합니다.

- 정책 개편 사항 (v2.0):
  1. [성능 우선] Rust 엔진은 단순 대소문자 변환만 수행하며, 복합 유니코드(ß 등)는 지원하지 않습니다.
  2. [명시적 선택] Python 엔진은 오직 '특별한 문자열 검색' 옵션 활성화 시에만 동작합니다.
  3. [폴백 금지] Rust 엔진 부재 시나 오류 발생 시 Python으로 자동 전환되지 않습니다. (파일 스킵 처리)
"""

@pytest.fixture
def german_content_file(tmp_path):
    """독일어 'Straße' (Street)가 포함된 테스트 파일을 생성합니다."""
    file_path = tmp_path / "german_test.txt"
    file_path.write_text("Die Straße ist sehr lang.", encoding="utf-8")
    return str(file_path)

def test_rust_performance_policy_guide(german_content_file):
    """
    [안내] Rust 엔진은 성능을 위해 'ß' <-> 'ss' 와 같은 복합 유니코드 폴딩을 지원하지 않습니다.
    """
    # Rust 모드(use_complex_search=False)로 검색 시 결과가 없음(None)이 명시적 정책입니다.
    # 이전에는 여기서 Python으로 자동 폴백되었으나, 이제는 None을 반환하고 사용자 엔진 선택을 기다립니다.
    result = search_in_file(german_content_file, "strasse", use_complex_search=False)
    assert result is None, "복합 검색 옵션이 없으면 Rust는 복합 유니코드를 매칭하지 않고 종료해야 합니다."

def test_python_integrity_policy_guide(german_content_file):
    """
    [안내] Python 엔진(특별한 문자열 검색)은Full Case Folding을 지원하여 유니코드 무결성을 보장합니다.
    """
    # 명시적으로 use_complex_search=True를 설정해야만 Python 엔진이 구동됩니다.
    result = search_in_file(german_content_file, "strasse", use_complex_search=True)
    
    assert result is not None, "Python 엔진은 'casefold'를 통해 'ß' <-> 'ss' 검색에 성공해야 합니다."
    if isinstance(result, tuple) and len(result) == 3:
        assert result[1] == 1
        assert "Straße" in result[2][0][1]

def test_engine_isolation_policy_guide(tmp_path):
    """
    [안내] Rust 엔진이 없는 환경에서 일반 검색 시 Python으로 자동 폴백되지 않음을 확인합니다.
    """
    test_file = tmp_path / "iso_test.txt"
    test_file.write_text("keyword", encoding="utf-8")
    
    with patch("core.search_engine.HAS_RUST_ENGINE", False):
        result = search_in_file(str(test_file), "keyword", use_complex_search=False)
        assert result is None, "정책상 Rust가 없고 복합 검색 모드가 아니면 Python이 구동되지 않아야 합니다."
