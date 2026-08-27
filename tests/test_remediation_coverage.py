"""
[test_remediation_coverage.py]

이 테스트는 최근 수행된 코드 수정(Remediation) 및 기능 강화 사항들에 대한 테스트 커버리지를 보장합니다.

- 테스트 목적:
  1. JSON/XML 등 특수 검색 모드의 기술적 부채 해결 시나리오 검증.
  2. 복합 검색 옵션 활성화 시의 엔진 폴백(Rust -> Python) 정확도 및 무결성 확인.

- 주요 검증 사항:
  1. 특수 확장자 파일 내의 결과 형식(Detail Format) 정확도.
  2. 라인 뒤죽박죽 또는 숫자 표기 버그 등에 대한 수정 사항 확인.
  3. `use_complex_search` 플래그에 따른 엔진 분기 및 결과 도합 정확도.
"""

import json
from typing import Any, List

import pytest

from core.search_engine import search_in_file, search_in_json_special, search_in_xml_special



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



def test_complex_search_python_fallback(tmp_path):
    f_path = tmp_path / "german.txt"
    f_path.write_text("Das ist die Maße.", encoding="utf-8")
    res = search_in_file(str(f_path), "Masse", use_complex_search=True)
    assert res is not None
    res_list: List[Any] = list(res)
    assert int(res_list[1]) == 1
    matches: List[Any] = list(res_list[2])
    assert "Maße" in str(matches[0][1])


def test_json_number_line_bug_fix(tmp_path):
    json_content = '{\n  "id": 123456789,\n  "next": "none"\n}'
    f_path = tmp_path / "line_test.json"
    f_path.write_text(json_content, encoding="utf-8")
    res = search_in_json_special(str(f_path), "123456789")
    assert res is not None
    res_list: List[Any] = list(res)
    matches: List[Any] = list(res_list[2])
    assert int(matches[0][0]) == 2
