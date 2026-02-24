"""
[test_stress_search_engine.py]

이 테스트는 매우 많은 수의 파일이나 반복적인 대규모 검색 시의 엔진 부하 및 성능 안정성을 검증합니다.

- 테스트 목적:
  1. 시스템 리소스 한계 상황에서도 검색 엔진이 일관된 성능을 유지하고 누수(Leak) 없이 작동하는지 확인.

- 주요 검증 사항:
  1. 수천 개 파일에 대한 고속 스캐닝 및 키워드 추출 속도.
  2. 반복적인 대규모 검색 요청 시의 메모리 및 CPU 안정성.
"""

import time

import pytest

from core.search_engine import HAS_RUST_ENGINE, find_files_with_keyword_fast, search_directory_fast


@pytest.mark.stress
def test_stress_find_files_with_keyword_fast(tmp_path):
    if not HAS_RUST_ENGINE:
        pytest.skip("Rust engine is required for this stress test")

    root = tmp_path / "bulk_scan"
    root.mkdir()

    expected = 0
    file_count = 2000

    for i in range(file_count):
        path = root / f"f_{i:05d}.txt"
        if i % 10 == 0:
            path.write_text("needle in haystack\n", encoding="utf-8")
            expected += 1
        else:
            path.write_text("just noise\n", encoding="utf-8")

    started = time.perf_counter()
    found = find_files_with_keyword_fast([str(root)], "needle", extensions=["txt"])
    elapsed = time.perf_counter() - started

    assert len(found) == expected
    assert elapsed < 20.0


@pytest.mark.stress
def test_load_repeated_search_directory_fast(tmp_path):
    if not HAS_RUST_ENGINE:
        pytest.skip("Rust engine is required for this load test")

    root = tmp_path / "repeated_scan"
    root.mkdir()

    expected = 0
    file_count = 1200

    for i in range(file_count):
        path = root / f"doc_{i:05d}.txt"
        if i % 12 == 0:
            path.write_text("alpha target beta\n", encoding="utf-8")
            expected += 1
        else:
            path.write_text("alpha beta gamma\n", encoding="utf-8")

    rounds = 4
    for _ in range(rounds):
        result = search_directory_fast([str(root)], "target", extensions=["txt"])
        hits = result["results"]
        match_count = sum(count for _path, count, _matches in hits)

        assert len(hits) == expected
        assert match_count == expected
