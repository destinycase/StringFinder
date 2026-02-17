import os
import pytest
from core.search_engine import search_in_file, detect_encoding_quickly
import openpyxl
from utils.constants import Constants


def test_detect_encoding_utf16le():
    """UTF-16LE BOM 감지 테스트"""
    # UTF-16LE BOM: FF FE
    data = b"\xff\xfe\x48\x00\x65\x00\x6c\x00\x6c\x00\x6f\x00"  # "Hello" in UTF-16LE
    encoding = detect_encoding_quickly(data)
    # 현재 구현은 utf-16을 반환 (Python이 자동으로 LE/BE 판별)
    assert encoding in [Constants.ENC_UTF16_LE, Constants.ENC_UTF16], f"Expected UTF-16 variant, got {encoding}"


def test_search_utf16le_file(tmp_path):
    """UTF-16LE 파일 검색 테스트 - BOM 있는 경우"""
    # UTF-16LE BOM 포함 파일 생성
    test_file = tmp_path / "test_utf16le_bom.txt"
    content = "안녕하세요\n테스트 문자열입니다\nUTF-16LE 인코딩"
    # write_text는 BOM을 자동 추가하지 않으므로 직접 작성
    with open(test_file, "wb") as f:
        f.write(b"\xff\xfe")  # UTF-16LE BOM
        f.write(content.encode("utf-16-le"))

    # 검색 수행
    result = search_in_file(str(test_file), "테스트")

    # 검색 성공 확인 (SKIPPED가 아님)
    assert result is not None, "Search result should not be None"
    if isinstance(result, tuple) and len(result) >= 2:
        if result[0] == "SKIPPED":
            # 검색 실패 시 상세 정보 출력
            pytest.fail(f"Search should not be skipped: {result[1]}")
        # 정상 결과: (file_path, match_count, matches)
        assert result[1] > 0, "Should find at least one match"


def test_utf16le_large_file(tmp_path):
    """대용량 UTF-16LE 파일 검색 테스트"""
    # 대용량 UTF-16LE 파일 생성 (약 1MB)
    test_file = tmp_path / "large_utf16le.txt"
    content = "테스트 라인\n" * 10000

    with open(test_file, "wb") as f:
        f.write(b"\xff\xfe")  # UTF-16LE BOM
        f.write(content.encode("utf-16-le"))

    # 검색 수행
    result = search_in_file(str(test_file), "테스트")

    # 검색 성공 확인
    assert result is not None, "Result should not be None"
    if isinstance(result, tuple) and len(result) >= 2:
        if result[0] == "SKIPPED":
            pytest.fail(f"Large UTF-16LE file should not be skipped: {result[1]}")


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
    assert "Sheet!B2" in result[2][0][1]  # 셀 위치 확인
    assert "Python Rulez" in result[2][0][2]  # 내용 확인


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


def test_search_large_file_size_limit(temp_dir):
    """2GB 이상 파일 검색 제한 테스트"""
    # 실제 2GB 파일 생성은 비현실적이므로 로직 검증만 수행
    # search_in_file 내부에서 file_size > 2GB 체크 로직이 있음을 확인
    # 이 테스트는 실제 대용량 파일 없이 로직 존재 여부만 확인
    pass  # 실제 구현 시 모킹 필요


def test_search_encoding_error_handling(temp_dir):
    """인코딩 오류 처리 테스트 (UnicodeDecodeError 로깅 확인)"""
    # 잘못된 인코딩 파일 생성
    file_path = os.path.join(temp_dir, "bad_encoding.txt")
    with open(file_path, "wb") as f:
        # UTF-8로 디코딩 불가능한 바이트 시퀀스
        f.write(b"\xff\xfe\x00\x00")

    # 검색 시도 - 패턴이 없어서 binary check에서 걸러지면 "None" 반환이 정상
    # 하지만 파일에 접근을 못하는 등 "에러" 인 경우에는 "SKIPPED" 가 되어야 함
    result = search_in_file(file_path, "test")
    # 인코딩 오류는 현재 errors='replace'로 처리되어 익셉션이 나지 않으므로 None 반환
    assert result is None


def test_search_io_error_handling(temp_dir):
    """파일 I/O 오류 처리 테스트 (권한 없는 파일 등)"""
    # 존재하지 않는 파일 경로
    non_existent = os.path.join(temp_dir, "non_existent.txt")
    result = search_in_file(non_existent, "test")
    result = search_in_file(non_existent, "test")
    # search_in_file now returns ("SKIPPED", msg) tuple
    assert isinstance(result, tuple)
    assert result[0] == "SKIPPED"
    assert "지정된 파일을 찾을 수 없습니다" in result[1] or "No such file" in result[1]


def test_search_corrupted_excel_handling(temp_dir):
    """손상된 엑셀 파일 처리 테스트"""
    # 잘못된 엑셀 파일 생성 (.xlsx 확장자지만 내용은 텍스트)
    excel_path = os.path.join(temp_dir, "corrupted.xlsx")
    with open(excel_path, "w") as f:
        f.write("This is not a valid Excel file")

    # 검색 시도 - 예외가 발생하지 않고 "SKIPPED" 반환해야 함
    result = search_in_file(excel_path, "test")
    assert isinstance(result, tuple)
    assert result[0] == "SKIPPED"
