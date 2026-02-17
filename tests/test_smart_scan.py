import pytest
import os
import shutil
import tempfile
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
from core.search_engine import find_files_with_keyword_fast, HAS_RUST_ENGINE


@pytest.fixture
def temp_test_env():
    """
    Creates a temporary directory with:
    1. file_with_keyword.txt (contains "KEYWORD")
    2. file_without_keyword.txt (does not contain "KEYWORD")
    3. file_ignored_ext.log (contains "KEYWORD", but extension might be filtered)
    """
    base_dir = tempfile.mkdtemp(prefix="test_smart_scan_")

    with open(os.path.join(base_dir, "file_with_keyword.txt"), "w", encoding="utf-8") as f:
        f.write("This file contains the KEYWORD used for testing.")

    with open(os.path.join(base_dir, "file_without_keyword.txt"), "w", encoding="utf-8") as f:
        f.write("This file contains nothing relevant.")

    with open(os.path.join(base_dir, "file_ignored_ext.log"), "w", encoding="utf-8") as f:
        f.write("KEYWORD is here but extension is log.")

    yield base_dir
    shutil.rmtree(base_dir)


def test_smart_scan_basic(temp_test_env):
    if not HAS_RUST_ENGINE:
        pytest.skip("Rust engine not available")

    search_path = [temp_test_env]
    keyword = "KEYWORD"
    extensions = ["txt"]

    # Run Smart Scan
    start_time = time.time()
    found_files = find_files_with_keyword_fast(search_path, keyword, extensions)
    duration = time.time() - start_time

    print(f"\nSmart Scan Duration: {duration:.4f}s")
    print(f"Found Files: {found_files}")

    # Verification
    # 1. Should find "file_with_keyword.txt"
    # Return type is now [(path, size), ...]
    found_paths = [f[0] for f in found_files]
    assert any("file_with_keyword.txt" in f for f in found_paths)

    # 2. Should NOT find "file_without_keyword.txt" (Binary Pre-check Check)
    assert not any("file_without_keyword.txt" in f for f in found_paths)

    # 3. Should NOT find "file_ignored_ext.log" (Extension Filter Check)
    assert not any("file_ignored_ext.log" in f for f in found_paths)


def test_smart_scan_case_insensitive(temp_test_env):
    if not HAS_RUST_ENGINE:
        pytest.skip("Rust engine not available")

    search_path = [temp_test_env]
    keyword = "keyword"  # Lowercase search
    extensions = ["txt"]

    found_files = find_files_with_keyword_fast(search_path, keyword, extensions)
    found_paths = [f[0] for f in found_files]

    # Should find "file_with_keyword.txt" (contains "KEYWORD")
    assert any("file_with_keyword.txt" in f for f in found_paths)
