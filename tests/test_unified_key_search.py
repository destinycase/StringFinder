"""
단일 비교 기준(key = NFC(casefold(NFC(x))))과 줄 정의(LF·CRLF·CR) 회귀 테스트.

Python 대체 경로가 Rust의 누락을 가리지 않도록 Rust 엔진의 search_dir·search_files_list를
직접 호출합니다(검수 I-3). 검수 결함 I-1(비ASCII 대소문자 누락)과 I-2(XML 정확히 일치의
NFC 누락)의 재현 사례를 형식 × 부분/정확 일치로 확인합니다.
"""

import json
import unicodedata

import pytest

from sf_utils.constants import Constants

sf_engine = pytest.importorskip("rust_engine.sf_engine")

EXACT = Constants.RUST_MODE_EXACT
MODES = {
    "text": (".txt", 0),
    "json": (".json", Constants.RUST_MODE_JSON),
    "xml": (".xml", Constants.RUST_MODE_XML),
    "archive": (".archive", Constants.RUST_MODE_ARCHIVE),
    "excel": (".xlsx", Constants.RUST_MODE_EXCEL),
}


def reference_key(text: str) -> str:
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", text).casefold())


def write_value(path, fmt: str, value: str) -> None:
    if fmt == "text":
        path.write_text(f"before\n{value}\nafter\n", encoding="utf-8")
    elif fmt == "json":
        path.write_text(json.dumps({"items": ["before", value]}, ensure_ascii=False), encoding="utf-8")
    elif fmt == "xml":
        escaped = value.replace("&", "&amp;").replace("<", "&lt;")
        path.write_text(f"<root><item>before</item><item>{escaped}</item></root>", encoding="utf-8")
    elif fmt == "archive":
        document = {
            "Namespace": "",
            "Children": [{"Key": "K1", "Source": {"Text": value}, "Translation": {"Text": ""}}],
        }
        path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    elif fmt == "excel":
        openpyxl = pytest.importorskip("openpyxl")
        workbook = openpyxl.Workbook()
        workbook.active["A1"] = "before"
        workbook.active["A2"] = value
        workbook.save(path)


def found(tmp_path, fmt: str, value: str, query: str, exact: bool) -> bool:
    extension, mode = MODES[fmt]
    target = tmp_path / f"{fmt}-{'exact' if exact else 'part'}{extension}"
    write_value(target, fmt, value)
    results, skipped = sf_engine.search_dir([str(tmp_path)], query, mode_bits=mode | (EXACT if exact else 0))
    assert not skipped, skipped
    target.unlink()
    return any(path.endswith(target.name) for path, _matches in results)


# (본문 값, 검색어, 부분 일치 기대, 정확히 일치 기대)
CASES = [
    ("Привет мир", "привет", True, False),
    ("ПРИВЕТ", "привет", True, True),
    ("ΣΟΦΊΑ", "σοφία", True, True),
    ("ÉCOLE", "école", True, True),
    ("K", "k", True, True),  # Kelvin 기호
    ("Straße", "STRASSE", True, True),
    ("각", "각", True, True),  # 분해형(NFD) 한글 ↔ 완성형
    ("é", "é", True, True),
    ("ǰ", "j", False, False),  # 접기 뒤 재정규화: ǰ는 j가 아님
    ("J̌", "ǰ", True, True),
    ("plain", "plain", True, True),
]


@pytest.mark.parametrize("fmt", list(MODES))
@pytest.mark.parametrize("value,query,partial,exact", CASES)
def test_every_format_uses_the_same_key_comparison(tmp_path, fmt, value, query, partial, exact):
    assert found(tmp_path, fmt, value, query, exact=False) is partial
    assert found(tmp_path, fmt, value, query, exact=True) is exact


def test_xml_exact_match_normalizes_decomposed_values(tmp_path):
    # 검수 I-2: XML 정확히 일치만 값에 NFC를 적용하지 않던 결함.
    target = tmp_path / "nfd.xml"
    target.write_text("<root><v>각</v><v a=\"각\"/></root>", encoding="utf-8")
    results, _ = sf_engine.search_dir([str(tmp_path)], "각", mode_bits=Constants.RUST_MODE_XML | EXACT)
    assert len(results) == 1 and len(results[0][1]) == 2


def lines_of(results):
    return [match.line for _path, matches in results for match in matches]


@pytest.mark.parametrize(
    "content,expected",
    [
        (b"a\nneedle\n", [2]),
        (b"a\r\nneedle\r\n", [2]),
        (b"a\rneedle\r", [2]),
        (b"a\r\r\nneedle", [3]),
        (b"a\n\rneedle", [3]),
        (b"needle\rx\r\nneedle\ny\rneedle", [1, 3, 5]),
    ],
)
def test_plain_text_line_numbers_follow_lf_crlf_and_cr(tmp_path, content, expected):
    (tmp_path / "lines.txt").write_bytes(content)
    results, _ = sf_engine.search_dir([str(tmp_path)], "needle")
    assert lines_of(results) == expected


@pytest.mark.parametrize("encoding", ["utf-16-le", "utf-16-be", "cp949"])
def test_decoded_text_line_numbers_follow_lf_crlf_and_cr(tmp_path, encoding):
    text = "가\r나\r\n다\n목표\r라"
    bom = {"utf-16-le": b"\xff\xfe", "utf-16-be": b"\xfe\xff"}.get(encoding, b"")
    (tmp_path / "decoded.txt").write_bytes(bom + text.encode(encoding))
    results, _ = sf_engine.search_dir([str(tmp_path)], "목표")
    assert lines_of(results) == [4]


def test_decoded_crlf_split_across_chunks_counts_once(tmp_path):
    # 64KiB 디코딩 청크 경계에 CR | LF가 걸리도록 만듭니다.
    filler = "가" * 32767  # UTF-16 65534바이트
    text = filler + "\r\n" + "목표\n"
    (tmp_path / "chunk.txt").write_bytes(b"\xff\xfe" + text.encode("utf-16-le"))
    results, _ = sf_engine.search_dir([str(tmp_path)], "목표")
    assert lines_of(results) == [2]


def test_structured_line_numbers_follow_lf_crlf_and_cr(tmp_path):
    (tmp_path / "doc.json").write_bytes(b'{\r"a": "x",\r\n"b": "needle"\n}')
    (tmp_path / "doc.xml").write_bytes(b"<r>\r<a>x</a>\r\n<b>needle</b>\n</r>")
    results, _ = sf_engine.search_dir(
        [str(tmp_path)], "needle", mode_bits=Constants.RUST_MODE_JSON | Constants.RUST_MODE_XML
    )
    by_name = {path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]: [m.line for m in matches] for path, matches in results}
    assert by_name == {"doc.json": [3], "doc.xml": [3]}


def test_reported_positions_always_point_at_the_original_match(tmp_path):
    content = "needle NEEDLE Needle\nÉcole ÉCOLE école\nstraße STRASSE\n"
    target = tmp_path / "positions.txt"
    target.write_text(content, encoding="utf-8")
    raw = target.read_bytes()
    for query in ["needle", "école", "strasse"]:
        results, _ = sf_engine.search_dir([str(tmp_path)], query)
        for _path, matches in results:
            for match in matches:
                if match.offset is None:
                    continue
                piece = raw[match.offset : match.offset + match.length].decode("utf-8")
                assert reference_key(piece) == reference_key(query), (query, piece)


def test_files_list_entry_point_uses_the_same_comparison(tmp_path):
    target = tmp_path / "list.txt"
    target.write_text("ΣΟΦΊΑ\n", encoding="utf-8")
    results, _ = sf_engine.search_files_list([str(target)], "σοφία")
    assert lines_of(results) == [1]


def test_query_that_normalizes_to_nothing_is_an_input_error():
    with pytest.raises(ValueError):
        sf_engine.search_files_list([], "")
