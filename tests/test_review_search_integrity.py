"""Regression coverage for structured-document integrity and partial failures."""
import sys
import zipfile
from datetime import date

import pytest
from openpyxl import Workbook
from openpyxl.utils.datetime import CALENDAR_MAC_1904

from core import search_engine as se
from core.json_policy import JsonInteger, loads_document
from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants as C
from sf_utils.localization import get_language, set_language


@pytest.fixture(autouse=True)
def search_policy():
    with se.use_search_settings_snapshot({C.CONFIG_KEY_MAX_PER_FILE_MATCHES: 10,
                                         C.CONFIG_KEY_SEARCH_ENCODING: "auto"}):
        yield


def native(path, query, mode, existence=False, entry="directory"):
    if not se.HAS_RUST_ENGINE:
        pytest.skip("native engine unavailable")
    if entry == "directory":
        return se.search_directory_fast([str(path)], query, [], special_mode=mode, existence_only=existence)
    return se.search_files_list_fast([str(path)], query, special_mode=mode, existence_only=existence)


def precise(path, query, mode, existence=False):
    return se.search_in_file(str(path), query, special_mode=mode,
                             use_complex_search=True, existence_only=existence)


@pytest.mark.parametrize("digits", [4301, 5000, 20000])
@pytest.mark.parametrize("negative", [False, True])
def test_json_large_integer_preserves_source_and_global_limit(tmp_path, digits, negative):
    text = ("-" if negative else "") + "9" * digits
    limit = sys.get_int_max_str_digits()
    value = loads_document('{"v":' + text + '}')["v"]
    assert isinstance(value, JsonInteger)
    assert str(value) == text
    assert sys.get_int_max_str_digits() == limit
    p = tmp_path / "large.json"
    p.write_text('{"v":' + text + ',"other":"needle"}', encoding="utf-8")
    for query in ("999999", "needle"):
        result = native(p, query, C.MODE_JSON)
        assert result["results"][0][1] == 1 and not result["skipped"]
        assert precise(p, query, C.MODE_JSON)[1] == 1
    assert precise(p, "999999", C.MODE_JSON, True)[1] == 1


def test_iterative_json_large_integer_and_invalid_number():
    text = "9" * 5000
    parsed = loads_document("[" * 1100 + text + "]" * 1100)
    for _ in range(1100):
        parsed = parsed[0]
    assert str(parsed) == text
    for invalid in ("01", "--1", "1e", "NaN", "Infinity"):
        with pytest.raises(ValueError):
            loads_document('{"v":' + invalid + '}')


@pytest.mark.parametrize("mode", [C.MODE_JSON, C.MODE_XML])
@pytest.mark.parametrize("encoding", ["utf-16-le", "utf-16-be", "utf-8"])
@pytest.mark.parametrize("existence", [False, True])
@pytest.mark.parametrize("entry", ["directory", "files", "single"])
def test_damaged_structured_encoding_skips_whole_document(tmp_path, mode, encoding, existence, entry):
    extension, content = ("json", '{"v":"needle\ud800"}') if mode == C.MODE_JSON else ("xml", '<root>needle\ud800</root>')
    bom = {"utf-16-le": b"\xff\xfe", "utf-16-be": b"\xfe\xff", "utf-8": b"\xef\xbb\xbf"}[encoding]
    p = tmp_path / ("damaged." + extension)
    p.write_bytes(bom + content.encode(encoding, errors="surrogatepass"))
    if entry == "single":
        assert se.search_in_file(str(p), "needle", special_mode=mode, existence_only=existence) == (C.STATUS_SKIPPED, AppStrings.ERROR_DOCUMENT_ENCODING)
    else:
        result = native(p, "needle", mode, existence, entry)
        assert result["results"] == []
        assert result["skipped"][0][1] == AppStrings.ERROR_DOCUMENT_ENCODING
    assert precise(p, "needle", mode, existence) == (C.STATUS_SKIPPED, AppStrings.ERROR_DOCUMENT_ENCODING)


@pytest.mark.parametrize("space", ["\n", "\r\n", "\r", "\t"])
@pytest.mark.parametrize("existence", [False, True])
def test_xml_literal_attribute_whitespace(tmp_path, space, existence):
    p = tmp_path / "attribute.xml"
    p.write_bytes(('<root v="needle' + space + 'value"/>').encode())
    assert native(p, "needle value", C.MODE_XML, existence)["results"][0][1] == 1
    assert precise(p, "needle value", C.MODE_XML, existence)[1] == 1
    p.write_text('<root v="needle&#xA;value"/>', encoding="utf-8")
    assert not native(p, "needle value", C.MODE_XML, existence)["results"]
    assert precise(p, "needle value", C.MODE_XML, existence) is None


def broken_workbook(path, broken_count=1, broken_first=False):
    wb = Workbook()
    wb.epoch = CALENDAR_MAC_1904
    good = wb.active
    good.title = "Good"
    good["A1"] = "needle"
    good["C4"] = date(1904, 1, 1)
    for i in range(broken_count):
        wb.create_sheet(f"Broken{i}")["A1"] = "other"
    if broken_first:
        wb.move_sheet(good, offset=broken_count)
    wb.save(path)
    with zipfile.ZipFile(path) as archive:
        members = [(entry, archive.read(entry.filename)) for entry in archive.infolist()]
    good_index = broken_count + 1 if broken_first else 1
    with zipfile.ZipFile(path, "w") as archive:
        for entry, content in members:
            if entry.filename.startswith("xl/worksheets/sheet") and entry.filename != f"xl/worksheets/sheet{good_index}.xml":
                content = b"<worksheet><broken"
            archive.writestr(entry, content)
    wb.close()


@pytest.mark.parametrize("entry", ["directory", "files"])
@pytest.mark.parametrize("query", ["needle", "1904-01-01", "not found"])
@pytest.mark.parametrize("existence", [False, True])
def test_excel_partial_sheet_keeps_results_and_notice(tmp_path, entry, query, existence):
    p = tmp_path / "partial.xlsx"
    broken_workbook(p, broken_first=True)
    result = native(p, query, C.MODE_EXCEL, existence, entry=entry)
    assert len(result["skipped"]) == 1
    assert "Broken0" in result["skipped"][0][1]
    answer = precise(p, query, C.MODE_EXCEL, existence)
    if query == "not found":
        assert not result["results"]
        assert answer[0] == C.STATUS_SKIPPED
    else:
        assert result["results"][0][1] == 1
        assert answer[1] == 1
        assert "Broken0" in se._extract_normalized_partial_skip_reason(answer[2])
        assert "Good" not in result["skipped"][0][1]


@pytest.mark.parametrize("existence", [False, True])
def test_excel_errors_do_not_consume_cap_or_disappear_on_hit(tmp_path, existence):
    p = tmp_path / "many_errors.xlsx"
    broken_workbook(p, broken_count=11, broken_first=True)
    result = native(p, "needle", C.MODE_EXCEL, existence)
    assert result["results"][0][1] == 1
    assert len(result["skipped"]) == 1
    answer = precise(p, "needle", C.MODE_EXCEL, existence)
    assert answer[1] == 1
    assert se._extract_normalized_partial_skip_reason(answer[2])
    direct = se.search_in_excel_special(str(p), "needle", existence_only=existence)
    assert direct[1] == 1
    assert se._extract_normalized_partial_skip_reason(direct[2])


@pytest.mark.parametrize("entry", ["directory", "files", "single"])
def test_json_tab_key_keeps_value(tmp_path, entry):
    p = tmp_path / "tab.json"
    p.write_text('{"a\\tb":"needle\\tvalue"}', encoding="utf-8")
    answer = se.search_in_json_special(str(p), "needle") if entry == "single" else native(p, "needle", C.MODE_JSON, entry=entry)["results"][0]
    assert answer[2][0][1:3] == ("a\tb", "needle\tvalue")
    assert precise(p, "needle", C.MODE_JSON)[2][0][1:3] == ("a\tb", "needle\tvalue")


def test_deep_directory_scan_is_stack_safe(tmp_path):
    # Lower the limit only for the scan, avoiding platform maximum path lengths.
    directories = []
    current = tmp_path
    for _ in range(220):
        current = current / "d"
        current.mkdir()
        directories.append(current)
    path = current / "a.txt"
    path.write_text("needle", encoding="utf-8")
    previous = sys.getrecursionlimit()
    try:
        sys.setrecursionlimit(200)
        scanner = se.FileScanner([str(tmp_path)], [])
        assert scanner.scan() == [(str(path), 6)]
        assert not scanner.skipped
    finally:
        sys.setrecursionlimit(previous)
        path.unlink()
        for directory in reversed(directories):
            directory.rmdir()


@pytest.mark.parametrize("mode", [C.MODE_JSON, C.MODE_XML])
def test_keyword_prefilter_rejects_invalid_structured_bytes(tmp_path, mode):
    p = tmp_path / ("bad.json" if mode == C.MODE_JSON else "bad.xml")
    content = '{"v":"needle\ud800"}' if mode == C.MODE_JSON else '<root>needle\ud800</root>'
    p.write_bytes(b"\xff\xfe" + content.encode("utf-16-le", errors="surrogatepass"))
    found, skipped = se.find_files_with_keyword_fast([str(p)], "needle",
        special_mode=mode, return_skipped=True)
    assert not found
    assert skipped[0][1] == AppStrings.ERROR_DOCUMENT_ENCODING


@pytest.mark.parametrize("mode", [None, C.MODE_JSON, C.MODE_XML])
def test_ordinary_text_keeps_replacement_policy(tmp_path, mode):
    p = tmp_path / "ordinary.txt"
    p.write_bytes(b"\xff\xfe" + "needle\ud800".encode("utf-16-le", errors="surrogatepass"))
    result = native(p, "needle", mode)
    assert result["results"][0][1] == 1 and not result["skipped"]
    assert precise(p, "needle", mode)[1] == 1


def test_json_tab_numeric_key_obeys_match_cap(tmp_path):
    p = tmp_path / "numeric.json"
    p.write_text("[" + ",".join('{"a\\tb":1.5}' for _ in range(15)) + "]", encoding="utf-8")
    result = native(p, "1.5", C.MODE_JSON)
    assert result["results"][0][1] == 10
    assert len(result["skipped"]) == 1


def test_new_notices_localize_after_session_language_change():
    previous = get_language()
    try:
        set_language("ko")
        notices = (AppStrings.ERROR_DOCUMENT_ENCODING,
                   AppStrings.SKIP_REASON_EXCEL_PARTIAL.format("Broken0"))
        assert all(se.is_supported_skip_reason(reason) for reason in notices)
        set_language("en")
        translated = [se.localize_skip_reason_for_display(reason) for reason in notices]
        assert translated == [AppStrings.ERROR_DOCUMENT_ENCODING,
                              AppStrings.SKIP_REASON_EXCEL_PARTIAL.format("Broken0")]
    finally:
        set_language(previous)


@pytest.mark.parametrize("existence", [False, True])
def test_worker_retains_excel_results_and_emits_partial_notice(tmp_path, existence, qtbot):
    from core.worker import SearchWorker
    p = tmp_path / "worker.xlsx"
    broken_workbook(p, broken_first=True)
    worker = SearchWorker({"search_paths": [str(tmp_path)], "search_string": "needle",
                           "extensions": ["xlsx"], "special_mode": C.MODE_EXCEL,
                           "existence_only": existence})
    received, skipped = [], []
    worker.signals.results_found.connect(lambda rows: received.extend(rows))
    worker.signals.skipped_found.connect(lambda rows: skipped.extend(rows))
    worker.run()
    qtbot.waitUntil(lambda: bool(received) and bool(skipped))
    assert received[0][1] == 1
    assert len(skipped) == 1 and "Broken0" in skipped[0][1]
