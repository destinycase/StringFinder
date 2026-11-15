"""Excel 값 검색의 셀 결과, 손상 Excel 건너뜀, 휴지통 안 Excel 제외."""


from folder_search_helper import rendered_skips
import pytest

from folder_search_helper import search_in_excel_special
from sf_utils.constants import Constants


def test_excel_special_search_returns_cell_details(tmp_path, monkeypatch):
    from openpyxl import Workbook

    target = tmp_path / "limited_python.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["first", "second", "needle"])
    workbook.save(target)
    workbook.close()

    result = search_in_excel_special(
        str(target),
        "needle",
        )

    assert isinstance(result, tuple) and len(result) == 3
    assert result[1] == 1


def test_excel_special_exact_search_keeps_single_cell_sheet(tmp_path):
    from openpyxl import Workbook

    target = tmp_path / "single_cell.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet["A1"] = "needle"
    workbook.save(target)
    workbook.close()

    result = search_in_excel_special(
        str(target),
        "needle",
    )

    assert isinstance(result, tuple) and len(result) == 3
    assert result[1] == 1
    assert result[2][0][2] == "A1"


def test_real_rust_excel_returns_all_cell_details(tmp_path, monkeypatch):
    from core import search_engine
    from openpyxl import Workbook

    if not search_engine.HAS_RUST_ENGINE:
        pytest.skip("compiled Rust engine is unavailable")

    limited_target = tmp_path / "rust_limited.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["first", "second", "needle"])
    workbook.save(limited_target)
    workbook.close()

    matched_target = tmp_path / "rust_early_match.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["needle", "second", "third"])
    workbook.save(matched_target)
    workbook.close()

    monkeypatch.setattr(
        search_engine,
        "_get_rust_search_limits",
        lambda: (
            Constants.DEFAULT_MAX_PER_FILE_MATCHES,
            Constants.DEFAULT_MAX_JSON_DEPTH,
            Constants.DEFAULT_MAX_SEARCH_FILE_SIZE_MB * 1024 * 1024,
        ),
    )

    after_legacy_limit = search_in_excel_special(
        str(limited_target),
        "needle",
        )
    matched = search_in_excel_special(
        str(matched_target),
        "needle",
        )

    assert isinstance(after_legacy_limit, tuple) and len(after_legacy_limit) == 3
    assert after_legacy_limit[1] == 1
    assert isinstance(matched, tuple) and len(matched) == 3
    assert matched[1] == 1


@pytest.mark.parametrize(
    ("suffix", "payload"),
    [
        (".xlsx", b"PK\x03\x04broken workbook"),
        (".xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1broken workbook"),
    ],
)
def test_real_rust_bulk_corrupted_excel_is_reported_as_skipped(
    tmp_path, suffix, payload):
    from core import search_engine

    if not search_engine.HAS_RUST_ENGINE:
        pytest.skip("compiled Rust engine is unavailable")

    target = tmp_path / f"corrupted{suffix}"
    target.write_bytes(payload)

    result = search_engine.search_directory_fast(
        [str(target)],
        "needle",
        special_mode=Constants.MODE_EXCEL,
        )

    assert result["results"] == []
    assert len(rendered_skips(result["skipped"])) == 1
    assert rendered_skips(result["skipped"])[0][0] == str(target)
    assert "Excel" in rendered_skips(result["skipped"])[0][1]


def test_real_rust_recycle_excel_is_never_reported(tmp_path):
    from core import search_engine

    if not search_engine.HAS_RUST_ENGINE:
        pytest.skip("compiled Rust engine is unavailable")

    recycle_dir = tmp_path / "$RECYCLE.BIN" / "canary"
    recycle_dir.mkdir(parents=True)
    target = recycle_dir / "canary.xlsx"
    target.write_bytes(b"PK\x03\x04broken workbook")

    for exclude_hidden in (False, True):
        result = search_engine.search_directory_fast(
            [str(target)],
            "needle",
            special_mode=Constants.MODE_EXCEL,
            exclude_hidden=exclude_hidden,
        )
        assert result == {"results": [], "skipped": []}
