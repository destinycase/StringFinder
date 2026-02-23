"""
[test_special_search.py]

이 테스트는 JSON, XML 등 형식이 지정된 구조화된 데이터 특수 검색 기능을 검증합니다.

- 테스트 목적:
  1. 일반 텍스트 검색이 아닌 구조 기반 검색(Structural Search) 시의 매칭 정확도 보장.

- 주요 검증 사항:
  1. JSON 키(Key) 및 값(Value) 영역의 구분 검색.
  2. XML 태그명 및 속성값 영역의 정밀 검색.
"""

import json
import xml.etree.ElementTree as ET

import pytest

from core.search_engine import search_in_json_special, search_in_xml_special


@pytest.fixture
def temp_json_file(tmp_path):
    data = {
        "key": "value",
        "nested": {"num": 12345, "bool": True, "target": "find_me"},
        "list": ["item1", "target_in_list"],
    }
    file_path = tmp_path / "test.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return str(file_path)


@pytest.fixture
def temp_xml_file(tmp_path):
    root = ET.Element("root")
    child1 = ET.SubElement(root, "child", id="target_id", name="starter")
    child1.text = "inner_text"
    child2 = ET.SubElement(root, "other", value="10.0.26100.1")
    child2.text = "not_this"

    file_path = tmp_path / "test.xml"
    tree = ET.ElementTree(root)
    tree.write(file_path, encoding="utf-8", xml_declaration=True)
    return str(file_path)


def test_json_special_search(temp_json_file):
    res = search_in_json_special(temp_json_file, "find_me")
    assert res is not None
    assert isinstance(res, tuple)
    assert len(res[2][0]) >= 3  # type: ignore
    assert res[2][0][2] == "find_me"  # type: ignore

    res = search_in_json_special(temp_json_file, "target_in_list")
    assert res is not None
    assert res[1] == 1

    res = search_in_json_special(temp_json_file, "nested")
    assert res is None

    res = search_in_json_special(temp_json_file, "12345")
    assert res is not None


def test_xml_special_search(temp_xml_file):
    res = search_in_xml_special(temp_xml_file, "target_id")
    assert res is not None
    assert isinstance(res, tuple)
    assert len(res) == 3
    assert len(res[2]) > 0
    assert len(res[2][0]) >= 3  # type: ignore
    assert "target_id" in res[2][0][2]  # type: ignore

    res = search_in_xml_special(temp_xml_file, "inner_text")
    assert res is not None

    res = search_in_xml_special(temp_xml_file, "root")
    assert res is None

    res = search_in_xml_special(temp_xml_file, "26100")
    assert res is not None
