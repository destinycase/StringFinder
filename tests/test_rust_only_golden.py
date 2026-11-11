"""Python 검색기를 지운 뒤의 앱 검색 경로 골든 테스트입니다(제안서 7.3).

앱이 실제로 쓰는 경로(``search_directory_fast``·``search_files_list_fast``·``search_in_file`` → Rust)를
지나며, 예전에 글자 변형 검색(Python)으로만 찾던 경우를 포함해 형식마다 기대 결과를 고정합니다.
"""
import json
import os
import random
import unicodedata

import pytest
from openpyxl import Workbook

from core import search_engine as se
from sf_utils.constants import Constants as C

NFD_KOREAN = unicodedata.normalize("NFD", "목표단어")


@pytest.fixture
def corpus(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "plain.txt").write_text(
        "첫 줄\r\n둘째 줄 Straße\r\n셋째 줄 " + NFD_KOREAN + "\rCR 줄 ПРИВЕТ\n", encoding="utf-8", newline="")
    (root / "cp949.txt").write_bytes("앞 줄\r\n목표단어 와 strasse\r\n".encode("cp949"))
    (root / "utf16.txt").write_bytes("﻿첫 줄\r\nΣΟΦΊΑ 목표단어\r\n".encode("utf-16-le"))
    (root / "data.json").write_text(json.dumps(
        {"a": "Straße", "b": [NFD_KOREAN, "기타"], "목표단어": "키만 일치"}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    (root / "data.xml").write_text(
        f"<root>\n  <a>STRASSE</a>\n  <b attr=\"{NFD_KOREAN}\">x</b>\n</root>\n", encoding="utf-8")
    book = Workbook()
    book.active.title = "Data"
    book.active["B2"] = "Straße"
    book.active["C3"] = NFD_KOREAN
    book.save(root / "data.xlsx")
    return root


def _lines(result):
    """{파일 이름: [줄(또는 셀) 위치]}로 줄입니다."""
    out = {}
    for path, count, matches in result["results"]:
        rows = [row for row in matches if str(row[0]) not in ("-1", "-2")]
        out[os.path.basename(path)] = sorted(row[0] if not isinstance(row[0], str) else row[0] for row in rows)
        assert count == len(rows)
    return out


def test_plain_text_matches_case_and_normal_forms_across_encodings(corpus):
    result = se.search_directory_fast([str(corpus)], "strasse", ["txt"])
    assert _lines(result) == {"plain.txt": [2], "cp949.txt": [2]}
    result = se.search_directory_fast([str(corpus)], "목표단어", ["txt"])
    # plain.txt: CR만 쓴 줄바꿈도 줄로 셉니다(3번째 줄). UTF-16·CP949는 Rust가 직접 디코딩합니다.
    assert _lines(result) == {"plain.txt": [3], "cp949.txt": [2], "utf16.txt": [2]}
    result = se.search_directory_fast([str(corpus)], "привет", ["txt"])
    assert _lines(result) == {"plain.txt": [4]}
    result = se.search_directory_fast([str(corpus)], "σοφία", ["txt"])
    assert _lines(result) == {"utf16.txt": [2]}
    assert result["skipped"] == []


@pytest.mark.parametrize("mode,query,expected", [
    (C.MODE_JSON, "strasse", 1),
    (C.MODE_JSON, "목표단어", 1),  # 키 이름은 검색하지 않고 분해형 한글 값만 일치
    (C.MODE_XML, "straße", 1),
    (C.MODE_XML, "목표단어", 1),
])
def test_structured_values_use_the_same_comparison(corpus, mode, query, expected):
    # 앱은 값만 검색 때 대상 확장자를 해당 형식으로 제한해 넘깁니다.
    extension = "json" if mode == C.MODE_JSON else "xml"
    result = se.search_directory_fast([str(corpus)], query, [extension], special_mode=mode)
    assert [count for _path, count, _rows in result["results"]] == [expected]
    single = se.search_in_file(str(corpus / ("data.json" if mode == C.MODE_JSON else "data.xml")),
                               query, special_mode=mode)
    assert single and single[1] == expected


@pytest.mark.parametrize("query,cell", [("strasse", "B2"), ("목표단어", "C3")])
def test_excel_cells_use_the_same_comparison(corpus, query, cell):
    result = se.search_in_file(str(corpus / "data.xlsx"), query, special_mode=C.MODE_EXCEL)
    assert result and result[1] == 1
    assert result[2][0][2] == cell


def test_file_list_entry_matches_directory_entry(corpus):
    files = [str(path) for path in sorted(corpus.glob("*.txt"))]
    listed = se.search_files_list_fast(files, "목표단어")
    walked = se.search_directory_fast([str(corpus)], "목표단어", ["txt"])
    assert _lines(listed) == _lines(walked)


def test_oversized_single_file_reports_a_size_reason(tmp_path, monkeypatch):
    path = tmp_path / "big.txt"
    path.write_text("needle " + "x" * (2 * 1024 * 1024), encoding="utf-8")
    monkeypatch.setattr(se.ConfigManager, "get_advanced_settings",
                        lambda _self: {C.CONFIG_KEY_MAX_SEARCH_FILE_SIZE_MB: 1})
    result = se.search_in_file(str(path), "needle")
    assert result and result[0] == C.STATUS_SKIPPED
    assert str(path.stat().st_size) in result[1]


@pytest.mark.chaos
def test_random_and_malformed_bytes_never_break_the_search(tmp_path):
    """예전 Python 경로 chaos 테스트를 대신합니다: 깨진 입력도 결과·건너뜀 중 하나로 끝나야 합니다."""
    rng = random.Random(1234)
    files = []
    for index in range(8):
        path = tmp_path / f"fuzz_{index}.bin"
        path.write_bytes(bytes(rng.getrandbits(8) for _ in range(rng.randint(1024, 256 * 1024))))
        files.append(path)
    for name, data in (("bad_utf8.txt", b"\xed\xa0\x80 needle"), ("bad_cp949.txt", b"\xff\xfe\xfa needle"),
                       ("huge_line.txt", b"a" * (3 * 1024 * 1024) + b"needle")):
        (tmp_path / name).write_bytes(data)
        files.append(tmp_path / name)
    for query in ("needle", "목표단어", "ß"):
        result = se.search_files_list_fast([str(path) for path in files], query)
        assert set(result) >= {"results", "skipped"}
        for path in files:
            single = se.search_in_file(str(path), query)
            assert single is None or isinstance(single, tuple)
