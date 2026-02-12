import json
from core.search_engine import strip_comments, search_in_json_special, search_in_xml_special, search_in_file


def test_strip_comments_line_preservation():
    """주석 제거 시 줄 번호(개행)가 보존되는지 테스트"""
    content = "line1\n// comment\nline3\n/* multicline\ncomment */\nline6"
    stripped = strip_comments(content, ".js")
    lines = stripped.split("\n")
    assert len(lines) == 6
    assert "line1" in lines[0]
    assert "line3" in lines[2]
    assert "line6" in lines[5]
    # 주석 내용은 공백으로 치환되어야 함
    assert "comment" not in lines[1]
    assert "multiline" not in stripped


def test_json_exact_match_precision(tmp_path):
    """JSON 전체 일치 시 부분 일치(id vs QVideoFrame)가 섞이지 않는지 테스트"""
    data = {"className": "QVideoFrame", "attribute": "id", "someText": "This id is unique"}
    file_path = tmp_path / "test.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f)

    # 1. 부분 일치 검색 (3개 모두 발견되어야 함)
    res_partial = search_in_json_special(str(file_path), "id", exact_match=False)
    assert res_partial[1] == 3

    # 2. 전체 일치 검색 (정확히 "id"인 것만 발견되어야 함)
    res_exact = search_in_json_special(str(file_path), "id", exact_match=True)
    assert res_exact[1] == 1
    assert res_exact[2][0][2] == "id"  # 실제 매치된 밸류 확인


def test_xml_exact_match_precision(tmp_path):
    """XML 전체 일치 시 정확한 매칭 검증"""
    xml_content = """<?xml version="1.0" encoding="utf-8"?>
    <root>
        <item id="exact_id">Part of exact_id</item>
        <note>exact_id</note>
    </root>
    """
    file_path = tmp_path / "test.xml"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(xml_content)

    # 전체 일치 검색
    res = search_in_xml_special(str(file_path), "exact_id", exact_match=True)
    # id="exact_id" 와 <note>exact_id</note> 두 개가 검색되어야 함 (텍스트 부분 일치는 제외)
    assert res[1] == 2
    for m in res[2]:
        assert m[2] == "exact_id"


def test_invalid_format_handling(tmp_path):
    """무효한 형식의 파일 스킵 테스트"""
    # 확실히 파싱이 불가능한 깨진 데이터
    bad_json = '{"key": "value"'  # unclosed brace
    json_path = tmp_path / "bad.json"
    with open(json_path, "w") as f:
        f.write(bad_json)

    # 1. JSON (부분 일치)
    res_direct = search_in_json_special(str(json_path), "value")
    assert isinstance(res_direct, tuple)
    assert res_direct[0] == "SKIPPED"

    res_file = search_in_file(str(json_path), "value", special_mode="JSON (부분 일치)")
    assert isinstance(res_file, tuple)
    assert res_file[0] == "SKIPPED"

    # 2. XML (부분 일치)
    bad_xml = "<root><unclosedTag>content"
    xml_path = tmp_path / "bad.xml"
    with open(xml_path, "w") as f:
        f.write(bad_xml)

    res_direct_xml = search_in_xml_special(str(xml_path), "content")
    assert isinstance(res_direct_xml, tuple)
    assert res_direct_xml[0] == "SKIPPED"

    res_file_xml = search_in_file(str(xml_path), "content", special_mode="XML (부분 일치)")
    assert isinstance(res_file_xml, tuple)
    assert res_file_xml[0] == "SKIPPED"


def test_no_fallback_for_special_mode(tmp_path):
    """특수 검색 모드 시 일반 검색으로 Fallback되지 않는지 테스트 (주석 내 검색어 제외 검증)"""
    # 주석 안에만 검색어가 있는 JSON (표준 JSON은 주석을 지원하지 않으므로 SKIPPED 처리됨)
    content = """
    {
        // find_me_in_comment
        "val": "other_value"
    }
    """
    file_path = tmp_path / "test_valid.json"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)

    # JSON 특수 모드 활성화 시 주석이 포함된 JSON은 SKIPPED 처리됨
    result = search_in_file(str(file_path), "find_me_in_comment", special_mode="JSON (부분 일치)")
    assert isinstance(result, tuple)
    assert result[0] == "SKIPPED"
