import pytest
import os
import shutil
import time
import tempfile
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
from core.search_engine import HAS_RUST_ENGINE, search_directory_fast, search_in_file


# --- Setup for large directory structure ---
@pytest.fixture(scope="module")
def large_directory_structure():
    """
    Generates a large directory structure for benchmarking.
    Structure:
    - root
        - sub1
            - file_1.txt
            - ...
        - ...
        - subN
    Target: 100 subdirs * 100 files = 10,000 files.
    """
    base_dir = tempfile.mkdtemp(prefix="bench_rust_")

    file_count = 0
    target_string = "FIND_THIS_KEYWORD"

    print(f"\nGeneraring benchmark files in {base_dir}...")

    for i in range(50):  # 50 subdirectories
        sub_dir = os.path.join(base_dir, f"sub_{i}")
        os.makedirs(sub_dir)
        for j in range(100):  # 100 files per subdir => 5000 files total
            file_path = os.path.join(sub_dir, f"file_{j}.txt")
            with open(file_path, "w", encoding="utf-8") as f:
                if j % 50 == 0:
                    f.write(f"Line 1\n{target_string}\nLine 3")
                else:
                    f.write(f"Just some content {i}-{j}\n")
            file_count += 1

    print(f"Generated {file_count} files.")

    yield base_dir

    print(f"\nCleaning up {base_dir}...")
    shutil.rmtree(base_dir)


def python_walk_search(root_dir, search_string):
    """Simulates the old Python-side walking"""
    results = []
    skipped = []

    # Simple recursive walk
    for root, dirs, files in os.walk(root_dir):
        for name in files:
            file_path = os.path.join(root, name)
            # We use search_in_file which might use Rust internally for *content* search,
            # but the walking is Python.
            # To measure "Python Walking Overhead", we use the python search_in_file logic (mocking if needed)
            # But practically, the comparison is:
            # 1. os.walk (Python) -> Content Search
            # 2. Rust Parallel Walk -> Content Search

            # Since search_in_file automatically uses Rust if available (HAS_RUST_ENGINE),
            # this correctly measures the "Walking & Dispatch" overhead of Python vs Rust.
            res = search_in_file(file_path, search_string)
            if res:
                results.append(res)
    return results


def test_rust_vs_python_walk(benchmark, large_directory_structure):
    if not HAS_RUST_ENGINE:
        pytest.skip("Rust engine not available")

    search_string = "FIND_THIS_KEYWORD"

    # 1. Benchmark Rust Implementation
    def run_rust():
        return search_directory_fast([large_directory_structure], search_string, ["txt"])

    # 2. Benchmark Python Implementation
    def run_python():
        return python_walk_search(large_directory_structure, search_string)

    print("\n--- Rust Engine Benchmark ---")
    rust_result = benchmark(run_rust)

    # Since we can only run one benchmark per test function with standard pytest-benchmark,
    # we might need two separate tests or manual timing if we want to compare in one go.
    # However, pytest-benchmark is designed for 1 function.
    # We will use manual timing for comparison output here, and let pytest-benchmark track the optimized one.

    start = time.time()
    py_res = run_python()
    py_duration = time.time() - start

    rust_duration = benchmark.stats["mean"]

    # Verify correctness
    rust_matches = 0
    # rust returns dict {"results": [(path, count, matches), ...]}
    if isinstance(rust_result, dict):
        rust_matches = len(rust_result.get("results", []))

    py_matches = len(py_res)

    print("\n[Comparison]")
    print("Files: ~5000")
    print(f"Python Walk: {py_duration:.4f} sec")
    print(f"Rust   Walk: {rust_duration:.4f} sec")
    if rust_duration > 0:
        print(f"Speedup: {py_duration / rust_duration:.2f}x")

    assert rust_matches == py_matches
    # Expectation: Rust should be significantly faster (at least 2x, often 10x)
    # Asserting strict performance might be flaky on shared runners, but for local 5x is safe target.
    # match count should be 50 directories * 2 matches (j%50==0 => indices 0, 50) = 100 matches
    assert rust_matches == 100
