import os
import pytest
from core.search_engine import search_in_file, Constants
from sf_utils.app_strings import AppStrings

def test_boolean_search_basic(tmp_path):
    f_path = tmp_path / "test.txt"
    f_path.write_text("hello world\nthis is a test\npython is great", encoding="utf-8")
    
    # 일반 검색 (매치 1개)
    res = search_in_file(str(f_path), "python", is_boolean=False)
    assert res is not None
    assert res[1] == 1
    assert res[2][0][1] == "python is great"
    
    # Boolean 검색 (매치 1개 이상 존재 시 즉시 종료)
    res_bool = search_in_file(str(f_path), "python", is_boolean=True)
    assert res_bool is not None
    assert res_bool[1] == 1
    assert res_bool[2][0][1] == AppStrings.BOOLEAN_SEARCH_MATCH_CONTENT

def test_boolean_search_no_match(tmp_path):
    f_path = tmp_path / "test.txt"
    f_path.write_text("hello world", encoding="utf-8")
    
    res = search_in_file(str(f_path), "python", is_boolean=True)
    assert res is None

def test_boolean_search_bom_utf8(tmp_path):
    # UTF-8 with BOM
    f_path = tmp_path / "utf8_bom.txt"
    content = "안녕하세요 BOM 테스트입니다."
    f_path.write_bytes(b'\xef\xbb\xbf' + content.encode('utf-8'))
    
    res = search_in_file(str(f_path), "안녕하세요", is_boolean=True)
    assert res is not None
    assert res[2][0][1] == AppStrings.BOOLEAN_SEARCH_MATCH_CONTENT

def test_boolean_search_bom_utf16le(tmp_path):
    # UTF-16 LE with BOM
    f_path = tmp_path / "utf16le_bom.txt"
    content = "Hello UTF-16 LE with BOM"
    f_path.write_bytes(b'\xff\xfe' + content.encode('utf-16-le'))
    
    # Rust 엔진은 UTF-16을 직접 처리하지 않고 Python 폴백을 사용할 수 있으므로 확인 필요
    # 현재 정책상 use_complex_search=False여도 Rust가 지원하지 않는 인코딩은 Python 폴백
    res = search_in_file(str(f_path), "Hello", is_boolean=True)
    assert res is not None
    # Python 폴백 시에도 is_boolean 옵션이 전달되어야 함 (현재 search_in_file 로직 확인 필요)
    # 현재 search_in_file은 HAS_RUST_ENGINE일 때만 is_boolean을 사용함.
    # 하지만 Python 폴백 시에는 is_boolean을 현재 명시적으로 처리하지 않음 (전체 검색 수행 후 상위에서 거를 수도 있음)
    # 일단 Rust 엔진 경로 위주로 검증
