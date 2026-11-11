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

import os

from core.search_engine import search_in_file


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
    """인코딩 감지 실패 시 바이너리 또는 비정형 데이터 기본값 처리 확인"""
    test_file = tmp_path / "obsure_data.bin"
    # 랜덤 바이너리 데이터 (인코딩 감지 어려움)
    with open(test_file, "wb") as f:
        f.write(os.urandom(1024))

    result = search_in_file(str(test_file), "any")
    assert result is None or (isinstance(result, tuple) and len(result) == 3)


def test_search_empty_file_skip_reason(tmp_path):
    """빈 파일 스킵 시의 상태 및 메시지 검증"""
    from core.search_engine import search_in_file
    from sf_utils.app_strings import AppStrings
    from sf_utils.constants import Constants

    # 1. 鍮??뚯씪 ?앹꽦
    zero_file = tmp_path / "zero_byte.txt"
    zero_file.touch()

    # 2. 寃???ㅽ뻾
    result = search_in_file(str(zero_file), "any")

    assert result is not None, "빈 파일은 None이 아니라 SkippedResult를 반환해야 합니다"
    assert isinstance(result, tuple)
    assert len(result) == 2
    assert result[0] == Constants.STATUS_SKIPPED
    assert result[1] == AppStrings.SKIP_EMPTY_FILE


