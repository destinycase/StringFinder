from core.search_engine import _normalize_rust_match
from sf_utils.constants import Constants


class MockMatch:
    def __init__(self, line, content, offset=None, length=None):
        self.line = line
        self.content = content
        self.offset = offset
        self.length = length


def test_excel_normalization_case_insensitivity():
    # Rust engine reports: Sheet\tCell\tValue
    match_obj = MockMatch(1, "Sheet1\tA1\tHello")

    # Test with correct casing
    res, marker = _normalize_rust_match(match_obj, Constants.MODE_EXCEL)
    assert res is not None
    assert res == (1, "Sheet1", "A1", "Hello", None, None)

    # Test with different casing (e.g., from UI combos)
    res, marker = _normalize_rust_match(match_obj, "excel (partial match)")
    assert res is not None
    assert res == (1, "Sheet1", "A1", "Hello", None, None)

    # Test with Uppercase
    res, marker = _normalize_rust_match(match_obj, "EXCEL")
    assert res is not None
    assert res == (1, "Sheet1", "A1", "Hello", None, None)


def test_archive_normalization_case_insensitivity():
    # Rust engine reports: NS\tKey\tSrc\tTrans
    match_obj = MockMatch(1, "UI\tBTN_OK\tSubmit\t확인")

    # Test with correct casing
    res, marker = _normalize_rust_match(match_obj, Constants.MODE_ARCHIVE)
    assert res is not None
    assert res == (1, "UI", "BTN_OK", "Submit", "확인", None, None)

    # Test with lowercase
    res, marker = _normalize_rust_match(match_obj, "archive")
    assert res is not None
    assert res == (1, "UI", "BTN_OK", "Submit", "확인", None, None)


def test_json_normalization_case_insensitivity():
    # Rust engine reports: Path\tValue
    match_obj = MockMatch(1, "settings/theme\tDark")

    res, marker = _normalize_rust_match(match_obj, "json")
    assert res is not None
    assert res == (1, "settings/theme", "Dark", None, None)


def test_no_mode_fails_splitting():
    # If no mode is provided, it should return raw content
    match_obj = MockMatch(1, "Sheet1\tA1\tHello")
    res, marker = _normalize_rust_match(match_obj, None)
    assert res == (1, "Sheet1\tA1\tHello", None, None)
