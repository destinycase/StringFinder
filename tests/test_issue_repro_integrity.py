from core.search_engine import search_in_file


def test_complex_search_unicode_integrity_repro(tmp_path):
    """
    외부 리뷰 제보 이슈 재현 및 해결 검증:
    'strasse'와 'straße'가 섞인 파일에서 'strasse' 검색 시 복합 검색 모드에서 2건이 모두 나와야 함.
    """
    content = "strasse\nstraße\n"
    test_file = tmp_path / "german_unicode.txt"
    test_file.write_text(content, encoding="utf-8")

    # 1. 일반 검색 (Rust 우선): Rust는 Simple CaseFolding만 지원하므로 'strasse' 1건만 기대
    result_simple = search_in_file(str(test_file), "strasse", use_complex_search=False)
    assert result_simple is not None and len(result_simple) == 3
    res_path_s, count_simple, matches_simple = result_simple
    assert count_simple == 1

    # 2. 복합 검색 (Python 강제): Python의 Full CaseFolding을 통해 2건 모두 기대
    result_complex = search_in_file(str(test_file), "strasse", use_complex_search=True)
    assert result_complex is not None and len(result_complex) == 3
    res_path_c, count_complex, matches_complex = result_complex
    assert count_complex == 2

    # 상세 매치 내용 검증
    contents = [m[1] for m in matches_complex]
    assert "strasse" in contents
    assert "straße" in contents
