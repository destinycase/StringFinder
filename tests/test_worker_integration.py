import pytest

from core.worker import GlobalExecutor, SearchWorker
from sf_utils.constants import Constants


def _make_files(tmp_path, count=8):
    files = []
    for index in range(count):
        path = tmp_path / f"worker_{index}.txt"
        path.write_text("deployment smoke needle\n", encoding="utf-8")
        files.append((str(path), path.stat().st_size))
    return files


@pytest.mark.integration
def test_real_worker_completes_and_reuses_pool(tmp_path):
    files = _make_files(tmp_path)
    try:
        results = []
        finished = []
        first = SearchWorker(
            {
                Constants.PAYLOAD_FILE_LIST: files,
                Constants.PAYLOAD_SEARCH_STRING: "needle",
                Constants.PAYLOAD_USE_COMPLEX_SEARCH: True,
            }
        )
        first.signals.results_found.connect(results.append)
        first.signals.search_finished.connect(lambda *args: finished.append(args))
        first.run()
        assert finished[-1] == (8, 8, 0)

        second = SearchWorker(
            {
                Constants.PAYLOAD_FILE_LIST: files,
                Constants.PAYLOAD_SEARCH_STRING: "needle",
                Constants.PAYLOAD_USE_COMPLEX_SEARCH: True,
            }
        )
        second_finished = []
        second.signals.search_finished.connect(lambda *args: second_finished.append(args))
        second.run()
        assert second_finished[-1] == (8, 8, 0)
        assert sum(len(batch) for batch in results) == 8
    finally:
        GlobalExecutor.shutdown(wait=True, cancel_futures=True)


@pytest.mark.integration
def test_real_worker_stop_then_search_again(tmp_path, monkeypatch):
    files = _make_files(tmp_path, count=16)
    monkeypatch.setattr(Constants, "BATCH_SIZE_NORMAL", 1)

    try:
        first = SearchWorker(
            {
                Constants.PAYLOAD_FILE_LIST: files,
                Constants.PAYLOAD_SEARCH_STRING: "needle",
                Constants.PAYLOAD_USE_COMPLEX_SEARCH: True,
            }
        )
        first.signals.results_found.connect(lambda _results: first.stop())
        first.run()
        assert not first.is_running.is_set()
        assert first._executor is None

        second = SearchWorker(
            {
                Constants.PAYLOAD_FILE_LIST: files,
                Constants.PAYLOAD_SEARCH_STRING: "needle",
                Constants.PAYLOAD_USE_COMPLEX_SEARCH: True,
            }
        )
        finished = []
        second.signals.search_finished.connect(lambda *args: finished.append(args))
        second.run()
        assert finished[-1] == (16, 16, 0)
    finally:
        GlobalExecutor.shutdown(wait=True, cancel_futures=True)


@pytest.mark.integration
def test_real_worker_timeout_then_search_again(tmp_path, monkeypatch):
    files = _make_files(tmp_path)
    timeout_key = Constants.CONFIG_KEY_TIMEOUT_WORKER_HANG

    def immediate_timeout(key, default):
        return 0 if key == timeout_key else default

    try:
        first = SearchWorker(
            {
                Constants.PAYLOAD_FILE_LIST: files,
                Constants.PAYLOAD_SEARCH_STRING: "needle",
                Constants.PAYLOAD_USE_COMPLEX_SEARCH: True,
            }
        )
        errors = []
        first.signals.error.connect(errors.append)
        monkeypatch.setattr("core.worker._get_adv_setting", immediate_timeout)
        first.run()
        assert errors and "Timeout" in errors[0]
        assert first._executor is None

        monkeypatch.undo()
        second = SearchWorker(
            {
                Constants.PAYLOAD_FILE_LIST: files,
                Constants.PAYLOAD_SEARCH_STRING: "needle",
                Constants.PAYLOAD_USE_COMPLEX_SEARCH: True,
            }
        )
        finished = []
        second.signals.search_finished.connect(lambda *args: finished.append(args))
        second.run()
        assert finished[-1] == (8, 8, 0)
    finally:
        GlobalExecutor.shutdown(wait=True, cancel_futures=True)
