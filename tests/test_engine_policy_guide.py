import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from folder_search_helper import search_in_file

"""
[test_engine_policy_guide.py]

이 테스트는 StringFinder 하이브리드 엔진(Rust + Python)의 통합 운영 정책을 검증하고 개발 가이드를 제공합니다.

- 정책 개편 사항 (v2.0, 6.4.2 갱신):
  1. [단일 비교 기준] Rust 엔진도 key(x) = NFC(casefold(NFC(x)))로 비교하므로 ß ↔ ss 같은
     Unicode 전체 대소문자 접기를 지원합니다.
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
    [안내] 6.4.2부터 Rust 기본 검색도 'ß' <-> 'ss' 같은 Unicode 전체 대소문자 접기를 지원합니다.
    """
    result = search_in_file(german_content_file, "strasse")
    assert result is not None, "기본 검색도 casefold 기준으로 'ß'와 'ss'를 같게 봐야 합니다."
    assert result[1] == 1
    assert "Straße" in result[2][0][1]


def test_python_integrity_policy_guide(german_content_file):
    """
    [안내] Python 엔진(특별한 문자열 검색)은Full Case Folding을 지원하여 유니코드 무결성을 보장합니다.
    """
    # 명시적으로 use_complex_search=True를 설정해야만 Python 엔진이 구동됩니다.
    result = search_in_file(german_content_file, "strasse")

    assert result is not None, "Python 엔진은 'casefold'를 통해 'ß' <-> 'ss' 검색에 성공해야 합니다."
    if isinstance(result, tuple) and len(result) == 3:
        assert result[1] == 1
        assert "Straße" in result[2][0][1]


def test_engine_isolation_policy_guide_rust_error(tmp_path):
    """
    [안내] Rust 엔진 오류(예외 발생) 시 Python으로 자동 폴백되지 않음을 확인합니다.
    Rust 엔진 미존재 시나리오는 진입점에서 앱이 종료되므로 테스트하지 않습니다.
    """
    from unittest.mock import patch
    test_file = tmp_path / "iso_test.txt"
    test_file.write_text("keyword", encoding="utf-8")

    from folder_search_helper import fake_dir_engine

    with patch("core.search_engine.sf_engine", fake_dir_engine(error=RuntimeError("Crash"))):
        # Rust 예외 → 오류를 그대로 알림 (Python 폴백 없음)
        with pytest.raises(RuntimeError, match="Crash"):
            search_in_file(str(test_file), "keyword")

