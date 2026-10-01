"""Candidate omissions and native/precise result consistency regressions."""
from concurrent.futures import Future
from datetime import date, datetime, time, timedelta
import json
import random
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from openpyxl import Workbook

from core import search_engine as se
from core.worker import SearchWorker
from sf_utils.constants import Constants as C


@pytest.fixture(autouse=True)
def settings_snapshot():
    with se.use_search_settings_snapshot({C.CONFIG_KEY_MAX_PER_FILE_MATCHES: 10000}):
        yield


def native(path, query, mode=None, **kwargs):
    if not se.HAS_RUST_ENGINE:
        pytest.skip("native engine required")
    return se.search_directory_fast([str(path)], query, [], special_mode=mode,
                                    exclude_hidden=False, **kwargs)


@pytest.mark.parametrize("literal", ["report{a,b}", "report[a]", "report*", "report?", "Ärger"])
def test_literal_filename_candidates_agree(tmp_path, literal):
    # '*' and '?' are invalid Windows filenames, but still valid saved filters.
    filename = literal if not any(c in literal for c in "*?") else "reporta"
    path = tmp_path / (filename.lower() + ".txt")
    path.write_text("needle", encoding="utf-8")
    expected = [] if any(c in literal for c in "*?") else [str(path)]
    scanner = se.FileScanner([str(tmp_path)], [], filename_filter=[literal])
    assert [p for p, _ in scanner.scan()] == expected
    output = native(tmp_path, "needle", filename_filter=[literal])
    assert [r[0] for r in output["results"]] == expected
    assert not output["skipped"]


@pytest.mark.parametrize("precise", [False, True])
@pytest.mark.parametrize("existence", [False, True])
@pytest.mark.parametrize("prefix_size", [65536, 2 * 1024 * 1024])
def test_korean_after_ascii_prefix(tmp_path, precise, existence, prefix_size):
    path = tmp_path / "korean.txt"
    path.write_bytes(b"a" * prefix_size + "\nneedle 가나다\n".encode("cp949"))
    result = se.search_in_file(str(path), "가나다", use_complex_search=precise,
                               existence_only=existence)
    assert result and result[0] == str(path) and result[1] == 1


@pytest.mark.parametrize("query,tail,expected", [
    ("needle", "needle needle needle 한글", 3),
    ("A", "갂", 0),  # CP949 0x81 0x41 is one Korean character, not ASCII A.
    ("A", "갂\nA 한글\nA", 2),
])
def test_ascii_scan_rechecks_late_cp949_without_false_hits(tmp_path, query, tail, expected):
    path = tmp_path / "late.txt"
    path.write_bytes(b"x" * 65536 + b"\n" + tail.encode("cp949"))
    output = native(path, query)
    assert not output["skipped"]
    assert sum(row[1] for row in output["results"]) == expected
    for result in output["results"]:
        assert all("�" not in match[1] for match in result[2])
    precise = se.search_in_file(str(path), query, use_complex_search=True)
    assert (precise[1] if precise else 0) == expected


@pytest.mark.parametrize("mode,suffix,content", [
    (C.MODE_JSON, "json", '{"padding":"' + 'a' * 65536 + '","v":"가나다"}'),
    (C.MODE_XML, "xml", '<root><padding>' + 'a' * 65536 + '</padding><v>가나다</v></root>'),
], ids=["json", "xml"])
@pytest.mark.parametrize("precise", [False, True])
def test_structured_korean_after_ascii_prefix(tmp_path, mode, suffix, content, precise):
    path = tmp_path / ("korean." + suffix)
    path.write_bytes(content.encode("cp949"))
    result = se.search_in_file(str(path), "가나다", special_mode=mode,
                               use_complex_search=precise)
    assert result and result[0] == str(path) and result[1] == 1


@pytest.mark.parametrize("encoding", ["utf-8", "cp949"])
@pytest.mark.parametrize("existence", [False, True])
def test_json_mmap_uses_full_decode_fallback(tmp_path, encoding, existence):
    path = tmp_path / "mapped.json"
    content = '{"padding":"' + 'a' * (2 * 1024 * 1024) + '","v":"가나다"}'
    path.write_bytes(content.encode(encoding))
    with se.use_search_settings_snapshot({C.CONFIG_KEY_JSON_MMAP_THRESHOLD: 1}):
        result = se.search_in_file(str(path), "가나다", special_mode=C.MODE_JSON,
                                   use_complex_search=True, existence_only=existence)
    assert result and result[0] == str(path) and result[1] == 1


@pytest.mark.parametrize("encoding", ["utf-8", "cp949", "utf-16", "utf-16-be"])
@pytest.mark.parametrize("newline", ["", "\n", "\r\n"])
def test_repeated_occurrences_have_same_count(tmp_path, encoding, newline):
    path = tmp_path / "repeated.txt"
    path.write_bytes(("needle needle needle 한글" + newline).encode(encoding))
    for precise in [False, True]:
        result = se.search_in_file(str(path), "needle", use_complex_search=precise)
        assert result and result[0] == str(path) and result[1] == 3
        existence = se.search_in_file(str(path), "needle", use_complex_search=precise,
                                     existence_only=True)
        assert existence and existence[1] == 1


@pytest.mark.parametrize("number", ["1.0", "-0.0", "1e20", "1e-5", "1e-4", "1e15", "1e16", "1.2345678901234567", "0.84551240822557006", "1.234567890123456789"])
def test_json_float_value_text_agrees(tmp_path, number):
    path = tmp_path / "number.json"
    path.write_text('{"v":' + number + '}', encoding="utf-8")
    query = str(float(number))
    output = native(path, query, C.MODE_JSON)
    assert len(output["results"]) == 1 and not output["skipped"]
    precise = se.search_in_file(str(path), query, special_mode=C.MODE_JSON,
                                use_complex_search=True)
    assert precise and precise[1] == 1


def test_json_float_roundtrip_preserves_searchable_digits(tmp_path):
    if not se.HAS_RUST_ENGINE:
        pytest.skip("native engine required")
    rng = random.Random(5925)
    path = tmp_path / "roundtrip.json"
    for _ in range(100):
        value = rng.uniform(-1e30, 1e30)
        path.write_text(json.dumps({"v": value}), encoding="utf-8")
        result = se.sf_engine.search_file(str(path), str(value), C.RUST_MODE_JSON | C.RUST_MODE_EXACT)
        assert len(result) == 1, value


@pytest.mark.parametrize("value,query", [
    (1.0, "1"), (1.00000000005, "1.00000000005"), (1e-5, "1e-05"),
    (True, "true"), (date(2026, 10, 1), "2026-10-01"),
    (datetime(2026, 10, 1, 12, 34, 56), "2026-10-01 12:34:56"),
    (time(12, 34, 56), "12:34:56"), (timedelta(days=2, seconds=5), "2 days, 0:00:05"),
    (datetime(2026, 10, 1, 12, 34, 56, 123000), "2026-10-01 12:34:56.123000"),
])
@pytest.mark.parametrize("iso_dates", [False, True])
def test_excel_typed_values_agree(tmp_path, value, query, iso_dates):
    path = tmp_path / "typed.xlsx"
    wb = Workbook(iso_dates=iso_dates)
    wb.active["A1"] = value
    wb.save(path)
    output = native(path, query, C.MODE_EXCEL)
    assert len(output["results"]) == 1 and not output["skipped"]
    precise = se.search_in_file(str(path), query, special_mode=C.MODE_EXCEL,
                                use_complex_search=True)
    assert precise and precise[0] == str(path) and precise[1] == 1
    assert output["results"][0][2][0][3] == precise[2][0][3]


@pytest.mark.parametrize("limit", [1, 2])
def test_completed_batch_notices_survive_total_limit(limit):
    worker = SearchWorker({"search_string": "needle", "use_complex_search": True})
    worker.search_settings_snapshot[C.CONFIG_KEY_MAX_TOTAL_MATCHES] = limit
    worker._last_progress_time = 0
    worker.all_results = []
    skipped = []
    worker.signals.skipped_found.connect(skipped.extend)
    future = Future()
    future.set_result({"results": [("good.json", 1, [(1, "v", "needle")])],
                       "skipped": [("bad.json", "parse failure")]})
    executor = SimpleNamespace(_max_workers=1, submit=lambda *a, **kw: future)
    with patch("core.worker.get_global_manager", return_value=None), \
            patch("core.worker.GlobalExecutor.get_executor", return_value=executor), \
            patch("core.worker.GlobalExecutor.release"), \
            patch.object(worker, "_check_safety_limits", return_value=True):
        assert worker._run_batch_search([("good.json", 10), ("bad.json", 10)]) == (1, 1, 1)
    assert [tuple(item) for item in skipped] == [("bad.json", "parse failure")]
    assert [tuple(item) for item in worker.all_skipped] == [tuple(item) for item in skipped]
