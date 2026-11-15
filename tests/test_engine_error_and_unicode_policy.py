"""엔진 오류 코드가 파일 건너뜀·오류로 처리되는지와 독일어·터키어 대소문자 비교."""

from typing import Any, List

import folder_search_helper
from folder_search_helper import search_in_json_special, search_in_xml_special
from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants


def test_json_size_limit_code_is_handled_as_file_skip(tmp_path, monkeypatch):
    file_path = tmp_path / "large.json"
    file_path.write_text('{"key": "value"}')

    # 엔진은 파일 오류를 결과가 아니라 건너뛴 목록으로 보냅니다(7.2.9).
    import core.search_engine
    monkeypatch.setattr(core.search_engine, "sf_engine", folder_search_helper.fake_dir_engine(
        skipped=[(str(file_path), "ERR_JSON_SIZE_LIMIT|Size exceeds limit")]))

    result = search_in_json_special(str(file_path), "value")

    assert isinstance(result, tuple)
    assert result[0] == Constants.STATUS_SKIPPED
    assert "JSON 파일 크기 제한 초과" in str(result[1])
    assert "메모리 보호" not in str(result[1])


def test_generic_rust_error_prevents_silent_failure(tmp_path, monkeypatch, caplog):
    """
    일반 검색 모드에서 Rust 엔진이 'ERR_PANIC|...' 사유로 파일을 건너뛰었을 때,
    단순히 '일치 항목 0건'으로 조용히 실패(Silent Failure)하지 않고
    반드시 STATUS_SKIPPED 처리되는지 확인하는 방어적 단위 테스트입니다.
    """
    file_path = tmp_path / "dummy.txt"
    file_path.write_text("dummy")

    # 엔진은 파일 오류를 결과가 아니라 건너뛴 목록으로 보냅니다(7.2.9).
    import core.search_engine
    monkeypatch.setattr(core.search_engine, "sf_engine", folder_search_helper.fake_dir_engine(
        skipped=[(str(file_path), "ERR_PANIC|Simulated engine crash")]))

    result = folder_search_helper.search_in_file(str(file_path), "query")

    assert result is not None
    assert isinstance(result, tuple)
    assert result[0] == Constants.STATUS_SKIPPED
    assert AppStrings.SKIP_DETAIL_INTERNAL_FAILURE in str(result[1])
    assert "Simulated engine crash" in caplog.text


def test_casefold_unicode_german(tmp_path):
    file_path = tmp_path / "german.json"
    file_path.write_text('{"name": "STRAẞE"}', encoding="utf-8")

    result = search_in_json_special(str(file_path), "strasse")

    assert result is not None
    res_list: List[Any] = list(result)
    assert int(res_list[1]) == 1
    # List indexing to avoid MyPy tuple length error
    matches: List[Any] = list(res_list[2])
    assert "STRAẞE" in str(matches[0][2])


def test_casefold_unicode_turkish(tmp_path):
    file_path = tmp_path / "turkish.xml"
    file_path.write_text("<root><tag>İSTANBUL</tag></root>", encoding="utf-8")

    result = search_in_xml_special(str(file_path), "İSTANBUL")

    assert result is not None
    res_list: List[Any] = list(result)
    assert int(res_list[1]) == 1
