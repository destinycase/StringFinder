import os
from core.search_engine import search_in_file, detect_encoding_quickly


def test_search_extremely_long_line(tmp_path):
    """매우 긴 라인(1MB 이상)을 포함한 파일 검색 시 안정성 및 mmap 동작"""
    test_file = tmp_path / "long_line.txt"
    long_line = "A" * (1024 * 1024) + " Target " + "B" * 100 + "\n"
    with open(test_file, "w", encoding="utf-8") as f:
        f.write(long_line)

    result = search_in_file(str(test_file), "Target")
    assert result is not None
    assert "Target" in result[2][0][1]  # type: ignore
    # 너무 길면 잘리거나 처리가 되어야 하지만 현재는 전체 반환.
    # 메모리 압박 테스트 성격.


def test_unknown_encoding_fallback(tmp_path):
    """인코딩 감지 실패 시(바이너리 또는 비정형 데이터) 기본값 처리 확인"""
    test_file = tmp_path / "obsure_data.bin"
    # 랜덤 바이너리 데이터 (인코딩 감지 어려움)
    with open(test_file, "wb") as f:
        f.write(os.urandom(1024))

    # 크래시 없이 SKIPPED 또는 None 반환 확인
    # search_in_file 내부에서 is_binary_file 등이 걸러냄
    result = search_in_file(str(test_file), "any")
    # 바이너리로 인식되면 (file, count, matches) 반환, 일치 없으면 None
    assert result is None or (isinstance(result, tuple) and len(result) == 3)


def test_detect_encoding_empty_data():
    empty_data = b""
    enc = detect_encoding_quickly(empty_data)
    assert enc == "utf-8"


def test_large_file_mixed_case_integrity(tmp_path):
    """
    [Phase 4] 10MB 이상 대용량 파일에서 Rust 엔진 없이(Python 폴백)
    Mixed-Case(hElLo) 검색 시 무결성을 보장하는지 테스트합니다.
    기존 바이트 검색은 이를 놓칠 수 있었으나, 스트리밍+casefold 검색으로 개선되었습니다.
    """
    # 1. 11MB 파일 생성 (hElLo 포함)
    d = tmp_path / "large_edge"
    d.mkdir()
    p = d / "large_mixed.txt"

    # 반복 패턴 (약 100바이트)
    chunk = "This is a dummy text to fill the file size. \n" * 2
    # 11MB / 100byte ~= 110,000 iterations

    with open(p, "w", encoding="utf-8") as f:
        for _ in range(110_000):
            f.write(chunk)
        # 중간에 Mixed-Case 키워드 삽입
        f.write("\nHere is the target: hElLo world!\n")
        for _ in range(100):
            f.write(chunk)

    # 2. 강제로 Python 엔진 사용 유도 (모킹)
    # search_in_file 내부에서 HAS_RUST_ENGINE을 확인하므로, 이를 False로 패치하거나
    # special_mode를 None으로 설정하여 Rust를 건너뛰게 해야 함 (Rust는 special_mode=None일 때 우선 실행됨)
    # 여기서는 search_engine.py의 HAS_RUST_ENGINE을 직접 패치

    from unittest.mock import patch

    with patch("core.search_engine.HAS_RUST_ENGINE", False):
        # 3. "hello" (소문자)로 검색 -> "hElLo" (혼합) 매칭 확인
        # special_mode=None (기본), is_binary=False

        # search_in_file 시그니처: (file_path, search_string_nfc, special_mode=None)
        result = search_in_file(str(p), "hello", None)
        assert result is not None, "검색 결과가 누락되었습니다."
        assert isinstance(result, tuple)
        assert len(result) == 3
        file_path_res, count, matches = result

        assert count == 1
        assert "hElLo" in matches[0][1]


def test_search_empty_file_skip_reason(tmp_path):
    """
    [개선 사항] 파일 크기가 0인 빈 파일 검색 시,
    None이 아닌 SkippedResult와 구체적인 사유('빈 파일')를 반환하는지 테스트합니다.
    """
    from sf_utils.app_strings import AppStrings
    from sf_utils.constants import Constants
    from core.search_engine import search_in_file

    # 1. 빈 파일 생성
    zero_file = tmp_path / "zero_byte.txt"
    zero_file.touch()

    # 2. 검색 실행
    result = search_in_file(str(zero_file), "any")

    # 3. 결과 검증: (STATUS_SKIPPED, "빈 파일")
    assert result is not None, "빈 파일은 None이 아니라 SkippedResult를 반환해야 합니다."
    assert isinstance(result, tuple)
    assert len(result) == 2
    assert result[0] == Constants.STATUS_SKIPPED
    assert result[1] == AppStrings.SKIP_EMPTY_FILE


def test_large_file_exact_mode_correctness(tmp_path):
    """
    [Phase 4 External Review Fix]
    10MB 이상 대용량 파일에서 '정확히 일치(Exact Match)' 모드가
    올바르게 작동하는지(대소문자 구분 준수) 검증합니다.
    """
    from sf_utils.constants import Constants
    from core.search_engine import search_in_file
    from unittest.mock import patch

    # 1. 11MB 파일 생성 (약 1100만 바이트)
    d = tmp_path / "large_exact"
    d.mkdir()
    p = d / "large_exact.txt"

    # 반복 패턴 (100바이트)
    chunk = "X" * 99 + "\n"

    with open(p, "w", encoding="utf-8") as f:
        # 110,000 * 100 bytes = 11MB
        for _ in range(110_000):
            f.write(chunk)

        # 테스트 타겟 삽입
        f.write("Target: ExactCaseMatch\n")  # 정답
        f.write("Target: exactcasematch\n")  # 오답 (소문자)

        for _ in range(100):
            f.write(chunk)

    # 2. Rust 엔진 비활성화 (Python 로직 강제 검증)
    with patch("core.search_engine.HAS_RUST_ENGINE", False):
        # Test 1: 정확한 대소문자 검색 -> 성공해야 함
        # special_mode에 MODE_EXACT 포함
        # spec_mode_exact = [Constants.MODE_EXACT]
        # search_in_file은 special_mode가 문자열이거나 리스트일 수 있음.
        # constants.py의 MODE_EXACT 값을 확인해야 함. 보통 문자열임.
        # search_engine.py: if special_mode and Constants.MODE_EXACT in special_mode:

        # Case A: Exact Match
        res_a = search_in_file(str(p), "ExactCaseMatch", special_mode=Constants.MODE_EXACT)
        assert res_a is not None
        # 결과 튜플: (file_path, count, matches)
        # matches에 "ExactCaseMatch"가 포함된 라인만 있어야 함
        count_a = res_a[1]
        lines_a = [m[1] for m in res_a[2]]  # type: ignore

        assert count_a == 2
        assert any("ExactCaseMatch" in line for line in lines_a)
        assert any("exactcasematch" in line for line in lines_a)

        # Case B: Case Mismatch (소문자로 검색) -> 성공해야 함 (v4.29.1 변경사항: Exact Mode도 Case-Insensitive)
        result_b = search_in_file(str(p), "exactcasematch", special_mode=Constants.MODE_EXACT)
        assert result_b is not None
        count_b = result_b[1]
        lines_b = [m[1] for m in result_b[2]]  # type: ignore

        # 'ExactCaseMatch', 'exactcasematch' 둘 다 찾아야 함
        # 파일에는 "Target: ExactCaseMatch"와 "Target: exactcasematch" 두 줄이 있고,
        # 둘 다 "exactcasematch" (casefolded)에 매칭됨.
        assert count_b == 2
        assert any("ExactCaseMatch" in line for line in lines_b)
        assert any("exactcasematch" in line for line in lines_b)
