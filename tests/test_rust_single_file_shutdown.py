"""Bound native shutdown regressions so a GIL deadlock cannot hang pytest."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


@pytest.mark.parametrize("case", ["match", "no_match", "parse_error", "cancelled"])
def test_single_file_monitor_shutdown(tmp_path, case):
    pytest.importorskip("rust_engine.sf_engine")
    path = tmp_path / ("sample.json" if case == "parse_error" else "sample.txt")
    path.write_text('{ malformed ' if case == "parse_error" else "needle\nother\n", encoding="utf-8")
    child_code = textwrap.dedent("""
        import faulthandler
        import sys
        import threading
        from rust_engine import sf_engine
        from sf_utils.constants import Constants

        def values(matches):
            return [(m.line, m.content, m.offset, m.length, m.kind) for m in matches]

        faulthandler.dump_traceback_later(8)
        path, case = sys.argv[1:]
        for iteration in range(100):
            event = threading.Event()
            if case == "cancelled":
                event.set()
            print(f"{case}: {iteration}", flush=True)
            if case == "parse_error":
                try:
                    sf_engine.search_file(path, "needle", Constants.RUST_MODE_JSON, event)
                except RuntimeError as error:
                    assert "ERR_JSON_PARSE" in str(error), str(error)
                else:
                    raise AssertionError("Malformed JSON must report a parse error")
            else:
                pattern = "absent" if case == "no_match" else "needle"
                expected = values(sf_engine.search_file(path, pattern))
                actual = values(sf_engine.search_file(path, pattern, stop_event=event))
                # A tiny search may finish before the monitor observes cancellation.
                if case == "cancelled":
                    assert actual == [] or actual == expected, (actual, expected)
                else:
                    assert actual == expected, (actual, expected)
                    assert len(actual) == (0 if case == "no_match" else 1)
        faulthandler.cancel_dump_traceback_later()
        print("DONE", flush=True)
    """)
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(Path(__file__).resolve().parents[1] / "src"), env.get("PYTHONPATH")])
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", child_code, str(path), case],
            env=env, capture_output=True, text=True, timeout=15,
        )
    except subprocess.TimeoutExpired as error:
        pytest.fail(f"Single-file shutdown timed out ({case}): {error.stdout!r}\n{error.stderr!r}")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DONE" in result.stdout
