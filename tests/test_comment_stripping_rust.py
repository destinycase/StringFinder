"""
[test_comment_stripping_rust.py]

이 테스트는 Rust 엔진(`sf_engine`)의 소스 코드 주석 제거(Comment Stripping) 기능을 검증합니다.

- 테스트 목적:
  1. 소스 코드 파일(Python, C/C++ 등) 검색 시 주석 내에 포함된 키워드를 검색 결과에서 배제하는지 확인.
  2. 실제 실행 코드 영역의 키워드는 정확히 탐지하는지 검증.

- 주요 검증 사항:
  1. Python 스타일 주석(#) 및 인라인 주석 제거 여부.
  2. C-Style 블록 주석(/* */) 및 라인 주석(//) 제거 여부.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from sf_engine import search_file


@pytest.mark.xfail(
    reason="소스 코드 주석 제거(strip_comments) 기능은 데이터 무결성 보장을 위해 의도적으로 제거됨. 원본 검색 결과를 반환하는 것이 올바른 동작.",
    strict=True
)
def test_rust_comment_stripping(tmp_path):
    py_content = """# This is a comment
print("hello") # inline comment
"""
    py_file = tmp_path / "test.py"
    py_file.write_text(py_content, encoding="utf-8")

    results = search_file(str(py_file), "comment")
    assert len(results) == 0, f"Comments should have been stripped, but found: {results}"

    results = search_file(str(py_file), "hello")
    assert len(results) == 1
    assert results[0].line == 2
    assert "hello" in results[0].content


@pytest.mark.xfail(
    reason="소스 코드 주석 제거(strip_comments) 기능은 데이터 무결성 보장을 위해 의도적으로 제거됨. 원본 검색 결과를 반환하는 것이 올바른 동작.",
    strict=True
)
def test_rust_comment_stripping_c_style(tmp_path):
    c_content = """/* block 
comment */
void main() {
    // inline comment
    printf("test");
}
"""
    c_file = tmp_path / "test.c"
    c_file.write_text(c_content, encoding="utf-8")

    results = search_file(str(c_file), "comment")
    assert len(results) == 0

    results = search_file(str(c_file), "test")
    assert len(results) == 1
    assert "test" in results[0].content


if __name__ == "__main__":
    try:
        test_rust_comment_stripping(pytest.importorskip("pathlib").Path("."))
        print("Python comment test passed")
        test_rust_comment_stripping_c_style(pytest.importorskip("pathlib").Path("."))
        print("C comment test passed")
    except Exception as e:
        print(f"Test failed: {e}")
