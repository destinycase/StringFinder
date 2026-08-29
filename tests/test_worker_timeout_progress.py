from concurrent.futures import Future
from unittest.mock import MagicMock, patch

from core.worker import SearchWorker
from sf_utils.constants import Constants


def test_worker_timeout_is_reset_when_batches_complete(monkeypatch):
    worker = SearchWorker(
        {
            Constants.PAYLOAD_FILE_LIST: [("one.txt", 1), ("two.txt", 1), ("three.txt", 1)],
            Constants.PAYLOAD_SEARCH_STRING: "needle",
            Constants.PAYLOAD_USE_COMPLEX_SEARCH: True,
        }
    )
    errors = []
    worker.signals.error.connect(errors.append)
    executor = MagicMock()
    executor._max_workers = 1

    def submit_batch(*_args, **_kwargs):
        future = MagicMock(spec=Future)
        future.result.return_value = {Constants.PAYLOAD_RESULTS: [], Constants.PAYLOAD_SKIPPED: []}
        return future

    def wait_for_batch(pending, **_kwargs):
        return {next(iter(pending))}, set()

    monkeypatch.setattr(Constants, "BATCH_SIZE_NORMAL", 1)

    with (
        patch("core.worker.get_global_manager", return_value=None),
        patch("core.worker.GlobalExecutor.get_executor", return_value=executor),
        patch("core.worker._get_adv_setting", return_value=0.15),
        patch("core.worker.wait", side_effect=wait_for_batch),
        patch(
            "core.worker.time.monotonic",
            side_effect=[0.00, 0.01, 0.02, 0.10, 0.11, 0.12, 0.20, 0.21, 0.22, 0.30],
        ),
    ):
        worker._run_batch_search(worker.file_list, force_python=True)

    assert errors == []
    assert executor.submit.call_count == 3
