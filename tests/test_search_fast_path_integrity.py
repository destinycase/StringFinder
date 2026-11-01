"""Safe shortcuts preserve candidates, values, positions, skips and final batches."""
import threading
import time

import pytest

from core import search_engine as engine
from sf_utils.constants import Constants as C


@pytest.mark.parametrize("api", ["file", "directory", "file_list"])
def test_invalid_utf8_line_cannot_match_internal_sentinel(tmp_path, api):
    path = tmp_path / "broken.txt"
    path.write_bytes(b"\xef\xbb\xbfvalid\ninvalid_utf8\n\xff\n")
    native = engine.sf_engine
    if native is None:
        pytest.skip("real native engine required")
    if api == "file":
        matches = native.search_file(str(path), "invalid_utf8", mode_bits=C.RUST_MODE_EXACT)
    else:
        rows, skipped = (native.search_dir if api == "directory" else native.search_files_list)(
            [str(tmp_path if api == "directory" else path)], "invalid_utf8", mode_bits=C.RUST_MODE_EXACT)
        assert skipped == []
        assert len(rows) == 1
        matches = rows[0][1]
    assert [list(match)[:2] for match in matches] == [[2, "invalid_utf8"]]


@pytest.mark.parametrize("api", ["directory", "file_list"])
@pytest.mark.parametrize("cancel", [False, True])
def test_dispatcher_drains_bounded_queue_and_last_batch(tmp_path, api, cancel):
    native = engine.sf_engine
    if native is None:
        pytest.skip("real native engine required")
    for index in range(41):
        (tmp_path / f"{index}.txt").write_text(f"needle {index}", encoding="utf-8")
    paths = [str(tmp_path)] if api == "directory" else [str(p) for p in tmp_path.iterdir()]
    search = native.search_dir if api == "directory" else native.search_files_list
    expected, skipped = search(paths, "needle")
    assert not skipped
    event = threading.Event()
    batches = []
    first_batch_size = 0

    def collect(batch):
        nonlocal first_batch_size
        first = not batches
        batches.extend(batch)
        if first:
            first_batch_size = len(batch)
        if cancel and first:
            time.sleep(0.15)  # Fill the queue while the first callback is paused.
            event.set()
            time.sleep(0.15)  # Allow the monitor to observe cancellation.
        time.sleep(0.002)  # Let producers fill the bounded queue.

    final, skipped = search(paths, "needle", results_callback=collect,
                            stop_event=event, batch_size=3, _flush_ms=1000)
    assert final == skipped == []
    assert batches
    assert len({path for path, _ in batches}) == len(batches)

    def signatures(rows):
        return sorted((p, [list(m) for m in matches]) for p, matches in rows)

    if not cancel:
        assert signatures(batches) == signatures(expected)
    else:
        assert first_batch_size < len(batches) < len(expected)
        assert all(row in signatures(expected) for row in signatures(batches))


@pytest.mark.parametrize("api", ["directory", "file_list"])
def test_dispatcher_disconnect_preserves_callback_error(tmp_path, api):
    native = engine.sf_engine
    if native is None:
        pytest.skip("real native engine required")
    path = tmp_path / "sample.txt"
    path.write_text("needle", encoding="utf-8")

    def broken_callback(batch):
        raise ValueError("callback probe")

    search = native.search_dir if api == "directory" else native.search_files_list
    with pytest.raises(RuntimeError, match="callback probe"):
        search([str(tmp_path if api == "directory" else path)], "needle",
               results_callback=broken_callback, _flush_ms=1000)
