"""Measure deep XML and newline-free UTF-16 without changing search limits.

Run: python tools/benchmark_review_edges.py
Timings exclude fixture generation; each case has one warmup and three samples.
"""

import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rust_engine import sf_engine  # noqa: E402
from sf_utils.constants import Constants  # noqa: E402


def measure(path, mode, cancel=False):
    samples = []
    for index in range(4):
        event = threading.Event() if cancel else None
        timer = threading.Timer(0.02, event.set) if event else None
        started = time.perf_counter()
        if timer:
            timer.start()
        try:
            result = sf_engine.search_file(str(path), "needle", mode, stop_event=event)
            elapsed = time.perf_counter() - started
        finally:
            if timer:
                timer.cancel()
                timer.join()
        assert result == [], "No-match fixture returned results"
        if index:
            samples.append(elapsed)
    return statistics.median(samples)


def main():
    print("case,median_seconds")
    with tempfile.TemporaryDirectory(prefix="sf-review-") as directory:
        root = Path(directory)
        for depth in (2000, 4000, 8000):
            path = root / "deep.xml"
            path.write_text("<a>" * depth + "x" + "</a>" * depth, encoding="utf-8")
            print(f"xml-depth-{depth},{measure(path, Constants.RUST_MODE_XML):.6f}")
        for size in (4, 8, 16, 64):
            path = root / "long.txt"
            path.write_bytes(b"\xff\xfe" + b"x\0" * (size * 1024 * 1024 // 2))
            print(f"utf16-{size}MiB,{measure(path, Constants.RUST_MODE_NORMAL):.6f}")
            if size == 64:
                print(f"utf16-64MiB-cancel-20ms,{measure(path, Constants.RUST_MODE_NORMAL, True):.6f}")


if __name__ == "__main__":
    main()
