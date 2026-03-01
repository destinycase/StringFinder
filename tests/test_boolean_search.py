"""
[test_boolean_search.py]

이 테스트는 '존재 여부만 확인'(existence_only) 모드, 즉 불리언 검색의 동작을 검증합니다.

- 테스트 목적:
  1. 검색어 발견 시 전체 매칭을 수행하지 않고 즉시 성공 반환 여부 확인 (성능 최적화).
  2. UTF-8, UTF-16LE 등 다양한 인코딩 환경에서의 불리언 검색 무결성 보장.

- 주요 검증 사항:
  1. 매치 발견 시 `AppStrings.BOOLEAN_SEARCH_MATCH_CONTENT` 반환 여부.
  2. 매치가 없을 때 `None` 반환 여부.
  3. BOM이 포함된 파일에서의 불리언 검색 정확도.
"""

from core.search_engine import search_in_file
from sf_utils.app_strings import AppStrings

def test_boolean_search_basic(tmp_path):
    f_path = tmp_path / "test.txt"
    f_path.write_text("hello world\nthis is a test\npython is great", encoding="utf-8")
    
    # 일반 검색 (매치 1개)
    res = search_in_file(str(f_path), "python", existence_only=False)
    assert res is not None
    assert res[1] == 1
    assert res[2][0][1] == "python is great"
    
    # Boolean 검색 (매치 1개 이상 존재 시 즉시 종료)
    res_bool = search_in_file(str(f_path), "python", existence_only=True)
    assert res_bool is not None
    assert res_bool[1] == 1
    assert res_bool[2][0][1] == AppStrings.BOOLEAN_SEARCH_MATCH_CONTENT

def test_boolean_search_no_match(tmp_path):
    f_path = tmp_path / "test.txt"
    f_path.write_text("hello world", encoding="utf-8")
    
    res = search_in_file(str(f_path), "python", existence_only=True)
    assert res is None

def test_boolean_search_bom_utf8(tmp_path):
    # UTF-8 with BOM
    f_path = tmp_path / "utf8_bom.txt"
    content = "안녕하세요 BOM 테스트입니다."
    f_path.write_bytes(b'\xef\xbb\xbf' + content.encode('utf-8'))
    
    res = search_in_file(str(f_path), "안녕하세요", existence_only=True)
    assert res is not None
    assert res[2][0][1] == AppStrings.BOOLEAN_SEARCH_MATCH_CONTENT

def test_boolean_search_bom_utf16le(tmp_path):
    # UTF-16 LE with BOM
    f_path = tmp_path / "utf16le_bom.txt"
    content = "Hello UTF-16 LE with BOM"
    f_path.write_bytes(b'\xff\xfe' + content.encode('utf-16-le'))
    
    # Rust 엔진은 UTF-16을 직접 처리하지 않고 Python 폴백을 사용할 수 있으므로 확인 필요
    # 현재 정책상 use_complex_search=False여도 Rust가 지원하지 않는 인코딩은 Python 폴백
    res = search_in_file(str(f_path), "Hello", existence_only=True)
    assert res is not None
    # Python 폴백 시에도 existence_only 옵션이 전달되어야 함
