"""일반·JSON·XML·Excel 검색 결과의 일치 위치가 정확한지 확인합니다."""

import json
from typing import cast

import openpyxl

from core.search_engine import SearchResult
from folder_search_helper import search_in_file
from sf_utils.constants import Constants


def _require_search_result(result: object) -> SearchResult:
    assert result is not None
    assert isinstance(result, tuple) and len(result) == 3
    return cast(SearchResult, result)


def test_normal_text_precision(tmp_path):
    file_path = tmp_path / "test.txt"
    file_path.write_text("user_id\nid\nidentity", encoding="utf-8")

    res_partial = _require_search_result(search_in_file(str(file_path), "id"))
    assert res_partial[1] == 3

    res_exact = _require_search_result(search_in_file(str(file_path), "id", special_mode=Constants.MODE_EXACT))
    assert res_exact[1] == 1
    assert res_exact[2][0][1].strip() == "id"


def test_json_special_precision(tmp_path):
    data = {"user": "admin", "user_id": "admin_123", "description": "This is admin user"}
    file_path = tmp_path / "test.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f)

    res_partial = _require_search_result(search_in_file(str(file_path), "admin", special_mode=Constants.MODE_JSON))
    assert res_partial[1] == 3

    res_exact = _require_search_result(
        search_in_file(str(file_path), "admin", special_mode=f"{Constants.MODE_JSON} {Constants.MODE_EXACT}")
    )
    assert res_exact[1] == 1
    assert res_exact[2][0][2] == "admin"


def test_xml_special_precision(tmp_path):
    xml_content = """<root>
        <item name="target">target_value</item>
        <item name="target_extra">extra</item>
        <note>target</note>
    </root>"""
    file_path = tmp_path / "test.xml"
    file_path.write_text(xml_content, encoding="utf-8")

    res_partial = _require_search_result(search_in_file(str(file_path), "target", special_mode=Constants.MODE_XML))
    assert res_partial[1] == 4

    res_exact = _require_search_result(
        search_in_file(str(file_path), "target", special_mode=f"{Constants.MODE_XML} {Constants.MODE_EXACT}")
    )
    assert res_exact[1] == 2
    for match in res_exact[2]:
        content = str(match[2]).lower()
        assert "target" in content
        assert "target_value" not in content


def test_excel_precision(tmp_path):
    file_path = tmp_path / "test.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "Match"
    ws["A2"] = "Matching"
    ws["B1"] = "Match_Extra"
    wb.save(file_path)

    res_partial = _require_search_result(search_in_file(str(file_path), "Match"))
    assert res_partial[1] == 3

    res_exact = _require_search_result(search_in_file(str(file_path), "Match", special_mode=Constants.MODE_EXACT))
    assert res_exact[1] == 1
    assert "A1" in str(res_exact[2][0][2])

