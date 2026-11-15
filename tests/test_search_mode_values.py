"""검색 모드 고정 값(7.2.3): 저장·판별은 언어와 상관없고, 화면 문구는 표시할 때만 붙습니다."""
import pytest

from core import search_mode as sm
from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants as C
from sf_utils.english_strings import ENGLISH_STRINGS
from sf_utils.localization import get_language, set_language


@pytest.mark.parametrize("value,expected", [
    ("Normal", "Normal"), ("", "Normal"), (None, "Normal"), ("사용 안함", "Normal"), ("Off", "Normal"),
    ("XML", "XML"), ("XML exact", "XML exact"), ("XML (정확히 일치)", "XML exact"), ("XML (contains)", "XML"),
    ("JSON (부분 일치)", "JSON"), ("JSON (exact)", "JSON exact"),
    ("Excel (정확히 일치)", "Excel exact"), ("UE localization archive (contains)", "archive"),
    ("UE 현지화 archive (정확히 일치)", "archive exact"), ("exact", "exact"), ("정확히 일치", "exact"),
])
def test_normalize_accepts_values_and_labels(value, expected):
    assert sm.normalize(value) == expected


def test_kind_and_exact():
    assert sm.kind("JSON exact") == sm.KIND_JSON and sm.is_exact("JSON exact")
    assert sm.kind("Normal") is None and not sm.is_exact("Normal")
    assert sm.kind("exact") is None and sm.is_exact("exact") and sm.is_normal("exact")
    assert sm.kind("json") is None  # 고정 값과 같은 대소문자만 형식으로 봅니다


@pytest.mark.parametrize("language", ["ko", "en"])
def test_values_do_not_change_with_language_but_labels_do(language):
    previous = get_language()
    try:
        set_language(language)
        assert AppStrings.SPECIAL_SEARCH_ITEMS == list(sm.VALUES)
        assert AppStrings.SPECIAL_SEARCH_OFF == C.MODE_NORMAL == sm.NORMAL
        assert C.MODE_EXACT == sm.EXACT
        assert sm.menu_label("JSON exact") == AppStrings.SPECIAL_SEARCH_MENU_ITEMS[4]
        assert sm.menu_label("exact") == AppStrings.SPECIAL_SEARCH_MENU_ITEMS[0]
    finally:
        set_language(previous)
    assert "SPECIAL_SEARCH_ITEMS" not in ENGLISH_STRINGS and "SPECIAL_SEARCH_OFF" not in ENGLISH_STRINGS


def test_engine_bits_follow_mode_kind():
    from core.search_engine import get_rust_mode_bits

    assert get_rust_mode_bits("JSON exact") & C.RUST_MODE_JSON and get_rust_mode_bits("JSON exact") & C.RUST_MODE_EXACT
    assert get_rust_mode_bits("XML") & C.RUST_MODE_XML and not get_rust_mode_bits("XML") & C.RUST_MODE_EXACT
    assert get_rust_mode_bits("archive exact") & C.RUST_MODE_ARCHIVE
    assert get_rust_mode_bits("exact") & C.RUST_MODE_EXACT
    assert get_rust_mode_bits("Normal") & (C.RUST_MODE_JSON | C.RUST_MODE_XML | C.RUST_MODE_EXACT) == 0
