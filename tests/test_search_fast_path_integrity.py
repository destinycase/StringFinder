"""Safe shortcuts preserve candidates, values, positions, skips and final batches."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap
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
    # 잘못된 UTF-8 바이트(ÿ)는 일치가 아니라 파일 단위 "일부 해석 못 함" 표시(line 0)로만 남습니다.
    assert [list(match)[:2] for match in matches if match.line != 0] == [[2, "invalid_utf8"]]
    assert [match.code for match in matches if match.line == 0] == ["DECODE_PARTIAL"]


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


@pytest.mark.parametrize("api", ["search_dir", "search_files_list"])
@pytest.mark.parametrize("hit", [True, False], ids=["hit", "no_match"])
def test_dispatcher_shutdown_does_not_wait_for_flush_interval(tmp_path, api, hit):
    """A leaked sender must not restore the 1s idle wait or hang pytest."""
    native = engine.sf_engine
    if native is None:
        pytest.skip("real native engine required")
    if hit:
        # Leave work after the early result so the dispatcher must wake from idle.
        (tmp_path / "000_hit.txt").write_text("needle\nother\n", encoding="utf-8")
        for index in range(300):
            (tmp_path / f"noise_{index:03d}.txt").write_text(f"other {index}\n", encoding="utf-8")
    else:
        for index in range(3):
            (tmp_path / f"sample_{index}.txt").write_text(f"needle {index}\nother\n", encoding="utf-8")
    child_code = textwrap.dedent("""
        import faulthandler
        import importlib.util
        import json
        from pathlib import Path
        import sys
        import time

        faulthandler.dump_traceback_later(12)
        engine_path, directory, api, query = sys.argv[1:]
        spec = importlib.util.spec_from_file_location("shutdown_check.sf_engine", engine_path)
        native = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(native)
        search = getattr(native, api)
        root = Path(directory)
        files = sorted(root.glob("*.txt"))
        if query == "needle":
            hit_path = root / "000_hit.txt"
            files = [hit_path] + [path for path in files if path != hit_path]
        paths = [str(root)] if api == "search_dir" else [str(path) for path in files]

        def values(rows):
            return sorted((path, [(m.line, m.content, m.offset, m.length, m.kind)
                                  for m in matches]) for path, matches in rows)

        expected, skipped = search(paths, query)
        assert skipped == [], skipped
        assert len(expected) == (1 if query == "needle" else 0), expected
        assert sum(len(matches) for _, matches in expected) == (1 if query == "needle" else 0), expected
        expected = values(expected)
        samples = []
        for iteration in range(6):  # One warmup, then five measured calls.
            received = []
            started = time.perf_counter()
            remaining, skipped = search(paths, query, results_callback=received.extend, _flush_ms=1000)
            elapsed = time.perf_counter() - started
            assert remaining == [] and skipped == [], (remaining, skipped)
            assert values(received) == expected, values(received)
            if iteration:
                samples.append(elapsed)
        faulthandler.cancel_dump_traceback_later()
        print(json.dumps({"api": api, "query": query, "engine_version": native.ENGINE_VERSION,
                          "samples_ms": [round(value * 1000, 3) for value in samples],
                          "minimum_ms": round(min(samples) * 1000, 3)}), flush=True)
        assert min(samples) < 0.5, f"Dispatcher shutdown waited for flush timeout: {samples}"
    """)
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    try:
        result = subprocess.run(
            [sys.executable, "-c", child_code, str(Path(native.__file__).resolve()),
             str(tmp_path), api, "needle" if hit else "absent"],
            cwd=tmp_path, env=env, capture_output=True, encoding="utf-8", errors="replace", timeout=20,
        )
    except subprocess.TimeoutExpired as error:
        pytest.fail(f"Dispatcher shutdown timed out ({api}, hit={hit}): {error.stdout!r}\n{error.stderr!r}")
    print(result.stdout, end="")
    assert result.returncode == 0, result.stdout + result.stderr
