"""BOM이 있는 JSON·XML 파일의 값만 검색."""

import pytest

from core.search_engine import search_directory_fast

@pytest.fixture
def bom_test_env(tmp_path):
    # 1. JSON with BOM
    json_path = tmp_path / "test_bom_data.json"
    json_data = b'\xef\xbb\xbf[\r\n\t{\r\n\t\t"Name": "FO_Developer_Letter_Book",\r\n\t\t"Value": 123\r\n\t}\r\n]'
    with open(json_path, 'wb') as f:
        f.write(json_data)
        
    # 2. XML with BOM
    xml_path = tmp_path / "test_bom_data.xml"
    xml_data = b'\xef\xbb\xbf<?xml version="1.0" encoding="utf-8"?>\n<root>\n\t<node name="FO_Developer_Letter_Book"/>\n</root>'
    with open(xml_path, 'wb') as f:
        f.write(xml_data)
    return tmp_path, json_path, xml_path


def test_bom_special_search_json(bom_test_env):
    tmp_path, json_path, xml_path = bom_test_env
    res = search_directory_fast([str(json_path)], "FO_Developer_Letter_Book", special_mode="JSON")
    results = res.get("results", [])
    assert len(results) > 0
    assert "FO_Developer_Letter_Book" in results[0][2][0][2]  # 구조 검색 결과의 값 칸


def test_bom_special_search_xml(bom_test_env):
    tmp_path, json_path, xml_path = bom_test_env
    res = search_directory_fast([str(xml_path)], "FO_Developer_Letter_Book", special_mode="XML")
    results = res.get("results", [])
    assert len(results) > 0
    assert "FO_Developer_Letter_Book" in results[0][2][0][2]  # 구조 검색 결과의 값 칸

