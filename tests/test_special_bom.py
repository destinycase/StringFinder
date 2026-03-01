"""
[test_special_bom.py]

이 테스트는 BOM(Byte Order Mark)이 포함된 특수 형식 파일(JSON, XML, Archive)에 대한 검색 호환성을 검증합니다.

- 테스트 목적:
  1. UTF-8 BOM 등이 포함된 정형 데이터 파일의 파싱 실패(Parser Error) 방지.
  2. BOM 제거 후에도 특수 검색 엔진이 데이터 구조를 정상적으로 유지하는지 확인.

- 주요 검증 사항:
  1. BOM이 포함된 JSON, XML, Archive 파일 검색 결과 정확도.
  2. 인코딩 감지 로직과 특수 검색 모드 간의 상호작용 무결성.
"""

import pytest

from core.search_engine import search_files_list_fast

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

    # 3. Archive (simulated JSON structure) with BOM
    archive_path = tmp_path / "test_bom_data.archive"
    archive_content = {
        "Subnamespaces": [
            {
                "Namespace": "TestNS",
                "Children": [
                    {
                        "Key": "TestKey1",
                        "Source": {"Text": "some_text"},
                        "Translation": {"Text": "FO_Developer_Letter_Book"}
                    }
                ]
            }
        ]
    }
    # It seems archive might be tested. Actually, the Rust engine unpacks zip if the mode is archive.
    # If the file is just text, it will try to deserialize. We'll write it as plain JSON with BOM.
    archive_data = b'\xef\xbb\xbf' + str(archive_content).replace("'", '"').encode('utf-8')
    with open(archive_path, 'wb') as f:
        f.write(archive_data)

    return tmp_path, json_path, xml_path, archive_path


def test_bom_special_search_json(bom_test_env):
    tmp_path, json_path, xml_path, archive_path = bom_test_env
    res = search_files_list_fast([str(json_path)], "FO_Developer_Letter_Book", special_mode="json")
    results = res.get("results", [])
    assert len(results) > 0
    assert "FO_Developer_Letter_Book" in results[0][2][0][1]


def test_bom_special_search_xml(bom_test_env):
    tmp_path, json_path, xml_path, archive_path = bom_test_env
    res = search_files_list_fast([str(xml_path)], "FO_Developer_Letter_Book", special_mode="xml")
    results = res.get("results", [])
    assert len(results) > 0
    assert "FO_Developer_Letter_Book" in results[0][2][0][1]


def test_bom_special_search_archive(bom_test_env):
    tmp_path, json_path, xml_path, archive_path = bom_test_env
    res = search_files_list_fast([str(archive_path)], "FO_Developer_Letter_Book", special_mode="archive")
    results = res.get("results", [])
    assert len(results) > 0
    assert "FO_Developer_Letter_Book" in results[0][2][0][1]
