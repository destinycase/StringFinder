"""Benchmark allocation-sensitive Rust Excel search paths.

Run from the repository root before and after rebuilding the Rust extension:
    python tools/benchmark_excel.py
"""

from __future__ import annotations

import argparse
import gc
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rust_engine import sf_engine  # type: ignore  # noqa: E402
from sf_utils.constants import Constants  # noqa: E402


@dataclass(frozen=True)
class ExcelBenchmarkCase:
    name: str
    query: str
    existence_only: bool
    expected_matches: int


def _create_dense_workbook(path: Path, rows: int, columns: int) -> None:
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("DenseStrings")
    for row_index in range(rows):
        values = [f"payload-{row_index:06d}-{column_index}" for column_index in range(columns)]
        if row_index == rows - 1:
            values[-1] = "needle-final-cell"
        sheet.append(values)
    workbook.save(path)


def _run(path: Path, case: ExcelBenchmarkCase):
    mode_bits = Constants.RUST_MODE_NORMAL | Constants.RUST_MODE_EXCEL
    if case.existence_only:
        mode_bits |= Constants.RUST_MODE_EXISTENCE_ONLY
    return sf_engine.search_file(
        str(path),
        case.query,
        mode_bits,
        max_per_file=5_000,
        max_check_cells=10_000_000,
    )


def _measure(path: Path, case: ExcelBenchmarkCase, repeats: int) -> float:
    warmup = _run(path, case)
    if len(warmup) != case.expected_matches:
        raise RuntimeError(f"{case.name}: expected {case.expected_matches} results, got {len(warmup)}")

    timings = []
    for _ in range(repeats):
        gc.collect()
        started = time.perf_counter()
        result = _run(path, case)
        timings.append(time.perf_counter() - started)
        if len(result) != case.expected_matches:
            raise RuntimeError(f"{case.name}: expected {case.expected_matches} results, got {len(result)}")
    return statistics.median(timings)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=100_000)
    parser.add_argument("--columns", type=int, default=4)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()

    cases = (
        ExcelBenchmarkCase("excel-dense-no-match", "absent-keyword", False, 0),
        ExcelBenchmarkCase("excel-dense-last-match", "needle-final-cell", False, 1),
        ExcelBenchmarkCase("excel-dense-existence-last", "needle-final-cell", True, 1),
    )

    with tempfile.TemporaryDirectory(prefix="stringfinder-excel-bench-") as temp_dir:
        workbook_path = Path(temp_dir) / "dense_strings.xlsx"
        _create_dense_workbook(workbook_path, max(1, args.rows), max(1, args.columns))
        print("case,cells,size_mib,median_seconds")
        for case in cases:
            median = _measure(workbook_path, case, max(1, args.repeats))
            print(
                f"{case.name},{args.rows * args.columns},"
                f"{workbook_path.stat().st_size / (1024 * 1024):.2f},{median:.4f}"
            )


if __name__ == "__main__":
    main()
