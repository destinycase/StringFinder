import os
import time
import json
import pytest
from core.search_cache import HybridSearchCache
from core.worker import SearchWorker


def test_cache_unification_and_migration(temp_dir):
    """캐시 파일 통합 및 기존 파일 제거 검증"""
    cache_dir = os.path.join(temp_dir, "cache_v3_test")
    os.makedirs(cache_dir, exist_ok=True)

    # 구버전 파일 생성
    old_files = ["result_cache.json", "file_cache.json"]
    for f in old_files:
        with open(os.path.join(cache_dir, f), "w") as fw:
            fw.write("{}")

    cache = HybridSearchCache(cache_dir, persist=True)

    # 1. V3 파일 경로 확인
    assert hasattr(cache, "cache_v3_path")
    assert "search_cache_v3.json" in cache.cache_v3_path

    # 2. 데이터 저장 후 V3 파일 생성 확인
    cache.result_cache.put("test_key", "test_value")
    cache.save_to_disk()
    assert os.path.exists(cache.cache_v3_path)

    # 3. 구버전 파일 제거 확인 (load_from_disk 내부에서 수행됨)
    # load_from_disk는 __init__에서 호출되지만, V3가 이미 생성된 후 다시 로드하면 제거됨
    cache.load_from_disk()
    for f in old_files:
        assert not os.path.exists(os.path.join(cache_dir, f))

    # 4. 내용 검증
    with open(cache.cache_v3_path, "r", encoding="utf-8") as f_ver:
        data = json.load(f_ver)
        # [v4.33.2 Update] Cache version bumped to 4 (5-element schema)
        assert data["version"] == "4"
        assert "result_cache" in data
        assert "file_cache" in data


def test_cache_locking_concurrency(temp_dir):
    """캐시 락을 통한 동시성 제어 검증 (크래시 여부)"""
    cache_dir = os.path.join(temp_dir, "cache_lock_test")
    cache = HybridSearchCache(cache_dir, persist=True)

    def worker_save():
        for _ in range(10):
            cache.result_cache.put(f"key_{_}", "value")
            cache.save_to_disk()
            time.sleep(0.01)

    def worker_load():
        for _ in range(10):
            cache.load_from_disk()
            time.sleep(0.01)

    # 스레드로 시뮬레이션 (Windows msvcrt 락은 프로세스 간 락이지만
    # 같은 프로세스 내에서도 핸들이 다르면 동작 확인 가능)
    import threading

    t1 = threading.Thread(target=worker_save)
    t2 = threading.Thread(target=worker_load)

    t1.start()
    t2.start()
    t1.join()
    t2.join()
    # 크래시 없이 끝나면 통과


def test_worker_stop_event_signal():
    """워커 중단 시 stop_event가 제대로 전달되고 설정되는지 확인"""
    params = {"file_list": [("test.txt", 100)], "search_string": "test", "search_paths": ["."], "extensions": ["txt"]}
    worker = SearchWorker(params)

    assert hasattr(worker, "stop_event")
    assert hasattr(worker, "stop_event")
    assert worker.stop_event is not None
    assert not worker.stop_event.is_set()

    worker.stop()
    assert worker.stop_event is not None
    assert worker.stop_event.is_set()
    assert worker.is_running is False


if __name__ == "__main__":
    pytest.main([__file__])
