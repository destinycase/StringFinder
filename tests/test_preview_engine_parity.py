"""미리보기가 검색 엔진과 같은 판정을 쓰는지 확인합니다(검수 R-3·R-4)."""
import unicodedata

from core import search_engine as se
from ui.result_view import _detect_text_encoding, _read_context_lines_from_file


def test_preview_encoding_follows_the_engine_for_late_cp949(tmp_path):
    # 앞 70KB는 ASCII이고 뒤가 CP949인 파일: 예전 미리보기(앞 64KiB만 봄)는 UTF-8로 읽어 글자가 깨졌습니다.
    path = tmp_path / "late_cp949.txt"
    path.write_bytes(b"a" * 70_000 + b"\r\n" + "검색어 여기\r\n".encode("cp949"))
    assert _detect_text_encoding(str(path)) == "cp949"
    result = se.search_in_file(str(path), "검색어")
    assert result and result[2][0][0] == 2
    lines = _read_context_lines_from_file(str(path), 2, 0, 0)
    assert lines == [(2, "검색어 여기")]


def test_preview_encoding_names_for_boms_and_utf16(tmp_path):
    cases = {
        "u8.txt": ("본문".encode("utf-8"), "utf-8"),
        "u8bom.txt": (b"\xef\xbb\xbf" + "본문".encode("utf-8"), "utf-8-sig"),
        "u16bom.txt": ("﻿본문 target".encode("utf-16-le"), "utf-16"),
        "cp.txt": ("본문 검색".encode("cp949"), "cp949"),
    }
    for name, (data, expected) in cases.items():
        (tmp_path / name).write_bytes(data)
        assert _detect_text_encoding(str(tmp_path / name)) == expected, name


def test_long_line_centering_uses_the_engine_comparison():
    # 분해형 한글로 된 긴 줄에서 완성형 검색어의 위치를 찾습니다(예전 casefold로는 못 찾아 줄 앞부분을 보여 줌).
    filler = unicodedata.normalize("NFD", "가나다라") * 2_000
    line = filler + unicodedata.normalize("NFD", " 목표단어 ") + filler
    position = se.find_key_match_char(line, "목표단어")
    assert position is not None
    assert line[position:position + 12].startswith(unicodedata.normalize("NFD", "목표"))
    # 시작 위치를 주면 그 뒤의 일치를 찾습니다.
    assert se.find_key_match_char(line, "목표단어", position + 1) is None
    assert se.find_key_match_char("Straße and STRASSE", "strasse", 1) == 11
