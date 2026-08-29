import time
from concurrent.futures import ProcessPoolExecutor
from unittest.mock import MagicMock, patch

from core.worker import GlobalExecutor


def _block_briefly_for_executor_test():
    time.sleep(30)


class FakeProcess:
    def __init__(self):
        self.alive = True
        self.terminate_called = False
        self.kill_called = False
        self.join_calls = []

    def is_alive(self):
        return self.alive

    def terminate(self):
        self.terminate_called = True
        self.alive = False

    def kill(self):
        self.kill_called = True
        self.alive = False

    def join(self, timeout=None):
        self.join_calls.append(timeout)


def test_shutdown_executor_reaps_workers_after_forced_shutdown():
    process = FakeProcess()
    executor = MagicMock()
    executor._processes = {1: process}

    GlobalExecutor.shutdown_executor(executor, terminate=True)

    executor.shutdown.assert_called_once_with(wait=False, cancel_futures=True)
    assert process.terminate_called is True
    assert process.kill_called is False
    assert process.join_calls == [2.0]


def test_shutdown_executor_keeps_normal_shutdown_graceful():
    process = FakeProcess()
    executor = MagicMock()
    executor._processes = {1: process}

    GlobalExecutor.shutdown_executor(executor, wait=True, terminate=False)

    executor.shutdown.assert_called_once_with(wait=True, cancel_futures=True)
    assert process.terminate_called is False
    assert process.join_calls == []


def test_concurrent_owners_do_not_share_an_executor(monkeypatch):
    executor_one = MagicMock()
    executor_two = MagicMock()
    owner_one = object()
    owner_two = object()
    monkeypatch.setattr(GlobalExecutor, "_executor", None)
    monkeypatch.setattr(GlobalExecutor, "_is_active", False)
    monkeypatch.setattr(GlobalExecutor, "_owner", None)
    monkeypatch.setattr(GlobalExecutor, "_additional_executors", {})

    with patch("core.worker.ProcessPoolExecutor", side_effect=[executor_one, executor_two]):
        assert GlobalExecutor.get_executor(owner=owner_one) is executor_one
        assert GlobalExecutor.get_executor(owner=owner_two) is executor_two

    GlobalExecutor.release(executor_one, owner=owner_one)
    assert GlobalExecutor._executor is executor_one
    assert GlobalExecutor._additional_executors[id(executor_two)][1] is owner_two
    GlobalExecutor.release(executor_two, owner=owner_two)
    assert GlobalExecutor._owner is None


def test_shutdown_executor_terminates_real_pool_worker():
    executor = ProcessPoolExecutor(max_workers=1)
    shutdown_called = False
    try:
        executor.submit(_block_briefly_for_executor_test)
        deadline = time.monotonic() + 5.0
        while not getattr(executor, "_processes", None) and time.monotonic() < deadline:
            time.sleep(0.01)

        processes = list((executor._processes or {}).values())
        assert processes
        GlobalExecutor.shutdown_executor(executor, terminate=True)
        shutdown_called = True

        assert all(not process.is_alive() for process in processes)
    finally:
        if not shutdown_called:
            executor.shutdown(wait=False, cancel_futures=True)
