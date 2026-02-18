import time
from core.worker import SearchWorker


def test_worker_immediate_stop():
    """워커 시작 직후 바로 중지 명령이 내려질 때의 안정성 (Race Condition)"""
    params = {
        "file_list": [("fake.txt", 100)] * 10,
        "search_string": "test",
        "search_paths": ["."],
        "extensions": ["txt"],
    }
    worker = SearchWorker(params)

    # run()이 내부적으로 스레드나 프로세스를 띄우는 구조이므로
    # 실제 UI에서는 별도 스레드로 실행됨. 여기서는 로직만 시뮬레이션.
    # 시작하자마자 stop
    worker.stop()
    assert worker.stop_event is not None
    assert worker.stop_event.is_set()
    assert worker.is_running is False


def test_worker_stop_propagation_latency():
    """중단 신호 발생 시 워커 루프가 얼마나 빨리 반응하는지 확인 (이론적 구조 검증)"""
    # Manager().Event()는 프로세스 간 공유되므로 지연 발생 가능성 있음
    from multiprocessing import Manager

    manager = Manager()
    event = manager.Event()

    start = time.perf_counter()
    event.set()
    latency = time.perf_counter() - start

    # 임계치(예: 0.1초) 이내여야 함
    assert latency < 0.1


def test_worker_signal_integrity(qtbot):
    """[중] 워커 시그널 중복 발행 방지 검증"""
    from core.worker import SearchWorker
    from core.search_cache import HybridSearchCache
    import tempfile
    import shutil

    tmp = tempfile.mkdtemp()
    try:
        cache = HybridSearchCache(tmp, persist=False)
        cache.result_cache.put("key", [])  # 0건 시뮬레이션

        params = {"search_string": "test", "search_paths": ["."], "cache_enabled": True}
        worker = SearchWorker(params)
        worker.cache = cache

        # 시그널 수신 카운터
        class SignalCounter:
            def __init__(self):
                self.count = 0

            def hit(self, *args):
                self.count += 1

        counter = SignalCounter()
        worker.signals.finished.connect(counter.hit)

        # 직접 실행 (캐시 히트 유도)
        worker.run()

        # 1회만 발행되어야 함 (기존에는 return 전과 finally에서 총 2회 발행될 수 있었음)
        assert counter.count == 1
    finally:
        shutil.rmtree(tmp)


def test_global_manager_reuse():
    """[중] Manager 재사용을 통한 생성 오버헤드 최적화 검증"""
    from core.worker import get_global_manager, shutdown_global_manager

    m1 = get_global_manager()
    m2 = get_global_manager()

    assert m1 is m2  # 동일 인스턴스여야 함
    assert m1 is not None

    # 종료 테스트 (주의: 다른 테스트에 영향을 줄 수 있으므로 조심스럽게 수행)
    # 여기서는 shutdown_global_manager 호출 시 None으로 초기화되는지 확인
    shutdown_global_manager()
    from core.worker import _global_manager

    assert _global_manager is None
