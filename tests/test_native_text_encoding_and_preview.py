"""재검수 보고서에서 확인된 검색 정확성 회귀 테스트."""

from core.search_engine import search_files_list_fast
from sf_utils.constants import Constants


def test_rust_search_finds_no_bom_utf16_with_binary_exclusion(tmp_path):
    path = tmp_path / "utf16_no_bom.txt"
    path.write_bytes("needle".encode("utf-16-le"))

    result = search_files_list_fast([str(path)], "needle", exclude_binary=True)

    assert len(result["results"]) == 1
    assert result["results"][0][2][0][1] == "needle"


def test_rust_search_finds_euc_kr_without_loading_full_text_result(tmp_path):
    path = tmp_path / "euc_kr.txt"
    path.write_bytes("안녕하세요 needle".encode("euc-kr"))

    result = search_files_list_fast([str(path)], "needle", exclude_binary=True)

    assert len(result["results"]) == 1
    assert "needle" in result["results"][0][2][0][1]


def test_rust_long_line_preview_contains_match(tmp_path):
    path = tmp_path / "long_line.txt"
    content = bytearray(b"A" * 12_000)
    content[8_000:8_006] = b"needle"
    path.write_bytes(content)

    result = search_files_list_fast([str(path)], "needle", exclude_binary=True)

    assert len(result["results"]) == 1
    assert "needle" in result["results"][0][2][0][1]


def test_rust_exact_search_handles_final_carriage_return(tmp_path):
    path = tmp_path / "final_cr.txt"
    path.write_bytes(b"prefix\nneedle\r")

    result = search_files_list_fast(
        [str(path)], "needle", special_mode=Constants.MODE_EXACT, exclude_binary=True
    )

    assert len(result["results"]) == 1
    assert result["results"][0][2][0][0] == 2
