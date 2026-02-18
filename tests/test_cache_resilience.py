import os
import pytest
from core.search_cache import HybridSearchCache


def test_cache_corruption_recovery(temp_dir):
    """캐시 파일이 손상된(잘못된 JSON) 경우에도 크래시 없이 초기화되는지 확인"""
    cache_dir = os.path.join(temp_dir, "corrupt_test")
    os.makedirs(cache_dir, exist_ok=True)

    v3_path = os.path.join(cache_dir, "search_cache_v3.json")
    with open(v3_path, "w", encoding="utf-8") as f:
        f.write("{ invalid json content ...")

    # load_from_disk에서 예외를 잡아서 처리하므로 인스턴스 생성이 성공해야 함
    cache = HybridSearchCache(cache_dir, persist=True)
    assert len(cache.result_cache.cache) == 0
    assert len(cache.file_cache) == 0


def test_cache_lru_capacity_management(temp_dir):
    """파일 캐시가 2000개를 초과할 때 500개씩 정리되는 로직 검증"""
    cache = HybridSearchCache(temp_dir, persist=False)

    # 2000개까지 채움
    for i in range(2000):
        cache.file_cache[(f"file_{i}.txt", "query")] = (123.456, 100, 123.456, [], [])

    assert len(cache.file_cache) == 2000

    # 한 개 더 추가하여 트리거 발생 (로직상 2000개를 초과하면 500개를 pop)
    # [Fix] 실제 파일이 존재해야 os.stat 및 검색 로직이 작동함
    trigger_path = os.path.join(temp_dir, "trigger.txt")
    with open(trigger_path, "w") as f:
        f.write("trigger content")

    def mock_search(p, q):
        return []

    # 임계치 초과 트리거 (2000 + 1) - 500 = 1501
    cache._search_with_incremental("query", [trigger_path], mock_search)

    assert len(cache.file_cache) == 1501


def test_cache_key_generation():
    """검색 조건에 따른 고유 키 생성 일관성 확인"""
    cache = HybridSearchCache(".", persist=False)
    k1 = cache._get_cache_key("test", ["p1"], ["ext1"])
    k2 = cache._get_cache_key("test", ["p1"], ["ext1"])
    k3 = cache._get_cache_key("TEST", ["p1"], ["ext1"])

    assert k1 == k2
    # [v4.33.2 Update] Cache keys are normalized to lowercase for query
    assert k1 == k3  # 대소문자 비구분 (Normalized)

    # [v4.29.2] 모드 및 필터에 따른 고유성 검증
    k_mode1 = cache._get_cache_key("test", ["p1"], ["ext1"], special_mode="JSON")
    k_mode2 = cache._get_cache_key("test", ["p1"], ["ext1"], special_mode="XML")
    k_filter1 = cache._get_cache_key("test", ["p1"], ["ext1"], filename_filter="*.log")

    assert k1 != k_mode1
    assert k_mode1 != k_mode2
    assert k1 != k_filter1


def test_cache_clear_v3(temp_dir):
    """[중] 캐시 V3 통합 파일 삭제 결함 리그레션 테스트"""
    cache_dir = os.path.join(temp_dir, "clear_test")
    os.makedirs(cache_dir, exist_ok=True)

    cache = HybridSearchCache(cache_dir, persist=True)
    cache.result_cache.put("key", ["res"])
    cache.save_to_disk()

    v3_path = cache.cache_v3_path
    lock_path = cache.cache_lock_path

    assert os.path.exists(v3_path)

    # 캐시 삭제 수행
    assert cache.clear() is True

    # 파일이 실제로 제거되었는지 확인
    assert not os.path.exists(v3_path)
    assert not os.path.exists(lock_path)


def test_cache_empty_results(temp_dir):
    """[하] 0건 결과 미캐시 이슈 리그레션 테스트"""
    cache = HybridSearchCache(temp_dir, persist=False)
    key = "missing_query"

    # 결과가 0건인 경우에도 캐시에 저장되어야 함
    cache.result_cache.put(key, [])

    assert cache.result_cache.get(key) == []
    assert cache.result_cache.get(key) is not None


def test_cache_key_normalization_integrity(temp_dir):
    """[v4.33.2 Fix] Cache key normalization and schema integrity test"""
    cache = HybridSearchCache(temp_dir, persist=False)

    file_path = os.path.join(temp_dir, "test.txt")
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("content")

    query = "TestQuery"  # Mix case

    # 1. Simulate Worker Saving (Now using normalized query as per active fix)
    stat = os.stat(file_path)
    # [Fix Verification] Worker should store lower case key.
    # We manually store it as such to verify _file_changed behavior.
    worker_key = (file_path, query.lower())

    # Corrupted: 5 elements but wrong types or 3 elements (old schema simulation but validated by type checker?)
    # Mypy enforces us to use correct type, but runtime handles corruption.
    # To simulate corruption for type checker, we might use Any or ignore, but here we just test recovery.
    # Let's put a valid old-schema-like entry but force it to be invalid for v4 if strictly checked?
    # Actually, the test checks `_file_changed` robustness.
    cache.file_cache[worker_key] = (stat.st_mtime, stat.st_size, stat.st_ctime, [("res", 1, [])], [])

    # 2. Verify _file_changed Hit
    is_changed = cache._file_changed(file_path, query)
    assert is_changed is False, "Expected cache HIT after normalization fix"


def test_cache_metadata_schema_compatibility(temp_dir):
    """[v4.33.2 Fix] Metadata 5-element schema compatibility test"""
    cache = HybridSearchCache(temp_dir, persist=False)

    file_path = os.path.join(temp_dir, "test_meta.txt")
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("content")

    query = "query"

    # 1. Store 5-element schema
    stat = os.stat(file_path)
    cache.file_cache[(file_path, query)] = (stat.st_mtime, stat.st_size, stat.st_ctime, [("res", 1, [])], [])

    # 2. Verify _file_changed understands 5-element schema
    is_changed = cache._file_changed(file_path, query)
    assert is_changed is False, "Expected cache HIT with matching metadata schema"


def test_cache_persistence_v4(temp_dir):
    """[v4.33.2 Fix] Persistence with 5-element schema"""
    cache_dir = os.path.join(temp_dir, "persist_v4")
    os.makedirs(cache_dir, exist_ok=True)
    cache = HybridSearchCache(cache_dir, persist=True)

    file_path = os.path.join(cache_dir, "file.txt")
    query = "persist_query"

    # Store 5-element data
    key = ("test_file", "query")
    # v4 Schema: (mtime, size, ctime, results, skipped)
    cache.file_cache[key] = (100.0, 50, 100.0, [("res", 1, [])], [])

    # 2. Add an entry for a non-existent file (Pollution)
    fake_path = os.path.join(cache_dir, "ghost.txt")
    cache.file_cache[(fake_path, query)] = (1.0, 100, 2.0, [], [])

    # Also add a valid entry that actually exists
    with open(file_path, "w") as f:
        f.write("content")

    real_key = (file_path, query)
    cache.file_cache[real_key] = (os.stat(file_path).st_mtime, 7, os.stat(file_path).st_ctime, [], [])

    # Should NOT raise ValueError
    try:
        cache.save_to_disk()
    except Exception as e:
        pytest.fail(f"Save failed with error: {e}")

    # Verify Load
    cache2 = HybridSearchCache(cache_dir, persist=True)

    # [External Review Fix Verification]
    # The "ghost.txt" entry should be removed because the file does not exist.
    # "file.txt" should remain.
    # "test_file" (from variable key) is just a tuple string, passed as key?
    # Wait, key was ("test_file", "query"). "test_file" likely doesn't exist either.
    # So both "ghost.txt" and "test_file" should be removed.
    # Only "file.txt" (real_path) should remain.

    assert len(cache2.file_cache) == 1
    assert real_key in cache2.file_cache

    val = cache2.file_cache[real_key]
    assert len(val) == 5
