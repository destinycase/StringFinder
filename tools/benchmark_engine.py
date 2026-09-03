"""Measure representative StringFinder Rust search paths locally.

Run from the repository root:
    python tools/benchmark_engine.py
"""

from __future__ import annotations

import gc
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from core.search_engine import search_in_file  # noqa: E402
from sf_utils.constants import Constants  # noqa: E402


@dataclass(frozen=True)
class BenchmarkCase:
    name: str
    path: Path
    query: str
    special_mode: str | None = None
    existence_only: bool = False
    expect_match: bool = True


def _rss_bytes() -> int | None:
    try:
        import psutil

        return psutil.Process(os.getpid()).memory_info().rss
    except ImportError:
        return None


def _measure(
    path: Path,
    query: str,
    special_mode: str | None = None,
    repeats: int = 3,
    *,
    existence_only: bool = False,
    expect_match: bool = True,
) -> tuple[float, float | None, int]:
    def run_search():
        return search_in_file(
            str(path),
            query,
            special_mode=special_mode,
            use_complex_search=False,
            existence_only=existence_only,
        )

    run_search()
    timings: list[float] = []
    rss_delta: float | None = None
    for _ in range(repeats):
        gc.collect()
        before = _rss_bytes()
        started = time.perf_counter()
        result = run_search()
        elapsed = time.perf_counter() - started
        after = _rss_bytes()
        timings.append(elapsed)
        if before is not None and after is not None:
            current_delta = (after - before) / (1024 * 1024)
            rss_delta = current_delta if rss_delta is None else max(rss_delta, current_delta)
        matched = result is not None
        if matched != expect_match:
            expectation = "match" if expect_match else "no match"
            raise RuntimeError(f"benchmark expected {expectation}: {path}")
    timings.sort()
    return timings[len(timings) // 2], rss_delta, path.stat().st_size


def _write_cases(root: Path) -> list[BenchmarkCase]:
    plain_unit = "prefix data without the query\nneedle appears here\n"
    plain_1m = root / "plain_1m.txt"
    plain_32m = root / "plain_32m.txt"
    plain_1m.write_text(plain_unit * (1024 * 1024 // len(plain_unit)), encoding="utf-8")
    plain_32m.write_text(plain_unit * (32 * 1024 * 1024 // len(plain_unit)), encoding="utf-8")

    json_path = root / "structured.json"
    json_items = [{"id": index, "value": "payload", "needle": "present"} for index in range(120_000)]
    json_path.write_text(json.dumps({"items": json_items}, ensure_ascii=False), encoding="utf-8")

    xml_path = root / "structured.xml"
    xml_items = "".join(
        f'<item id="{index}"><value>payload</value><needle>present</needle></item>' for index in range(120_000)
    )
    xml_path.write_text(f"<root>{xml_items}</root>", encoding="utf-8")

    return [
        BenchmarkCase("plain-1MiB", plain_1m, "needle"),
        BenchmarkCase("plain-32MiB", plain_32m, "needle"),
        BenchmarkCase("json-streaming-repeated", json_path, "present", Constants.MODE_JSON),
        BenchmarkCase("json-streaming-sparse", json_path, "119999", Constants.MODE_JSON),
        BenchmarkCase(
            "json-streaming-existence",
            json_path,
            "present",
            Constants.MODE_JSON,
            existence_only=True,
        ),
        BenchmarkCase(
            "json-no-match-fallback",
            json_path,
            "absent_keyword",
            Constants.MODE_JSON,
            expect_match=False,
        ),
        BenchmarkCase("xml-streaming", xml_path, "present", Constants.MODE_XML),
    ]


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="stringfinder-bench-") as temp_dir:
        cases = _write_cases(Path(temp_dir))
        print("case,size_mib,median_seconds,max_rss_delta_mib")
        for case in cases:
            median, rss_delta, size = _measure(
                case.path,
                case.query,
                case.special_mode,
                existence_only=case.existence_only,
                expect_match=case.expect_match,
            )
            rss_text = "n/a" if rss_delta is None else f"{rss_delta:.2f}"
            print(f"{case.name},{size / (1024 * 1024):.2f},{median:.4f},{rss_text}")


if __name__ == "__main__":
    main()
