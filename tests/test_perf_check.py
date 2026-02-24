import time
import pytest
from core.search_engine import search_in_file, HAS_RUST_ENGINE


@pytest.mark.skipif(not HAS_RUST_ENGINE, reason="Rust 엔진 필요")
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
    assert result[1] > 0
    # 최적화 전에는 O(Matches * LineLength)로 수 초가 걸릴 수 있음
    # 최적화 후에는 0.1s 이내여야 함
    assert duration < 0.5, f"Performance regression detected: {duration:.4f}s"


@pytest.mark.skipif(not HAS_RUST_ENGINE, reason="Rust 엔진 필요")
def test_panic_regression_check(tmp_path):
    # 패닉 재현 테스트와 동일한 레벨의 검증 (안전성 유지 확인)
    xml_path = tmp_path / "panic_check.xml"
    chunk = '<?xml version="1.0" encoding="utf-16"?><Root>' + ("<Item>Data</Item>" * 10000)
    with open(xml_path, "wb") as f:
        f.write(b"\xff\xfe" + chunk.encode("utf-16-le") + "Target</Root>".encode("utf-16-le"))

    result = search_in_file(str(xml_path), "Target", use_complex_search=False)
    assert result is not None
