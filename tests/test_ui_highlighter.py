from ui.search_tab import SearchTab


def test_highlight_xml_exact_match():
    """XML 전체 일치 강조 테스트: 속성 값만 강조하고 일반 문장은 제외되어야 함"""
    # 원본: <xs:attribute name="id" type="xs:ID">
    content = '&lt;xs:attribute name="id" type="xs:ID"&gt;'
    search_text = "id"

    # XML 전체 일치
    result = SearchTab.get_highlighted_html(content, search_text, is_xml=True, is_json=False, is_exact=True)

    # name="id" 부분만 강조되어야 함 (xs:ID의 ID는 제외)
    assert "name=\"<span style='color: #ff9900; font-weight: bold;'>id</span>\"" in result
    assert 'type="xs:<span' not in result


def test_highlight_xml_documentation_guard():
    """XML 전체 일치 강조 테스트: documentation 내의 일반 문장은 강조하지 않아야 함"""
    # 원본: The id attribute is a text string
    content = "The id attribute is a text string"
    search_text = "id"

    # XML 전체 일치
    result = SearchTab.get_highlighted_html(content, search_text, is_xml=True, is_json=False, is_exact=True)

    # 구조적 특징(' > <)이 없으므로 강조하지 않아야 함
    assert "<span" not in result


def test_highlight_json_exact_match():
    """JSON 전체 일치 강조 테스트: 값 부분만 정확히 강조되어야 함"""
    # 원본: "key": "id", "other": "identifier"
    content = '"key":"id", "other":"identifier"'
    search_text = "id"

    # JSON 전체 일치
    result = SearchTab.get_highlighted_html(content, search_text, is_xml=False, is_json=True, is_exact=True)

    # "id" 만 강조하고 identifier의 id는 제외
    assert ":\"<span style='color: #ff9900; font-weight: bold;'>id</span>\"" in result
    assert "identifier" in result
    assert "identi<span" not in result


def test_highlight_normal_exact_match():
    """일반 모드 전체 일치 강조 테스트: 식별자 구분자 확인"""
    content = "id identifier xs:id"
    search_text = "id"

    # 일반 전체 일치
    result = SearchTab.get_highlighted_html(content, search_text, is_xml=False, is_json=False, is_exact=True)

    # 독립된 id만 강조 (identifier나 xs:id의 id는 식별자 문자가 붙어있어 제외)
    assert "<span" in result
    assert result.count("<span") == 1
    assert "identifier" in result
    assert "xs:id" in result


def test_highlight_case_insensitive():
    """대소문자 구분 없는 강조 테스트 (값 부분)"""
    content = 'name="ID"'
    search_text = "id"

    result = SearchTab.get_highlighted_html(content, search_text, is_xml=True, is_json=False, is_exact=True)
    assert "<span" in result
    assert "ID" in result


def test_highlight_empty_search():
    """검색어 없을 때 원본 반환 테스트"""
    content = "some content"
    assert SearchTab.get_highlighted_html(content, "", False, False, False) == content
