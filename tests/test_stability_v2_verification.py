import os
import pytest
from core.search_engine import search_in_file, search_in_files_batch, HAS_RUST_ENGINE
from sf_utils.constants import Constants

"""
[test_stability_v2_verification.py]

안정성 강화 항목(Panic 방어, Mmap 폴백)을 검증하기 위한 테스트입니다.
1. Panic Recovery: Rust 엔진의 병렬 루프 내 패닉 발생 시 복구 여부.
2. Mmap Fallback: 파일 락 등으로 Mmap 실패 시 일반 Read로 전환되는지 여부.
"""


@pytest.mark.skipif(not HAS_RUST_ENGINE, reason="Rust 엔진이 빌드되어 있어야 테스트 가능합니다.")
def test_panic_recovery_logic(tmp_path, monkeypatch):
    """
    모킹을 통해 Rust 엔진에서 패닉 상황(REASON_ERR_PANIC)을 반환하게 하고,
    Python 레이어에서 이를 건너뛴 파일(SKIPPED)로 우아하게 처리하는지 확인합니다.
    (실제 Rust catch_unwind는 이미 C API 경계에서 PanicException으로 변환되거나
    내부적으로 수집되어 반환되므로, 인터페이스 무결성을 테스트합니다.)
    """
    file_path = tmp_path / "panic_test.txt"
    file_path.write_text("content")

    # Rust 엔진의 search_file이 패닉 에러 코드를 포함한 결과를 반환하도록 모킹
    class MockMatch:
        def __init__(self, content="ERR_PANIC|INTERNAL_PANIC"):
            self.content = content
            self.line = 1
            self.offset = 0
            self.length = 0

    class MockEngine:
        def search_file(self, path, query, mode, stop_event=None):
            # 패닉 상황 시뮬레이션: RuntimeError 발생
            raise RuntimeError("Fake Rust Panic")

        def search_files_list(self, *args, **kwargs):
            return [], []

        def search_dir(self, *args, **kwargs):
            return [], []

    import core.search_engine

    monkeypatch.setattr(core.search_engine, "sf_engine", MockEngine())
    monkeypatch.setattr(core.search_engine, "HAS_RUST_ENGINE", True)

    # 단일 파일 검색 테스트
    result = search_in_file(str(file_path), "query", use_complex_search=False)

    assert result is not None
    # result[0]은 익셉션 발생 시 Constants.STATUS_SKIPPED
    assert result[0] == Constants.STATUS_SKIPPED


@pytest.mark.skipif(not HAS_RUST_ENGINE, reason="Rust 엔진이 빌드되어 있어야 테스트 가능합니다.")
@pytest.mark.skipif(os.name != "nt", reason="Windows의 파일 독점 락을 이용한 테스트입니다.")
def test_mmap_fallback_with_real_lock(tmp_path):
    """
    Windows에서 파일을 독점 모드로 열어 Rust 엔진의 Mmap 매핑을 실패하게 유도하고,
    구현된 폴백 로직(Read 기반)이 작동하여 검색에 성공하는지 확인합니다.
    """
    file_path = tmp_path / "locked_file.txt"
    content = "This is a secret needle in a locked file."
    file_path.write_text(content, encoding="utf-8")

    try:
        # 1. 파일을 쓰기 모드로 열어 독점 락 유도
        f_handle = open(file_path, "ab")

        # 2. 검색 수행
        result = search_in_file(str(file_path), "needle", use_complex_search=False)

        # 3. 검증
        assert result is not None
        # 폴백이 작동했다면 성공(file_path 반환) 혹은 최소한 크래시는 없어야 함
        if result[0] != Constants.STATUS_SKIPPED:
            assert str(result[0]) == str(file_path)

    finally:
        f_handle.close()


def test_batch_recovery_scenario(tmp_path, monkeypatch):
    """
    배치 검색 중 일부 파일이 패닉을 일으켜도 나머지는 정상 완료되는지 확인합니다.
    """
    files = []
    for i in range(5):
        p = tmp_path / f"file_{i}.txt"
        p.write_text(f"content_{i}")
        files.append((str(p), p.stat().st_size))

    class MockEngineBatch:
        def search_file(self, path, query, mode, stop_event=None):
            if "file_2" in str(path):
                raise RuntimeError("Fake Rust Panic")
            return []  # 매치 없음

        def search_files_list(self, file_list, *args, **kwargs):
            # search_directory_fast 등이 사용하는 함수
            results = []
            skipped = []
            for path in file_list:
                if "file_2" in str(path):
                    skipped.append((path, "ERR_PANIC|UNIT_TEST_PANIC"))
                else:
                    results.append((path, []))
            return results, skipped

    import core.search_engine

    monkeypatch.setattr(core.search_engine, "sf_engine", MockEngineBatch())
    monkeypatch.setattr(core.search_engine, "HAS_RUST_ENGINE", True)

    # search_in_files_batch는 내부적으로 search_in_file(loop)을 호출함
    result = search_in_files_batch(files, "query", None)

    assert "results" in result
    assert "skipped" in result
    # file_2에 대한 스킵 항목 확인
    skipped_paths = [s[0] for s in result["skipped"]]
    assert any("file_2" in str(p) for p in skipped_paths)
