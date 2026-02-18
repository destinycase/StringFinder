import json
import openpyxl
from core.search_engine import search_in_file


def test_normal_text_precision(tmp_path):
    """일반 텍스트 파일 매칭 정밀도 테스트"""
    file_path = tmp_path / "test.txt"
    file_path.write_text("user_id\nid\nidentity", encoding="utf-8")

    # 부분 일치: id를 찾으면 user_id, id, identity 모두 검색되어야 함 (Case-insensitive)
    res_partial = search_in_file(str(file_path), "id")
    assert res_partial is not None
    assert res_partial[1] == 3

    # 정확히 일치: id만 검색되어야 함
    res_exact = search_in_file(str(file_path), "id", special_mode="정확히 일치")
    assert res_exact is not None
    assert res_exact[1] == 1
    assert res_exact[2][0][1].strip() == "id"


def test_json_special_precision(tmp_path):
    """JSON 특수 검색 매칭 정밀도 테스트"""
    data = {"user": "admin", "user_id": "admin_123", "description": "This is admin user"}
    file_path = tmp_path / "test.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f)

    # 부분 일치
    res_partial = search_in_file(str(file_path), "admin", special_mode="JSON (부분 일치)")
    assert res_partial is not None
    assert res_partial[1] == 3

    # 정확히 일치
    res_exact = search_in_file(str(file_path), "admin", special_mode="JSON (정확히 일치)")
    assert res_exact is not None
    assert res_exact[1] == 1
    assert res_exact[2][0][2] == "admin"


def test_xml_special_precision(tmp_path):
    """XML 특수 검색 매칭 정밀도 테스트"""
    xml_content = """<root>
        <item name="target">target_value</item>
        <item name="target_extra">extra</item>
        <note>target</note>
    </root>"""
    file_path = tmp_path / "test.xml"
    file_path.write_text(xml_content, encoding="utf-8")

    # 부분 일치
    res_partial = search_in_file(str(file_path), "target", special_mode="XML (부분 일치)")
    assert res_partial is not None
    assert res_partial[1] == 4  # name="target", "target_value", name="target_extra", <note>target</note>

    # 정확히 일치
    res_exact = search_in_file(str(file_path), "target", special_mode="XML (정확히 일치)")
    assert res_exact is not None
    assert res_exact[1] == 2  # name="target" 과 <note>target</note>
    for m in res_exact[2]:
        assert "target" in m[2].lower()
        assert "target_value" not in m[2].lower()


def test_excel_precision(tmp_path):
    """엑셀 파일 매칭 정밀도 테스트"""
    file_path = tmp_path / "test.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "Match"
    ws["A2"] = "Matching"
    ws["B1"] = "Match_Extra"
    wb.save(file_path)

    # 부분 일치
    res_partial = search_in_file(str(file_path), "Match")
    assert res_partial is not None
    assert res_partial[1] == 3

    # 정확히 일치
    res_exact = search_in_file(str(file_path), "Match", special_mode="정확히 일치")
    assert res_exact is not None
    assert res_exact[1] == 1
    assert "A1" in res_exact[2][0][1]


def test_archive_precision(tmp_path):
    """Archive 특수 검색 매칭 정밀도 테스트"""
    data = {
        "Subnamespaces": [
            {
                "Namespace": "Game",
                "Children": [
                    {"Key": "K1", "Source": {"Text": "Start"}, "Translation": {"Text": "시작"}},
                    {"Key": "K2", "Source": {"Text": "Restart"}, "Translation": {"Text": "재시작"}},
                ],
            }
        ]
    }
    file_path = tmp_path / "test.archive"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f)

    # 부분 일치
    res_partial = search_in_file(str(file_path), "Start", special_mode="Archive (부분 일치)")
    assert res_partial is not None
    assert res_partial[1] == 2  # 시작, 재시작 모두 포함 (Source 기준 Start, Restart)

    # 정확히 일치
    res_exact = search_in_file(str(file_path), "Start", special_mode="Archive (정확히 일치)")
    assert res_exact is not None
    assert res_exact[1] == 1
    assert res_exact[2][0][3] == "Start"
