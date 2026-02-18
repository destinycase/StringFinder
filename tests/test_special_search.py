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
    # res[2] is list of matches, res[2][0] is match tuple
    # match tuple length depends on implementation but has at least line, content
    # For JSON special, it is (line, path, val_raw) -> 3 elements
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
