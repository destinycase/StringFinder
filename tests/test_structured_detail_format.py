"""JSON·XML 값 검색 결과의 상세 형식(경로·값·줄 번호)을 고정합니다."""

import json
from typing import Any, List

import pytest

from folder_search_helper import search_in_json_special, search_in_xml_special


@pytest.fixture
def json_file(tmp_path):
    data = {"settings": {"theme": "Dark", "volume": 80}}
    file_name = tmp_path / "test.json"
    file_name.write_text(json.dumps(data), encoding="utf-8")
    return str(file_name)


@pytest.fixture
def xml_file(tmp_path):
    content = '<root><item id="main_node" value="active">Inner Content</item></root>'
    file_name = tmp_path / "test.xml"
    file_name.write_text(content, encoding="utf-8")
    return str(file_name)


def test_json_detail_format(json_file):
    res = search_in_json_special(json_file, "Dark")
    assert res is not None
    res_list: List[Any] = list(res)
    assert int(res_list[1]) == 1
    matches: List[Any] = list(res_list[2])
    assert str(matches[0][1]) == "settings.theme"
    assert str(matches[0][2]) == "Dark"


def test_xml_detail_format(xml_file):
    res_attr = search_in_xml_special(xml_file, "main_node")
    assert res_attr is not None
    res_list: List[Any] = list(res_attr)
    matches: List[Any] = list(res_list[2])
    assert "id" in str(matches[0][1])
    assert str(matches[0][2]) == "main_node"

    res_text = search_in_xml_special(xml_file, "Inner Content")
    assert res_text is not None
    res_list_text: List[Any] = list(res_text)
    matches_text: List[Any] = list(res_list_text[2])
    assert "item" in str(matches_text[0][1])
    assert str(matches_text[0][2]) == "Inner Content"



def test_json_number_line_bug_fix(tmp_path):
    json_content = '{\n  "id": 123456789,\n  "next": "none"\n}'
    f_path = tmp_path / "line_test.json"
    f_path.write_text(json_content, encoding="utf-8")
    res = search_in_json_special(str(f_path), "123456789")
    assert res is not None
    res_list: List[Any] = list(res)
    matches: List[Any] = list(res_list[2])
    assert int(matches[0][0]) == 2
