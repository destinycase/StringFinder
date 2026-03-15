"""
[test_perf_check.py]

이 테스트는 검색 엔진의 핵심 성능 지표 및 알고리즘 효율성을 검증합니다.

- 테스트 목적:
  1. 특정 패턴(매우 긴 라인, 다량의 매치 등)에서의 성능 퇴보(Regression) 탐지.
  2. Rust 엔진의 안정성 및 패닉(Panic) 방지 로직 보증.

- 주요 검증 사항:
  1. 단일 라인 내 수만 건의 매치 발생 시 선형 시간 내 처리 여부.
  2. 대용량 XML/JSON 처리 시의 엔진 안정성 재현 테스트.
"""

import time
from core.search_engine import search_in_file


def test_performance_many_matches_on_one_line(tmp_path):
    # 1. 1MB 크기의 단일 라인 파일 생성 (10,000개의 매치 포함)
    file_path = tmp_path / "long_line_many_matches.txt"
    content = "target " * 10000 + "word " * 100000
    file_path.write_text(content, encoding="utf-8")

    # 2. 검색 수행 시간 측정
    start_time = time.time()
    result = search_in_file(str(file_path), "target", use_complex_search=False)
    duration = time.time() - start_time

    print(f"\nSearch duration: {duration:.4f}s")

    # 3. 검증
    assert result is not None
    assert isinstance(result[1], int)
    # Rust 엔진은 이제 5,001건에서 캡핑하므로, 10,000개가 아닌 5,001개가 오거나 
    # Python 측 마커 포함 최대 5,001개가 와야 함
    assert result[1] >= 5001
    assert len(result[2]) <= 5001
    # 최적화 전에는 O(Matches * LineLength)로 수 초가 걸릴 수 있음
    # 최적화 후에는 0.1s 이내여야 함
    assert duration < 0.5, f"Performance regression detected: {duration:.4f}s"


def test_panic_regression_check(tmp_path):
    # 패닉 재현 테스트와 동일한 레벨의 검증 (안전성 유지 확인)
    xml_path = tmp_path / "panic_check.xml"
    chunk = '<?xml version="1.0" encoding="utf-16"?><Root>' + ("<Item>Data</Item>" * 10000)
    with open(xml_path, "wb") as f:
        f.write(b"\xff\xfe" + chunk.encode("utf-16-le") + "Target</Root>".encode("utf-16-le"))

    result = search_in_file(str(xml_path), "Target", use_complex_search=False)
    assert result is not None
