import pytest
import os
import tempfile
import shutil
import time
from core.worker import SearchWorker
from core.search_cache import HybridSearchCache


@pytest.fixture
def temp_workspace():
    path = tempfile.mkdtemp(prefix="test_cache_zero_")
    yield path
    shutil.rmtree(path)


def test_zero_result_cache_invalidation_on_file_addition(temp_workspace):
    """
    [Phase 7 Fix 검증]
    1. 검색 결과 0건 -> 캐시 저장
    2. 파일 추가 (검색 대상 폴더 mtime 변경)
    3. 재검색 시 캐시 무효화 및 새 결과 발견 확인
    """
    cache_dir = os.path.join(temp_workspace, "cache")
    cache = HybridSearchCache(cache_dir, persist=False)

    search_string = "TARGET_STRING"
    params = {
        "search_paths": [temp_workspace],
        "search_string": search_string,
        "extensions": ["txt"],
        "cache_enabled": True,
    }

    # --- 1. 첫 번째 검색 (비어있는 폴더) ---
    worker1 = SearchWorker(params)
    worker1.cache = cache
    worker1.run()

    assert len(worker1.all_results) == 0
    # 캐시에 0건 결과와 폴더 메타데이터가 저장되어야 함

    # --- 2. 파일 추가 (폴더 mtime 변경 유도) ---
    # Windows 등에서 mtime 해상도 문제로 인해 약간의 대기 필요할 수 있음
    time.sleep(0.1)
    new_file = os.path.join(temp_workspace, "match.txt")
    with open(new_file, "w", encoding="utf-8") as f:
        f.write(f"This file contains {search_string}")

    # --- 3. 두 번째 검색 (같은 조건) ---
    worker2 = SearchWorker(params)
    worker2.cache = cache
    worker2.run()

    # [v4.29.5 Fix 전] stale 캐시(0건) 히트로 인해 실패 예상
    # [v4.29.5 Fix 후] 폴더 mtime 변경 감지로 재검색 수행 -> 1건 발견 성공
    assert len(worker2.all_results) == 1
    assert worker2.all_results[0][0] == new_file


def test_zero_result_cache_consistency_no_change(temp_workspace):
    """변경 사항이 없을 때는 0건 캐시 히트가 유지되어야 함 (성능)"""
    cache_dir = os.path.join(temp_workspace, "cache")
    cache = HybridSearchCache(cache_dir, persist=False)

    search_string = "NON_EXISTENT"
    params = {
        "search_paths": [temp_workspace],
        "search_string": search_string,
        "extensions": ["txt"],
        "cache_enabled": True,
    }

    # 1차 검색
    worker1 = SearchWorker(params)
    worker1.cache = cache
    worker1.run()

    # 2차 검색 (변경 없음)
    worker2 = SearchWorker(params)
    worker2.cache = cache

    # 히트 로그 확인 대신 결과로 판단 (캐시 히트 시 속도가 훨씬 빠름)
    worker2.run()
    assert len(worker2.all_results) == 0
    # 히트 카운터 확인 (LRUCache 접근성 필요)
    assert cache.result_cache.hits == 1
