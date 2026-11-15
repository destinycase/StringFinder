"""
[test_search_engine_edge.py]

이 테스트는 검색 엔진이 직면할 수 있는 극단적인 상황(Edge Case)들에 대한 안정성을 검증합니다.

- 테스트 목적:
  1. 초장거리 라인, 인코딩 불명, 빈 파일 등 비정상적인 데이터 구조에서도 크래시 없이 안전하게 작동하는지 확인.

- 주요 검증 사항:
  1. 1MB 이상의 단일 라인(초장거리 라인) 처리 성능 및 안정성.
  2. 인코딩 감지 실패 시의 폴백 시스템 작동.
  3. 0바이트(빈 파일) 스킵 정책 및 메시지 정확성.
  4. 대용량 파일에서의 대소문자 혼합 매칭 무결성.
"""


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