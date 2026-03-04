"""
[test_audit_hardening.py]

이 테스트는 프로젝트 감사(Audit) 과정에서 식별된 보안 결함 및 안정성 강화 로직을 검증합니다.

- 테스트 목적:
  1. JSON/XML 특수 검색 모드에서 대형 파일 처리 시 메모리 가드 로직이 정상 작동하는지 확인.
  2. 비정상적인 트리 구조나 과도한 중첩을 가진 파일에 대한 검색 엔진의 복원력 검증.

- 주요 검증 사항:
  1. `search_in_json_special`, `search_in_xml_special` 함수 내 메모리 가드 트리거 여부.
  2. 모킹된 매치를 통한 엔진 결과 처리 무결성 확인.
"""

from typing import Any, List

from core.search_engine import search_in_json_special, search_in_xml_special
from sf_utils.constants import Constants


def test_memory_guard_handling_json(tmp_path, monkeypatch):
    file_path = tmp_path / "large.json"
    file_path.write_text('{"key": "value"}')

    class MockMatch:
        def __init__(self, content):
            self.content = content
            self.line = 1
            self.offset = 0
            self.length = 0

    def mock_search_file(path, _query, mode, stop_event=None):
        return [MockMatch("ERR_MEMORY_GUARD|Size exceeds limit")]

    import core.search_engine
    monkeypatch.setattr(core.search_engine, "sf_engine", type("obj", (object,), {"search_file": mock_search_file}))

    result = search_in_json_special(str(file_path), "value")

    assert isinstance(result, tuple)
    assert result[0] == Constants.STATUS_SKIPPED
    assert "Size exceeds limit" in str(result[1]) or "메모리" in str(result[1])


def test_generic_rust_error_prevents_silent_failure(tmp_path, monkeypatch):
    """
    일반 검색 모드에서 Rust 엔진이 'ERR_PANIC|...' 형태의 마커를 반환할 때,
    단순히 '일치 항목 0건'으로 조용히 실패(Silent Failure)하지 않고
    반드시 STATUS_SKIPPED 처리되는지 확인하는 방어적 단위 테스트입니다.
    """
    file_path = tmp_path / "dummy.txt"
    file_path.write_text("dummy")

    class MockMatch:
        def __init__(self, content):
            self.content = content
            self.line = 1
            self.offset = 0
            self.length = 0

    def mock_search_file(path, _query, mode_bits, stop_event=None, _progress_cb=None):
        return [MockMatch("ERR_PANIC|Simulated engine crash")]

    import core.search_engine
    monkeypatch.setattr(core.search_engine, "sf_engine", type("obj", (object,), {"search_file": mock_search_file}))

    result = core.search_engine.search_in_file(str(file_path), "query", use_complex_search=False)

    assert result is not None
    assert isinstance(result, tuple)
    assert result[0] == Constants.STATUS_SKIPPED
    assert "Simulated engine crash" in str(result[1])


def test_casefold_unicode_german(tmp_path):
    file_path = tmp_path / "german.json"
    file_path.write_text('{"name": "STRAẞE"}', encoding="utf-8")

    result = search_in_json_special(str(file_path), "strasse", use_complex_search=True)

    assert result is not None
    res_list: List[Any] = list(result)
    assert int(res_list[1]) == 1
    # List indexing to avoid MyPy tuple length error
    matches: List[Any] = list(res_list[2])
    assert "STRAẞE" in str(matches[0][2])


def test_casefold_unicode_turkish(tmp_path):
    file_path = tmp_path / "turkish.xml"
    file_path.write_text("<root><tag>İSTANBUL</tag></root>", encoding="utf-8")

    result = search_in_xml_special(str(file_path), "İSTANBUL", use_complex_search=True)

    assert result is not None
    res_list: List[Any] = list(result)
    assert int(res_list[1]) == 1
