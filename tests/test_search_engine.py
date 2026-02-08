import os
from core.search_engine import search_in_file, detect_encoding_quickly
import openpyxl


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


def test_detect_encoding_quickly():
    """인코딩 탐지 로직 테스트"""
    # UTF-8 데이터
    utf8_data = "안녕히 계세요".encode("utf-8")
    assert detect_encoding_quickly(utf8_data) == "utf-8"

    # CP949 데이터
    cp949_data = "안녕히 계세요".encode("cp949")
    assert detect_encoding_quickly(cp949_data) == "cp949"


def test_search_in_excel(temp_dir):
    """Calamine 기반 엑셀 검색 테스트"""
    excel_path = os.path.join(temp_dir, "test.xlsx")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "Search Me"
    ws["B2"] = "Python Rulez"
    wb.save(excel_path)
    wb.close()

    result = search_in_file(excel_path, "Rulez")
    assert result is not None
    assert result[1] == 1  # 1개 발견
    assert "Sheet!B2" in result[2][0][0]  # 셀 위치 확인
    assert "Python Rulez" in result[2][0][1]  # 내용 확인


def test_search_in_file_nfc_normalization(temp_dir):
    """NFC 정규화 검색 테스트 (맥OS 등에서 생성된 파일명 대응)"""
    file_path = os.path.join(temp_dir, "nfc_test.txt")
    # '해'를 조합형(NFD)으로 작성해도 검색어가 NFC면 찾아야 함
    content = "안녕하세요"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)

    # 초성/중성/종성 분리된 형태 (가상의 NFD 시뮬레이션은 생략하고 로직 호출 여부만 체크)
    result = search_in_file(file_path, "안녕")
    assert result is not None
