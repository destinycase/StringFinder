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

from core.search_engine import detect_encoding_quickly, search_in_file


def test_search_extremely_long_line(tmp_path):
    """test_search_extremely_long_line ?⑥닔."""
    test_file = tmp_path / "long_line.txt"
    long_line = "A" * (1024 * 1024) + " Target " + "B" * 100 + "\n"
    with open(test_file, "w", encoding="utf-8") as f:
        f.write(long_line)

    result = search_in_file(str(test_file), "Target")
    assert result is not None
    assert "Target" in result[2][0][1]  # type: ignore
    # ?덈Т 湲몃㈃ ?섎━嫄곕굹 泥섎━媛 ?섏뼱???섏?留??꾩옱???꾩껜 諛섑솚.
    # 硫붾え由??뺣컯 ?뚯뒪???깃꺽.


def test_unknown_encoding_fallback(tmp_path):
    """?몄퐫??媛먯? ?ㅽ뙣 ??諛붿씠?덈━ ?먮뒗 鍮꾩젙???곗씠?? 湲곕낯媛?泥섎━ ?뺤씤"""
    test_file = tmp_path / "obsure_data.bin"
    # ?쒕뜡 諛붿씠?덈━ ?곗씠??(?몄퐫??媛먯? ?대젮?)
    with open(test_file, "wb") as f:
        f.write(os.urandom(1024))

    result = search_in_file(str(test_file), "any")
    assert result is None or (isinstance(result, tuple) and len(result) == 3)


def test_detect_encoding_empty_data():
    empty_data = b""
    enc = detect_encoding_quickly(empty_data)
    assert enc == "utf-8"


def test_large_file_mixed_case_integrity(tmp_path):
    """test_large_file_mixed_case_integrity ?⑥닔."""
    d = tmp_path / "large_edge"
    d.mkdir()
    p = d / "large_mixed.txt"

    # 諛섎났 ?⑦꽩 (??100諛붿씠??
    chunk = "This is a dummy text to fill the file size. \n" * 2

    with open(p, "w", encoding="utf-8") as f:
        for _ in range(110_000):
            f.write(chunk)
        f.write("\nHere is the target: hElLo world!\n")
        for _ in range(100):
            f.write(chunk)

    from unittest.mock import patch

    with patch("core.search_engine.HAS_RUST_ENGINE", False):
        result = search_in_file(str(p), "hello", None)
        assert result is not None, "寃??寃곌낵媛 ?꾨씫?섏뿀?듬땲??"
        assert isinstance(result, tuple)
        assert len(result) == 3
        file_path_res, count, matches = result

        assert count == 1
        assert "hElLo" in matches[0][1]


def test_search_empty_file_skip_reason(tmp_path):
    """test_search_empty_file_skip_reason ?⑥닔."""
    from core.search_engine import search_in_file
    from sf_utils.app_strings import AppStrings
    from sf_utils.constants import Constants

    # 1. 鍮??뚯씪 ?앹꽦
    zero_file = tmp_path / "zero_byte.txt"
    zero_file.touch()

    # 2. 寃???ㅽ뻾
    result = search_in_file(str(zero_file), "any")

    assert result is not None, "鍮??뚯씪? None???꾨땲??SkippedResult瑜?諛섑솚?댁빞 ?⑸땲??"
    assert isinstance(result, tuple)
    assert len(result) == 2
    assert result[0] == Constants.STATUS_SKIPPED
    assert result[1] == AppStrings.SKIP_EMPTY_FILE


def test_large_file_exact_mode_correctness(tmp_path):
    """test_large_file_exact_mode_correctness 함수."""
    from unittest.mock import patch

    from core.search_engine import search_in_file
    from sf_utils.constants import Constants

    d = tmp_path / "large_exact"
    d.mkdir()
    p = d / "large_exact.txt"

    # 반복 패턴 (100바이트)
    chunk = "X" * 99 + "\n"

    with open(p, "w", encoding="utf-8") as f:
        for _ in range(110_000):
            f.write(chunk)

        # exact 불일치 라인 + exact 일치 라인 혼합
        f.write("Target: ExactCaseMatch\n")
        f.write("Target: exactcasematch\n")
        f.write("ExactCaseMatch\n")
        f.write("exactcasematch\n")

        for _ in range(100):
            f.write(chunk)

    with patch("core.search_engine.HAS_RUST_ENGINE", False):
        res_a = search_in_file(str(p), "ExactCaseMatch", special_mode=Constants.MODE_EXACT)
        assert res_a is not None
        count_a = res_a[1]
        lines_a = [m[1] for m in res_a[2]]  # type: ignore

        assert count_a == 2
        assert "ExactCaseMatch" in lines_a
        assert "exactcasematch" in lines_a

        result_b = search_in_file(str(p), "exactcasematch", special_mode=Constants.MODE_EXACT)
        assert result_b is not None
        count_b = result_b[1]
        lines_b = [m[1] for m in result_b[2]]  # type: ignore

        assert count_b == 2
        assert "ExactCaseMatch" in lines_b
        assert "exactcasematch" in lines_b
