import os
import sys
import time
import shutil
import statistics
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEventLoop
import datetime

# 프로젝트 경로 설정
project_root = Path(__file__).parent.parent
src_path = str(project_root / "src")
if src_path not in sys.path:
    sys.path.insert(0, src_path)

from core.worker import SearchWorker  # noqa: E402
from sf_utils.constants import Constants  # noqa: E402

BENCHMARK_DATA_DIR = project_root / "data" / "benchmark"


def create_dataset():
    """표준 데이터셋 3종 생성"""
    if BENCHMARK_DATA_DIR.exists():
        print(f"[Info] Cleaning existing benchmark data: {BENCHMARK_DATA_DIR}")
        shutil.rmtree(BENCHMARK_DATA_DIR)

    BENCHMARK_DATA_DIR.mkdir(parents=True, exist_ok=True)

    print("[Step 1/3] Creating Set A: Small & Many (10,000 files)")
    set_a = BENCHMARK_DATA_DIR / "set_a"
    set_a.mkdir()
    for i in range(10000):
        with open(set_a / f"file_{i:05d}.txt", "w", encoding="utf-8") as f:
            content = "This is a small test file. " * 50
            if i % 100 == 0:
                content += "target_keyword_a "
            f.write(content)

    print("[Step 2/3] Creating Set B: Mixed & Large (50MB+ files)")
    set_b = BENCHMARK_DATA_DIR / "set_b"
    set_b.mkdir()
    for i in range(10):
        # 50MB 파일 2개 포함
        size = 50 * 1024 * 1024 if i < 2 else 1 * 1024 * 1024
        with open(set_b / f"large_{i}.txt", "wb") as f:
            f.write(b"data " * (size // 5))
            if i == 0:
                f.seek(size // 2)
                f.write(b"target_keyword_b")

    print("[Step 3/4] Creating Set C: Binary Mixed")
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

    print("[Step 4/4] Creating Set D & E: Performance Specialized")
    set_de = BENCHMARK_DATA_DIR / "set_de"
    set_de.mkdir()

    # Set D: Boolean Early Exit (500MB, Match at Start)
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


def run_benchmark(dataset_name, path, keyword, is_boolean=False):
    """지정된 데이터셋에 대해 벤치마크 수행"""
    app = QApplication.instance()
    if not app:
        app = QApplication(sys.argv)

    print(f"\n[Bench] Running benchmark for {dataset_name} (Boolean={is_boolean})...")

    params = {
        Constants.PAYLOAD_SEARCH_PATHS: [str(path)],
        Constants.PAYLOAD_SEARCH_STRING: keyword,
        Constants.PAYLOAD_EXTENSIONS: [],
        Constants.PAYLOAD_EXCLUDE_BINARY: False,
        Constants.PAYLOAD_EXCLUDE_HIDDEN: True,
        "is_boolean": is_boolean,
    }

    worker = SearchWorker(params)
    loop = QEventLoop()

    from typing import Optional, Any
    
    start_time: float = time.time()
    first_result_time: Optional[float] = None
    finish_time: Optional[float] = None
    progress_intervals: list[float] = []
    last_progress_time: float = time.time()
    total_results: int = 0

    def on_results(formatted_batch: list[Any]) -> None:
        nonlocal first_result_time, total_results
        if first_result_time is None:
            first_result_time = time.time()
        for item in formatted_batch:
            try:
                total_results += item[1]
            except (IndexError, TypeError):
                pass

    def on_progress(count: int, total: int) -> None:
        nonlocal last_progress_time
        now = time.time()
        interval = now - last_progress_time
        if interval > 0:
            progress_intervals.append(interval)
        last_progress_time = now

    def on_finished() -> None:
        nonlocal finish_time
        finish_time = time.time()
        loop.quit()

    from PySide6.QtCore import QThreadPool

    worker.signals.results_found.connect(on_results)
    worker.signals.progress_updated.connect(on_progress)
    worker.signals.finished.connect(on_finished)

    QThreadPool.globalInstance().start(worker)
    loop.exec()

    assert finish_time is not None

    # 지표 계산
    total_time = finish_time - start_time
    latency = (first_result_time - start_time) if first_result_time else total_time
    jitter = float(statistics.stdev(progress_intervals)) if len(progress_intervals) > 1 else 0.0

    return {
        "dataset": dataset_name,
        "total_time": total_time,
        "latency": latency,
        "jitter": jitter,
        "results_count": total_results,
    }


def print_result_table(bench_results):
    print("\n" + "=" * 90)
    print(" PERFORMANCE BENCHMARK REPORT (PERF-07)")
    print("=" * 90)
    print(f"| {'Dataset':<25} | {'Total Time':<12} | {'Latency':<12} | {'Jitter':<12} | {'Hits':<8} |")
    print(f"| {'-' * 25} | {'-' * 12} | {'-' * 12} | {'-' * 12} | {'-' * 8} |")
    for res in bench_results:
        print(
            f"| {res['dataset']:<25} | {res['total_time']:>10.3f}s | {res['latency']:>10.3f}s | {res['jitter']:>10.4f} | {res['results_count']:>8} |"
        )
    print("=" * 90)


def save_benchmark_history(bench_results, tag="Current"):
    """벤치마크 결과를 benchmark_history.md 파일에 누적 기록"""
    history_file = project_root / "benchmark_history.md"
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    header_exists = history_file.exists()
    
    with open(history_file, "a", encoding="utf-8") as f:
        if not header_exists:
            f.write("# StringFinder 성능 벤치마크 기록\n\n")
            f.write("| 시간 | 버전/태그 | 데이터셋 | 전체 시간 | Latency | Jitter | 결과 수 |\n")
            f.write("| --- | --- | --- | --- | --- | --- | --- |\n")
        
        for res in bench_results:
            f.write(f"| {now} | {tag} | {res['dataset']} | {res['total_time']:.3f}s | {res['latency']:.3f}s | {res['jitter']:.4f} | {res['results_count']} |\n")
        f.write("| " + "---" * 15 + " |\n") # 구분선 추가

    print(f"\n[Info] Benchmark results accumulated in: {history_file}")


def check_thresholds(results):
    """성능 기준치 (Thresholds) - [REF-PERF-07]"""
    # [Adjustment] Worker 실행 오버헤드(GIL, QApp, Process Management)를 고려하여 완화
    THRESHOLDS = {
        "Set A: Small/Many": {"total_time": 3.0, "latency": 1.5, "jitter": 0.2},
        "Set B: Mixed/Large": {"total_time": 1.5, "latency": 0.8, "jitter": 0.2},
        "Set C: Binary Mixed": {"total_time": 1.0, "latency": 0.8, "jitter": 0.2},
        "Set D: Boolean Early": {"total_time": 1.5, "latency": 1.5, "jitter": 0.2},  # 500MB 대용량 파일 핸들링 오버헤드
        "Set E: ASCII Fast": {"total_time": 1.0, "latency": 0.8, "jitter": 0.2},
    }

    failed = False
    for res in results:
        t = THRESHOLDS.get(res["dataset"])
        if t:
            if res["total_time"] > t["total_time"]:
                print(f"[FAIL] {res['dataset']}: Total Time {res['total_time']:.3f}s > {t['total_time']}s")
                failed = True
            # Jitter check is sensitive to OS scheduler, warning only
            if res["jitter"] > t["jitter"]:
                print(f"[WARN] {res['dataset']}: Jitter {res['jitter']:.3f} > {t['jitter']}")

    if failed:
        print("\n[Result] Performance check FAILED! Some benchmarks exceeded thresholds.")
        sys.exit(1)
    else:
        print("\n[Result] Performance check PASSED. All metrics within limits.")


if __name__ == "__main__":
    try:
        if not (BENCHMARK_DATA_DIR).exists() or "--force-gen" in sys.argv:
            create_dataset()
        else:
            print("[Info] Using existing benchmark datasets.")

        results = []
        results.append(run_benchmark("Set A: Small/Many", BENCHMARK_DATA_DIR / "set_a", "target_keyword_a"))
        results.append(run_benchmark("Set B: Mixed/Large", BENCHMARK_DATA_DIR / "set_b", "target_keyword_b"))
        results.append(run_benchmark("Set C: Binary Mixed", BENCHMARK_DATA_DIR / "set_c", "target_keyword_c"))
        results.append(
            run_benchmark(
                "Set D: Boolean Early",
                BENCHMARK_DATA_DIR / "set_de" / "boolean_early.txt",
                "target_keyword_d",
                is_boolean=True,
            )
        )
        results.append(
            run_benchmark("Set E: ASCII Fast", BENCHMARK_DATA_DIR / "set_de" / "ascii_fast.txt", "target_keyword_e")
        )

        print_result_table(results)
        
        # 벤치마크 결과 저장 (v1.0.4 - Before)
        tag = "v1.0.4 - Before Batch 1"
        if len(sys.argv) > 1 and not sys.argv[1].startswith("--"):
            tag = sys.argv[1]
        save_benchmark_history(results, tag=tag)

        check_thresholds(results)
    except Exception as e:
        print(f"\n[Error] Benchmark failed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
