"""
[test_chaos_fuzzing.py]

이 테스트는 비정상적이거나 악의적인 입력값(Fuzz inputs)에 대한 검색 엔진의 크래시 저항성을 검증합니다.

- 테스트 목적:
  1. 완전한 이진 데이터, 잘못된 인코딩 시퀀스 등 예측 불가능한 파일 내용에 대한 안정성 확보.
  2. 매우 긴 라인(Huge Line) 등 메모리 부하를 유발하는 환경에서의 비정상 종료 방지.

- 주요 검증 사항:
  1. 랜덤 생성된 바이너리 파일에 대한 무작위 검색(Fuzzing) 수행.
  2. UTF-8, CP949 인코딩이 파괴된 파일에 대한 검색 시도.
  3. 거대하게 조작된 단일 라인이 포함된 파일 처리 무합성.
"""

import os
import random

import pytest

from core.search_engine import search_in_files_batch


@pytest.mark.chaos
def test_chaos_binary_fuzz_inputs(tmp_path):
    rng = random.Random(1234)

    for i in range(8):
        size = rng.randint(1024, 512 * 1024)
        path = tmp_path / f"fuzz_bin_{i}.bin"
        data = os.urandom(size)
        path.write_bytes(data)

        result = search_in_files_batch([(str(path), size)], "needle", None)
        assert "results" in result
        assert "skipped" in result


@pytest.mark.chaos
def test_chaos_malformed_encoding_inputs(tmp_path):
    malformed_utf8 = b"\xed\xa0\x80"
    malformed_cp949 = b"\xff\xfe\xfa"

    p1 = tmp_path / "bad_utf8.txt"
    p2 = tmp_path / "bad_cp949.txt"
    p1.write_bytes(malformed_utf8)
    p2.write_bytes(malformed_cp949)

    result = search_in_files_batch(
        [
            (str(p1), len(malformed_utf8)),
            (str(p2), len(malformed_cp949)),
        ],
        "test",
        None,
    )
    assert "results" in result
    assert "skipped" in result


@pytest.mark.chaos
def test_chaos_huge_line_input(tmp_path):
    huge_line = b"a" * (8 * 1024 * 1024)
    path = tmp_path / "huge_line.txt"
    path.write_bytes(huge_line)

    result = search_in_files_batch([(str(path), len(huge_line))], "test", None)
    assert "results" in result
    assert "skipped" in result
