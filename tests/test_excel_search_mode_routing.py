"""
[test_excel_complex_bugfix.py]

이 테스트는 Excel 검색 시 '복합 검색' 옵션 인자가 누락되어 의도와 다르게 작동하던 버그(v4.38.3 해결)를 검증합니다.

- 테스트 목적:
  1. Excel 파일 검색 호출 시 `use_complex_search` 옵션이 내부 전문 함수까지 누락 없이 전달되는지 확인.
  2. 복합 검색 옵션 활성화 시, 성능 위주의 Rust 엔진을 우회하고 정밀한 Python 엔진을 사용하는지 확인.

- 주요 검증 사항:
  1. 확장자 기반 자동 분기 시 인자 전달 여부.
  2. Excel 특수 모드 강제 지정 시 인자 전달 여부.
  3. 복합 검색 옵션에 따른 Rust 엔진 사용/우회 조건문 작동 여부.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# 프로젝트 환경 설정
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.search_engine import search_in_file
from sf_utils.constants import Constants


@pytest.fixture
def mock_excel_file(tmp_path):
    """더미 엑셀 파일을 생성합니다 (확장자 테스트용)."""
    file_path = tmp_path / "test.xlsx"
    file_path.write_text("Dummy content", encoding="utf-8")
    return str(file_path)


def test_excel_extension_flow_passes_complex_arg(mock_excel_file):
    """확장자 기반 Excel 검색 시 use_complex_search 인자가 전달되는지 검증합니다."""
    with patch("core.search_engine.search_in_excel_special") as mock_excel:
        mock_excel.return_value = None

        # 1. use_complex_search=True 전달 시
        search_in_file(mock_excel_file, "query", use_complex_search=True)
        # 호출 인자 확인
        args, kwargs = mock_excel.call_args
        assert kwargs.get("use_complex_search") is True, (
            "확장자 기반 호출 시 use_complex_search=True가 전달되어야 합니다."
        )

        # 2. use_complex_search=False 전달 시 (또는 기본값)
        search_in_file(mock_excel_file, "query", use_complex_search=False)
        args, kwargs = mock_excel.call_args
        # 기본인자 또는 명시적 False 확인
        assert kwargs.get("use_complex_search") is False, (
            "확장자 기반 호출 시 use_complex_search=False가 전달되어야 합니다."
        )


def test_excel_special_mode_flow_passes_complex_arg(mock_excel_file):
    """MODE_EXCEL 특수 모드 검색 시 use_complex_search 인자가 전달되는지 검증합니다."""
    with patch("core.search_engine.search_in_excel_special") as mock_excel:
        mock_excel.return_value = None

        # 특수 모드로 Excel 검색 호출
        search_in_file(mock_excel_file, "query", special_mode=Constants.MODE_EXCEL, use_complex_search=True)

        args, kwargs = mock_excel.call_args
        assert kwargs.get("use_complex_search") is True, (
            "특수 모드 기반 호출 시 use_complex_search=True가 전달되어야 합니다."
        )


def test_excel_rust_bypass_when_complex_on():
    """use_complex_search=True일 때 Excel 검색에서 Rust 엔진을 우회하는지 검증합니다."""
    # search_in_excel_special 내부의 HAS_RUST_ENGINE과 sf_engine.search_file을 고려하여 테스트
    from core import search_engine

    # 더미 파일 경로 (실제 파일 존재 여부는 시그니처 체크에서 중요함)
    test_file = "dummy.xlsx"

    with (
        patch("core.search_engine._check_excel_signature") as mock_sig,
        patch("core.search_engine.HAS_RUST_ENGINE", True),
        patch("core.search_engine.sf_engine") as mock_rust_engine,
        patch("python_calamine.CalamineWorkbook") as mock_calamine,
    ):
        mock_sig.return_value = (True, None)
        mock_calamine.from_path.return_value = MagicMock()

        # 1. use_complex_search=True 인 경우 -> Rust 엔진(sf_engine.search_file)을 호출하지 않아야 함
        search_engine.search_in_excel_special(test_file, "query", use_complex_search=True)
        assert not mock_rust_engine.search_file.called, "복합 검색 활성화 시 Rust 엔진을 우회해야 합니다."

        # 2. use_complex_search=False 인 경우 -> Rust 엔진이 호출되어야 함
        search_engine.search_in_excel_special(test_file, "query", use_complex_search=False)
        assert mock_rust_engine.search_file.called, "복합 검색 비활성화 시 Rust 엔진이 사용되어야 합니다."
