"""
[test_chaos_io_edge.py]

이 테스트는 파일 I/O 작업 도중 발생하는 물리적 예외 상황에서의 시스템 안정성을 검증합니다.

- 테스트 목적:
  1. 파일 읽기 중 권한 박탈, 파일 삭제 등 레이스 컨디션 상황에서의 예외 처리 로직 확인.
  2. 급격한 I/O 가로채기 상황에서도 프로세스가 패닉(Panic) 없이 우아하게 종료되는지 검증.

- 주요 검증 사항:
  1. 파일 로드 도중 외부 스레드에서의 권한 변경(`chmod 000`) 시나리오 대응.
  2. 다량의 파일 검색 배치 도중 실시간 파일 삭제 시나리오 대응.
"""

import os
import stat
import threading
import time

import pytest

from core.search_engine import search_in_files_batch


@pytest.mark.chaos
def test_chaos_permission_flip_flop(tmp_path):
    target_file = tmp_path / "chaos.txt"
    target_file.write_bytes(b"a" * 4 * 1024 * 1024)

    stop_event = threading.Event()

    def chaos_monkey():
        while not stop_event.is_set():
            try:
                os.chmod(target_file, 0)
                time.sleep(0.001)
                os.chmod(target_file, stat.S_IREAD | stat.S_IWRITE)
                time.sleep(0.001)
            except OSError:
                pass

    thread = threading.Thread(target=chaos_monkey, daemon=True)
    thread.start()

    try:
        for _ in range(8):
            size = target_file.stat().st_size
            result = search_in_files_batch([(str(target_file), size)], "needle", None)
            assert "results" in result
            assert "skipped" in result
    finally:
        stop_event.set()
        thread.join(timeout=5.0)
        try:
            os.chmod(target_file, stat.S_IREAD | stat.S_IWRITE)
        except OSError:
            pass


@pytest.mark.chaos
def test_chaos_mid_read_deletion(tmp_path):
    files = []
    for i in range(120):
        path = tmp_path / f"del_{i}.txt"
        path.write_text("content", encoding="utf-8")
        files.append((str(path), path.stat().st_size))

    def deleter():
        time.sleep(0.01)
        for p, _ in files:
            try:
                os.remove(p)
            except OSError:
                pass

    thread = threading.Thread(target=deleter, daemon=True)
    thread.start()
    try:
        result = search_in_files_batch(files, "content", None)
        assert "results" in result
        assert "skipped" in result
    finally:
        thread.join(timeout=5.0)
