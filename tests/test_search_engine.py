"""일반 텍스트 검색: UTF-16, 큰 파일, 없음, 빈 파일 등 기본 동작."""

import os

import openpyxl

from folder_search_helper import search_in_file
from sf_utils.constants import Constants


def test_search_utf16le_file(tmp_path):
    test_file = tmp_path / "test_utf16le_bom.txt"
    content = "Hello\nTest String\nUTF-16LE Encoding"
    with open(test_file, "wb") as f:
        f.write(b"\xff\xfe")
        f.write(content.encode("utf-16-le"))

    result = search_in_file(str(test_file), "Test")
    assert result is not None
    assert isinstance(result[1], int)
    assert result[1] > 0


def test_utf16le_large_file(tmp_path):
    test_file = tmp_path / "large_utf16le.txt"
    content = "Test Line\n" * 1000
    with open(test_file, "wb") as f:
        f.write(b"\xff\xfe")
        f.write(content.encode("utf-16-le"))

    result = search_in_file(str(test_file), "Test")
    assert result is not None


def test_search_in_text_file(sample_text_file):
    file_path, search_str = sample_text_file
    result = search_in_file(file_path, search_str)
    assert result is not None
    assert result[0] == file_path
    assert result[1] == 1


def test_search_not_found(sample_text_file):
    file_path, _ = sample_text_file
    result = search_in_file(file_path, "NonExistentString")
    assert result is None


def test_search_empty_file(temp_dir):
    path = os.path.join(temp_dir, "empty.txt")
    with open(path, "w") as _:
        pass
    result = search_in_file(path, "anything")

    # 앱의 폴더 검색은 빈 파일을 일치 없음으로 처리하고 건너뛴 목록에 넣지 않습니다.
    assert result is None


def test_search_in_excel(temp_dir):
    excel_path = os.path.join(temp_dir, "test.xlsx")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "Search Me"
    ws["B2"] = "Python Rulez"
    wb.save(excel_path)
    wb.close()

    result = search_in_file(excel_path, "Rulez")
    assert result is not None
    # [Mypy] Union[SearchResult, SkippedResult] 타입 추론 강화
    assert isinstance(result, tuple) and len(result) == 3
    assert result[1] == 1
    matches = result[2]
    assert matches[0][1] == "Sheet"
    assert matches[0][2] == "B2"


def test_search_in_file_nfc_normalization(temp_dir):
    file_path = os.path.join(temp_dir, "nfc_test.txt")
    content = "\uc548\ub155"  # "안녕"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)

    result = search_in_file(file_path, "\uc548\ub155")
    assert result is not None


def test_search_large_file_size_limit(temp_dir, monkeypatch):
    from sf_utils.app_strings import AppStrings

    file_path = os.path.join(temp_dir, "large_guard.json")
    with open(file_path, "w", encoding="utf-8") as f:
        f.write('{"msg":"needle", "pad":"' + "x" * (2 * 1024 * 1024) + '"}')

    # 설정한 상한(1MB)보다 실제로 큰 파일은 Rust가 크기 사유로 건너뜁니다.
    from core import search_engine as engine_module
    monkeypatch.setattr(engine_module.ConfigManager, "get_advanced_settings",
                        lambda _self: {Constants.CONFIG_KEY_MAX_SEARCH_FILE_SIZE_MB: 1})
    forced_size = os.path.getsize(file_path)
    result = search_in_file(file_path, "needle", special_mode=Constants.MODE_JSON)

    assert isinstance(result, tuple)
    assert result[0] == Constants.STATUS_SKIPPED
    assert result[1] == AppStrings.SKIP_REASON_TOO_LARGE.format(
        AppStrings.SKIP_DETAIL_FILE_SIZE_BYTES.format(forced_size)
    )


def test_search_encoding_error_handling(temp_dir):
    file_path = os.path.join(temp_dir, "bad_encoding.txt")
    with open(file_path, "wb") as f:
        f.write(b"\xff\xfe\x00\x00")
    result = search_in_file(file_path, "test")
    assert result is None


def test_search_io_error_handling(temp_dir):
    non_existent = os.path.join(temp_dir, "non_existent.txt")
    result = search_in_file(non_existent, "test")
    assert isinstance(result, tuple)
    assert result[0] == "SKIPPED"


def test_search_corrupted_excel_handling(temp_dir):
    excel_path = os.path.join(temp_dir, "corrupted.xlsx")
    with open(excel_path, "w") as f:
        f.write("This is not a valid Excel file")
    result = search_in_file(excel_path, "test")
    assert isinstance(result, tuple)
    assert result[0] == "SKIPPED"
