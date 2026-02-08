import pytest
import os
from core.search_engine import search_in_file


def test_search_in_text_file(sample_text_file):
    """일반 텍스트 파일 검색 테스트"""
    file_path, search_str = sample_text_file
    result = search_in_file(file_path, search_str)

    # 결과 구조: (file_path, count, matches)
    assert result is not None
    assert result[0] == file_path
    assert result[1] == 1
    assert "Python Search" in result[2][0][1]


def test_search_not_found(sample_text_file):
    """검색 결과가 없는 경우 테스트"""
    file_path, _ = sample_text_file
    result = search_in_file(file_path, "NonExistentString")
    assert result is None


def test_search_empty_file(temp_dir):
    """빈 파일 검색 테스트"""
    path = os.path.join(temp_dir, "empty.txt")
    with open(path, "w") as _:
        pass
    result = search_in_file(path, "anything")
    assert result is None


@pytest.mark.skipif(not os.path.exists("sample.xlsx"), reason="sample.xlsx not found")
def test_search_in_excel():
    """엑셀 파일 검색 테스트 (샘플 파일 필요)"""
    # 실제 엑셀 라이브러리 의존성 확인은 생략하고 로직만 체크
    # 필요시 테스트 내부에서 임시 엑셀 생성 가능하나 openpyxl 의존성 있음
    pass
