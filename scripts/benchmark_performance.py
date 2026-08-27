import os
import sys
import time
import shutil
import statistics
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QEventLoop, QThreadPool, QTimer
from PySide6.QtWidgets import QApplication
import datetime
import psutil
import json

# Project path setup
project_root = Path(__file__).parent.parent
src_path = str(project_root / "src")
if src_path not in sys.path:
    sys.path.insert(0, src_path)

from core.worker import SearchWorker  # noqa: E402
from sf_utils.constants import Constants  # noqa: E402
from sf_utils._version import VERSION  # noqa: E402

BENCHMARK_DATA_DIR = project_root / "data" / "benchmark"
HISTORY_TITLE_DEFAULT = "# StringFinder Performance Benchmark History"
HISTORY_COLUMNS = [
    "Time",
    "Tag",
    "Dataset",
    "Total Time",
    "Latency",
    "Jitter",
    "Peak RSS",
    "Hits",
    "Skipped",
    "Skip Reasons",
]
# Rust engine hard limit (src/rust_engine/src/lib.rs: MAX_FILE_SIZE)
RUST_MAX_FILE_SIZE_BYTES = 1024 * 1024 * 1024
# Keep Set H below operational limits (memory guard / RSS threshold).
SET_H_TARGET_FILE_SIZE_BYTES = 96 * 1024 * 1024
SET_H_KEYWORD_BYTES = b"target_keyword_h\n"
SET_J_KEYWORD = "target_keyword_j"


def _create_set_j_excel_file(file_path: Path) -> None:
    """Create a valid XLSX benchmark file for Excel special-mode tests."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from openpyxl import Workbook
    except ImportError as exc:
        raise RuntimeError("openpyxl is required to generate Set J Excel benchmark data.") from exc

    wb = Workbook()
    ws = wb.active
    ws.title = "BenchMain"
    ws.append(["id", "source", "translation"])
    for i in range(1, 5001):
        source = f"source_{i}"
        trans = f"translation_{i}"
        if i == 2500:
            source = f"prefix_{SET_J_KEYWORD}_suffix"
        ws.append([i, source, trans])

    ws2 = wb.create_sheet("BenchAux")
    ws2.append(["key", "value"])
    for i in range(1, 2001):
        value = f"aux_value_{i}"
        if i == 1333:
            value = f"{SET_J_KEYWORD}_aux"
        ws2.append([f"k{i}", value])

    wb.save(file_path)




def _create_set_h_sparse_file(file_path: Path) -> None:
    """Create Set H within engine size limits."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    target_size = SET_H_TARGET_FILE_SIZE_BYTES
    if target_size >= RUST_MAX_FILE_SIZE_BYTES:
        raise ValueError("Set H target size must be strictly less than Rust max file size.")
    if target_size <= len(SET_H_KEYWORD_BYTES):
        raise ValueError("Set H target size is too small for keyword payload.")

    # Final file size == seek_pos + len(keyword), so seek below the hard limit.
    seek_pos = target_size - len(SET_H_KEYWORD_BYTES)
    with open(file_path, "wb") as f:
        f.write(b"Start of Stress\n")
        f.seek(seek_pos)
        f.write(SET_H_KEYWORD_BYTES)



def create_dataset():
    """Create benchmark datasets A-J."""
    if BENCHMARK_DATA_DIR.exists():
        print(f"[Info] Cleaning existing benchmark data: {BENCHMARK_DATA_DIR}")
        shutil.rmtree(BENCHMARK_DATA_DIR)

    BENCHMARK_DATA_DIR.mkdir(parents=True, exist_ok=True)

    print("[Step 1/6] Creating Set A: Small & Many (10,000 files)")
    set_a = BENCHMARK_DATA_DIR / "set_a"
    set_a.mkdir()
    for i in range(10000):
        with open(set_a / f"file_{i:05d}.txt", "w", encoding="utf-8") as f:
            content = "This is a small test file. " * 50
            if i % 100 == 0:
                content += "target_keyword_a "
            f.write(content)

    print("[Step 2/6] Creating Set B: Mixed & Large (50MB+ files)")
    set_b = BENCHMARK_DATA_DIR / "set_b"
    set_b.mkdir()
    for i in range(10):
        # First two files are 50MB, others are 1MB.
        size = 50 * 1024 * 1024 if i < 2 else 1 * 1024 * 1024
        with open(set_b / f"large_{i}.txt", "wb") as f:
            line = b"data " * 200 + b"\n"
            written = 0
            while written < size:
                f.write(line)
                written += len(line)
            if i == 0:
                f.seek(size // 2)
                f.write(b"target_keyword_b\n")

    print("[Step 3/6] Creating Set C: Binary Mixed")
    set_c = BENCHMARK_DATA_DIR / "set_c"
    set_c.mkdir()
    for i in range(500):
        is_binary = i % 2 == 0
        ext = "bin" if is_binary else "txt"
        with open(set_c / f"file_{i}.{ext}", "wb") as f:
            if is_binary:
                f.write(os.urandom(1024))
                if i % 10 == 0:
                    f.write(b"target_keyword_c")
            else:
                f.write(b"Normal text content. " * 10)
                if i % 10 == 0:
                    f.write(b"target_keyword_c")

    print("[Step 4/6] Creating Set D & E: Performance Specialized")
    set_de = BENCHMARK_DATA_DIR / "set_de"
    set_de.mkdir()

    # Set D: Boolean Early Exit (500MB, match at start)
    with open(set_de / "boolean_early.txt", "wb") as f:
        f.write(b"target_keyword_d\n")
        content_bytes = b"Regular noise line for padding.\n" * 1000
        target = 500 * 1024 * 1024
        written = 0
        while written < target:
            f.write(content_bytes)
            written += len(content_bytes)

    # Set E: ASCII Fast-Path (100MB ASCII)
    with open(set_de / "ascii_fast.txt", "w", encoding="ascii") as f:
        content_str = "Standard ASCII line for fast-path testing.\n" * 1000
        target = 100 * 1024 * 1024
        written = 0
        while written < target:
            f.write(content_str)
            written += len(content_str)
        f.write("target_keyword_e\n")

    print("[Step 5/6] Creating Set F-I: High-Density Stress Sets")
    set_stress = BENCHMARK_DATA_DIR / "set_stress"
    set_stress.mkdir()

    # Set F: Monster JSON
    print(" - Generating Monster JSON...")
    sample_entry = {"id": 0, "val": "some random string content " * 10}
    data_list = [sample_entry.copy() for _ in range(100000)]
    for i in range(100000):
        data_list[i]["id"] = i
    data_list[50000]["keyword"] = "target_keyword_f"
    monster_json = {"metadata": "benchmark", "data": data_list}
    with open(set_stress / "monster.json", "w", encoding="utf-8") as f:
        json.dump(monster_json, f)

    # Set G: Deep XML
    print(" - Generating Deep XML...")
    with open(set_stress / "deep.xml", "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="utf-8"?>\n<root>\n')
        for i in range(1000):
            f.write(f"<node_{i}>\n")
        f.write("  <content>target_keyword_g</content>\n")
        f.write("  <padding>" + ("x" * 50 * 1024 * 1024) + "</padding>\n")
        for i in range(999, -1, -1):
            f.write(f"</node_{i}>\n")
        f.write("</root>\n")

    # Set H: Sparse stress (large but under operational limits)
    print(" - Generating Sparse Stress (96MB, safe)...")
    _create_set_h_sparse_file(set_stress / "sparse_stress.txt")

    # Set I: No-newline stress (50MB)
    print(" - Generating No-Newline Stress (50MB)...")
    with open(set_stress / "no_newline.txt", "wb") as f:
        f.write(b"x" * (50 * 1024 * 1024))
        f.write(b"target_keyword_i")

    print("[Step 6/6] Creating Set J: Excel Special Mode")
    set_special = BENCHMARK_DATA_DIR / "set_special"
    set_special.mkdir()
    print(" - Generating Excel benchmark (Set J)...")
    _create_set_j_excel_file(set_special / "excel_mix.xlsx")


def ensure_dataset_within_limits() -> None:
    """
    Repair oversized benchmark files in-place so runs do not exceed engine limits.
    This keeps historical datasets usable without requiring --force-gen.
    """
    set_h_path = BENCHMARK_DATA_DIR / "set_stress" / "sparse_stress.txt"
    if set_h_path.exists():
        try:
            size = set_h_path.stat().st_size
        except OSError:
            size = None

        if size is not None and size != SET_H_TARGET_FILE_SIZE_BYTES:
            print(
                "[Fix] Set H size drift detected "
                f"({size} != {SET_H_TARGET_FILE_SIZE_BYTES}). Recreating safe benchmark file..."
            )
            try:
                _create_set_h_sparse_file(set_h_path)
            except OSError:
                # If sparse metadata/state causes rewrite issues, recreate from scratch.
                try:
                    set_h_path.unlink(missing_ok=True)
                except OSError:
                    pass
                _create_set_h_sparse_file(set_h_path)

    # Ensure the Excel special-mode benchmark file exists.
    set_special_dir = BENCHMARK_DATA_DIR / "set_special"
    set_special_dir.mkdir(parents=True, exist_ok=True)
    set_j_path = set_special_dir / "excel_mix.xlsx"
    if not set_j_path.exists():
        print("[Fix] Set J missing. Creating Excel benchmark file...")
        _create_set_j_excel_file(set_j_path)


def run_benchmark(
    dataset_name: str,
    path: Path,
    keyword: str,
    existence_only: bool = False,
    special_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """Run benchmark for one dataset."""
    app = QApplication.instance()
    if not app:
        app = QApplication(sys.argv)

    print(f"\n[Bench] Running benchmark for {dataset_name} (Existence_Only={existence_only})...")

    params = {
        Constants.PAYLOAD_SEARCH_PATHS: [str(path)],
        Constants.PAYLOAD_SEARCH_STRING: keyword,
        Constants.PAYLOAD_EXTENSIONS: [],
        Constants.PAYLOAD_EXCLUDE_BINARY: False,
        Constants.PAYLOAD_EXCLUDE_HIDDEN: True,
        Constants.PAYLOAD_EXISTENCE_ONLY: existence_only,
    }
    if special_mode:
        params[Constants.PAYLOAD_SPECIAL_MODE] = special_mode

    worker = SearchWorker(params)
    loop = QEventLoop()

    start_time: float = time.time()
    first_activity_time: Optional[float] = None
    finish_time: Optional[float] = None
    progress_intervals: List[float] = []
    last_progress_time: float = time.time()
    total_results: int = 0
    reported_total_matches: Optional[int] = None
    reported_skipped_count: Optional[int] = None
    skipped_by_path: Dict[str, str] = {}

    def on_results(formatted_batch: List[Any]) -> None:
        nonlocal first_activity_time, total_results
        if first_activity_time is None:
            first_activity_time = time.time()
        for item in formatted_batch:
            try:
                total_results += int(item[1])
            except (IndexError, TypeError, ValueError):
                pass

    def on_skipped(skipped_batch: List[Any]) -> None:
        nonlocal first_activity_time
        if first_activity_time is None:
            first_activity_time = time.time()
        for item in skipped_batch:
            try:
                path_item, reason_item = item[0], item[1]
                skipped_by_path[str(path_item)] = str(reason_item)
            except (IndexError, TypeError):
                continue

    def on_progress(count: int, total: int) -> None:
        nonlocal last_progress_time
        _ = count
        _ = total
        now = time.time()
        interval = now - last_progress_time
        if interval > 0:
            progress_intervals.append(interval)
        last_progress_time = now

    def on_finished() -> None:
        nonlocal finish_time
        finish_time = time.time()
        loop.quit()

    def on_search_finished(found_count: int, total_matches: int, skipped_count: int) -> None:
        nonlocal reported_total_matches, reported_skipped_count
        _ = found_count
        reported_total_matches = int(total_matches)
        reported_skipped_count = int(skipped_count)

    process = psutil.Process()
    peak_rss: float = 0.0

    def check_memory() -> None:
        nonlocal peak_rss
        try:
            current_rss = process.memory_info().rss / (1024 * 1024)
            if current_rss > peak_rss:
                peak_rss = current_rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    mem_timer = QTimer()
    mem_timer.timeout.connect(check_memory)
    mem_timer.start(10)

    worker.signals.results_found.connect(on_results)
    worker.signals.skipped_found.connect(on_skipped)
    worker.signals.search_finished.connect(on_search_finished)
    worker.signals.progress_updated.connect(on_progress)
    worker.signals.finished.connect(on_finished)
    worker.signals.finished.connect(mem_timer.stop)

    QThreadPool.globalInstance().start(worker)
    loop.exec()

    assert finish_time is not None

    total_time = finish_time - start_time
    latency = (first_activity_time - start_time) if first_activity_time else total_time
    jitter = float(statistics.stdev(progress_intervals)) if len(progress_intervals) > 1 else 0.0
    results_count = reported_total_matches if reported_total_matches is not None else total_results
    skipped_count = reported_skipped_count if reported_skipped_count is not None else len(skipped_by_path)
    if skipped_count < len(skipped_by_path):
        skipped_count = len(skipped_by_path)

    skip_reason_counts: Dict[str, int] = {}
    for reason in skipped_by_path.values():
        skip_reason_counts[reason] = skip_reason_counts.get(reason, 0) + 1

    return {
        "dataset": dataset_name,
        "total_time": total_time,
        "latency": latency,
        "jitter": jitter,
        "results_count": results_count,
        "skipped_count": skipped_count,
        "skipped_reasons": skip_reason_counts,
        "peak_rss": peak_rss,
    }


def print_result_table(bench_results: List[Dict[str, Any]]) -> None:
    print("\n" + "=" * 104)
    print(" PERFORMANCE BENCHMARK REPORT (PERF-07)")
    print("=" * 104)
    print(
        f"| {'Dataset':<25} | {'Total Time':<12} | {'Latency':<12} | {'Jitter':<12} | "
        f"{'Peak RSS':<12} | {'Hits':<8} | {'Skipped':<8} |"
    )
    print(
        f"| {'-' * 25} | {'-' * 12} | {'-' * 12} | {'-' * 12} | {'-' * 12} | {'-' * 8} | {'-' * 8} |"
    )
    for res in bench_results:
        print(
            f"| {res['dataset']:<25} | {res['total_time']:>10.3f}s | {res['latency']:>10.3f}s | "
            f"{res['jitter']:>10.4f} | {res['peak_rss']:>9.1f} MB | {res['results_count']:>8} | "
            f"{res.get('skipped_count', 0):>8} |"
        )
    print("=" * 104)


def _parse_history_line(line: str) -> Optional[Dict[str, str]]:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return None

    parts = [p.strip() for p in stripped.strip("|").split("|")]
    if not parts:
        return None
    if all((not cell) or set(cell) <= {"-"} for cell in parts):
        return None
    if parts[0] in ("Time", "시간") or parts[0].startswith("---"):
        return None
    if len(parts) < 7:
        return None

    row = {
        "Time": parts[0],
        "Tag": parts[1] if len(parts) > 1 else "",
        "Dataset": parts[2] if len(parts) > 2 else "",
        "Total Time": parts[3] if len(parts) > 3 else "",
        "Latency": parts[4] if len(parts) > 4 else "",
        "Jitter": parts[5] if len(parts) > 5 else "",
        "Peak RSS": "",
        "Hits": "",
        "Skipped": "0",
        "Skip Reasons": "",
    }
    if len(parts) == 7:
        row["Hits"] = parts[6]
    elif len(parts) == 8:
        row["Peak RSS"] = parts[6]
        row["Hits"] = parts[7]
    else:
        row["Peak RSS"] = parts[6]
        row["Hits"] = parts[7]
        row["Skipped"] = parts[8] if len(parts) > 8 and parts[8] else "0"
        row["Skip Reasons"] = parts[9] if len(parts) > 9 else ""
    return row


def _load_history_rows(history_file: Path) -> Tuple[str, List[Dict[str, str]]]:
    title = HISTORY_TITLE_DEFAULT
    rows: List[Dict[str, str]] = []
    if not history_file.exists():
        return title, rows

    with open(history_file, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.rstrip("\n")
            if line.lstrip().startswith("#") and title == HISTORY_TITLE_DEFAULT:
                title = line.strip()
                continue
            parsed = _parse_history_line(line)
            if parsed:
                rows.append(parsed)
    return title, rows


def _write_history_rows(history_file: Path, title: str, rows: List[Dict[str, str]]) -> None:
    with open(history_file, "w", encoding="utf-8") as f:
        f.write(f"{title}\n\n")
        f.write("| " + " | ".join(HISTORY_COLUMNS) + " |\n")
        f.write("| " + " | ".join(["---"] * len(HISTORY_COLUMNS)) + " |\n")
        for row in rows:
            ordered = [row.get(col, "") for col in HISTORY_COLUMNS]
            f.write("| " + " | ".join(ordered) + " |\n")


def _looks_mojibake_text(text: str) -> bool:
    """Heuristic check for unreadable mojibake-like strings in history output."""
    if not text:
        return False
    if "??" in text:
        return True

    hangul = 0
    latin = 0
    cjk = 0
    for ch in text:
        code = ord(ch)
        if 0xAC00 <= code <= 0xD7A3:
            hangul += 1
        elif ("a" <= ch <= "z") or ("A" <= ch <= "Z"):
            latin += 1
        elif (0x4E00 <= code <= 0x9FFF) or (0x3040 <= code <= 0x30FF):
            cjk += 1

    # Skip reasons in this project are expected to be Korean/English templates.
    return hangul == 0 and latin < 3 and cjk >= 2


def _sanitize_skip_reason_text(reason: str) -> str:
    sanitized = str(reason or "").strip().replace("|", "/")
    if not sanitized:
        return ""
    if _looks_mojibake_text(sanitized):
        size_match = re.search(r"(\d+\s*bytes)", sanitized, re.IGNORECASE)
        if size_match:
            return f"[Error] File too large: {size_match.group(1)}"
        return "[Error] Unreadable skip reason"
    return sanitized


def _summarize_skip_reasons(skip_reasons: Dict[str, int], max_items: int = 3) -> str:
    if not skip_reasons:
        return ""
    normalized: Dict[str, int] = {}
    for reason, count in skip_reasons.items():
        key = _sanitize_skip_reason_text(reason)
        normalized[key] = normalized.get(key, 0) + int(count)

    ordered = sorted(normalized.items(), key=lambda item: (-item[1], item[0]))
    chunks = [f"{reason} ({count})" for reason, count in ordered[:max_items]]
    summary = "; ".join(chunks)
    return summary[:180]


def save_benchmark_history(bench_results: List[Dict[str, Any]], tag: str = "Current") -> None:
    """Persist benchmark history with schema normalization."""
    history_file = project_root / "benchmark_history.md"
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    title, rows = _load_history_rows(history_file)
    for row in rows:
        row["Skip Reasons"] = _sanitize_skip_reason_text(row.get("Skip Reasons", ""))
    for res in bench_results:
        rows.append(
            {
                "Time": now,
                "Tag": str(tag),
                "Dataset": str(res["dataset"]),
                "Total Time": f"{res['total_time']:.3f}s",
                "Latency": f"{res['latency']:.3f}s",
                "Jitter": f"{res['jitter']:.4f}",
                "Peak RSS": f"{res['peak_rss']:.1f} MB",
                "Hits": str(res["results_count"]),
                "Skipped": str(res.get("skipped_count", 0)),
                "Skip Reasons": _summarize_skip_reasons(res.get("skipped_reasons", {})),
            }
        )
    _write_history_rows(history_file, title, rows)

    print(f"\n[Info] Benchmark results accumulated in: {history_file}")


def check_thresholds(results: List[Dict[str, Any]]) -> None:
    """Performance thresholds check - [REF-PERF-07]."""
    THRESHOLDS = {
        "Set A: Small/Many": {"total_time": 3.0, "latency": 1.5, "jitter": 0.2, "peak_rss": 200.0},
        "Set B: Mixed/Large": {"total_time": 1.5, "latency": 0.8, "jitter": 0.2, "peak_rss": 200.0},
        "Set C: Binary Mixed": {"total_time": 1.0, "latency": 0.8, "jitter": 0.2, "peak_rss": 150.0},
        "Set D: Boolean Early": {"total_time": 1.5, "latency": 1.5, "jitter": 0.2, "peak_rss": 150.0},
        "Set E: ASCII Fast": {"total_time": 1.0, "latency": 0.8, "jitter": 0.2, "peak_rss": 200.0},
        "Set F: Monster JSON": {"total_time": 5.0, "latency": 2.0, "jitter": 0.3, "peak_rss": 600.0},
        "Set G: Deep XML": {"total_time": 3.0, "latency": 2.0, "jitter": 0.3, "peak_rss": 300.0},
        "Set H: Sparse Stress": {"total_time": 1.5, "latency": 1.5, "jitter": 0.3, "peak_rss": 200.0},
        "Set I: No-Newline": {"total_time": 5.0, "latency": 2.0, "jitter": 0.5, "peak_rss": 500.0},
        "Set J: Excel Mixed": {"total_time": 3.0, "latency": 2.0, "jitter": 0.3, "peak_rss": 300.0},
    }
    EXPECTED_MIN_HITS = {
        "Set A: Small/Many": 100,
        "Set B: Mixed/Large": 1,
        "Set C: Binary Mixed": 50,
        "Set D: Boolean Early": 1,
        "Set E: ASCII Fast": 1,
        "Set F: Monster JSON": 1,
        "Set G: Deep XML": 1,
        "Set H: Sparse Stress": 1,
        "Set I: No-Newline": 1,
        "Set J: Excel Mixed": 1,
    }

    failed = False
    for res in results:
        t = THRESHOLDS.get(res["dataset"])
        if not t:
            continue

        if res["total_time"] > t["total_time"]:
            print(f"[FAIL] {res['dataset']}: Total Time {res['total_time']:.3f}s > {t['total_time']}s")
            failed = True
        if res["latency"] > t["latency"]:
            print(f"[FAIL] {res['dataset']}: Latency {res['latency']:.3f}s > {t['latency']}s")
            failed = True
        if res["peak_rss"] > t["peak_rss"]:
            print(f"[FAIL] {res['dataset']}: Peak RSS {res['peak_rss']:.1f} MB > {t['peak_rss']} MB")
            failed = True
        if res["jitter"] > t["jitter"]:
            print(f"[WARN] {res['dataset']}: Jitter {res['jitter']:.3f} > {t['jitter']}")

        expected_hits = EXPECTED_MIN_HITS.get(res["dataset"])
        if expected_hits is not None and int(res.get("results_count", 0)) < expected_hits:
            print(
                f"[FAIL] {res['dataset']}: Hits {res.get('results_count', 0)} "
                f"< expected minimum {expected_hits}"
            )
            failed = True

        skipped = int(res.get("skipped_count", 0))
        if skipped > 0:
            reason_summary = _summarize_skip_reasons(res.get("skipped_reasons", {}), max_items=1)
            if reason_summary:
                print(f"[FAIL] {res['dataset']}: Skipped {skipped} file(s) ({reason_summary})")
            else:
                print(f"[FAIL] {res['dataset']}: Skipped {skipped} file(s)")
            failed = True

    if failed:
        print("\n[Result] Performance check FAILED! Some benchmarks exceeded thresholds.")
        sys.exit(1)
    else:
        print("\n[Result] Performance check PASSED. All metrics within limits.")


if __name__ == "__main__":
    try:
        if not BENCHMARK_DATA_DIR.exists() or "--force-gen" in sys.argv:
            create_dataset()
        else:
            print("[Info] Using existing benchmark datasets.")
            ensure_dataset_within_limits()

        results: List[Dict[str, Any]] = []
        results.append(run_benchmark("Set A: Small/Many", BENCHMARK_DATA_DIR / "set_a", "target_keyword_a"))
        results.append(run_benchmark("Set B: Mixed/Large", BENCHMARK_DATA_DIR / "set_b", "target_keyword_b"))
        results.append(run_benchmark("Set C: Binary Mixed", BENCHMARK_DATA_DIR / "set_c", "target_keyword_c"))
        results.append(
            run_benchmark(
                "Set D: Boolean Early",
                BENCHMARK_DATA_DIR / "set_de" / "boolean_early.txt",
                "target_keyword_d",
                existence_only=True,
            )
        )
        results.append(run_benchmark("Set E: ASCII Fast", BENCHMARK_DATA_DIR / "set_de" / "ascii_fast.txt", "target_keyword_e"))
        results.append(run_benchmark("Set F: Monster JSON", BENCHMARK_DATA_DIR / "set_stress" / "monster.json", "target_keyword_f"))
        results.append(run_benchmark("Set G: Deep XML", BENCHMARK_DATA_DIR / "set_stress" / "deep.xml", "target_keyword_g"))
        results.append(run_benchmark("Set H: Sparse Stress", BENCHMARK_DATA_DIR / "set_stress" / "sparse_stress.txt", "target_keyword_h"))
        results.append(run_benchmark("Set I: No-Newline", BENCHMARK_DATA_DIR / "set_stress" / "no_newline.txt", "target_keyword_i"))
        results.append(
            run_benchmark(
                "Set J: Excel Mixed",
                BENCHMARK_DATA_DIR / "set_special" / "excel_mix.xlsx",
                SET_J_KEYWORD,
                special_mode=Constants.MODE_EXCEL,
            )
        )

        print_result_table(results)

        tag = f"v{VERSION}"
        if len(sys.argv) > 1 and not sys.argv[1].startswith("--"):
            tag = sys.argv[1]
        
        # [Improvement] Save history BEFORE threshold check so failures are recorded.
        save_benchmark_history(results, tag=tag)
        check_thresholds(results)
    except Exception as e:
        print(f"\n[Error] Benchmark failed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
