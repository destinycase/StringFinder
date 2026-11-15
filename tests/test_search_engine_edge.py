"""아주 긴 줄과 알 수 없는 인코딩 처리."""


from folder_search_helper import search_in_file


def test_search_extremely_long_line(tmp_path):
    """초장거리 단일 라인 파일 검색 테스트"""
    test_file = tmp_path / "long_line.txt"
    long_line = "A" * (1024 * 1024) + " Target " + "B" * 100 + "\n"
    with open(test_file, "w", encoding="utf-8") as f:
        f.write(long_line)

    result = search_in_file(str(test_file), "Target")
    assert result is not None
    assert "Target" in result[2][0][1]  # type: ignore
    # 너무 길면 자르거나 처리가 되어야 하지만 현재는 전체 반환.
    # 메모리 압박 테스트 성격.


def test_unknown_encoding_fallback(tmp_path):
    """인코딩을 알 수 없는 비정형 데이터: 결과 없음과 부분 검색 안내를 고정 입력으로 각각 확인합니다.

    무작위 바이트는 실행마다 UTF-8로 읽히는 정도가 달라 결과 형태가 바뀌므로 쓰지 않습니다.
    """
    from sf_utils.app_strings import AppStrings
    from sf_utils.constants import Constants

    # 모두 UTF-8로 읽히지만 검색어가 없는 데이터 → 결과 없음
    readable = tmp_path / "readable.bin"
    readable.write_bytes(bytes(range(0x20, 0x7F)) * 8)
    assert search_in_file(str(readable), "zzzz_absent") is None

    # UTF-8로 읽을 수 없는 바이트가 섞인 데이터 → 결과 없이 부분 검색 안내
    broken = tmp_path / "broken.bin"
    broken.write_bytes(b"plain text " + bytes([0xC3, 0x28, 0xA0, 0xA1, 0xFF, 0xFE]) * 50 + b" more text")
    result = search_in_file(str(broken), "zzzz_absent")
    assert result is not None and result[0] == Constants.STATUS_SKIPPED
    assert result[1] == AppStrings.SKIP_REASON_DECODE_PARTIAL.format("UTF-8")

    # 읽을 수 없는 부분이 있어도 읽은 부분의 일치는 찾습니다
    found = search_in_file(str(broken), "more text")
    assert found is not None and len(found) == 3 and found[1] == 1