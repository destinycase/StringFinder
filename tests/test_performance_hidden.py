"""
[test_performance_hidden.py]

???뚯뒪?몃뒗 ?덈룄???쒖뒪?쒖쓽 '?④?' ?띿꽦 ?뚯씪 諛??붾젆?좊━??????꾪꽣留??뺤콉??寃利앺빀?덈떎.

- ?뚯뒪??紐⑹쟻:
  1. ?ъ슜?먯쓽 '?④? ?뚯씪 ?쒖쇅' ?듭뀡 ?좏깮 ?? ?ㅼ틦?덉? ?붿쭊???대? ?뺥솗???섑뻾?섎뒗吏 ?뺤씤.
  2. Rust ?붿쭊怨?Python ?붿쭊 紐⑤몢?먯꽌 ?숈씪???④? ?띿꽦 ?꾪꽣留??됰룞??蹂댁씠?붿? 蹂댁옣.

- 二쇱슂 寃利??ы빆:
  1. `SetFileAttributesW`瑜??듯븳 ?ㅼ젣 ?덈룄???④? ?띿꽦 ?앹꽦 諛??먯? ?뚯뒪??
  2. `FileScanner`??`exclude_hidden` ?뚮옒洹??묐룞 ?щ?.
  3. Rust 湲곕컲 怨좎냽 ?붾젆?좊━ 寃??`search_directory_fast`)?먯꽌???④? ?뚯씪 ?쒖쇅 ?뺣???
"""

import ctypes
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.search_engine import HAS_RUST_ENGINE, FileScanner, find_files_with_keyword_fast, search_directory_fast


def set_hidden(path):
    """set_hidden ?⑥닔."""
    if os.name == "nt":
        ret = ctypes.windll.kernel32.SetFileAttributesW(str(path), 2)
        if not ret:
            pytest.skip("Failed to set hidden attribute, skipping test.")
    else:
        pytest.skip("This test requires Windows file attributes.")


@pytest.fixture
def hidden_test_env(tmp_path):
    base = tmp_path / "env"
    base.mkdir()

    normal_dir = base / "normal_dir"
    normal_dir.mkdir()
    (normal_dir / "target.txt").write_text("find me in normal", encoding="utf-8")

    hidden_dir = base / "hidden_dir"
    hidden_dir.mkdir()
    (hidden_dir / "target.txt").write_text("find me in hidden", encoding="utf-8")

    set_hidden(hidden_dir)

    return base


def test_python_scanner_exclude_hidden(hidden_test_env):
    """test_python_scanner_exclude_hidden ?⑥닔."""
    scanner = FileScanner(folders=[str(hidden_test_env)], extensions=[".txt"], exclude_hidden=True)
    files = scanner.scan()
    paths = [f[0] for f in files]
    assert any("normal_dir" in p for p in paths)
    assert not any("hidden_dir" in p for p in paths)
    assert len(files) == 1

    scanner = FileScanner(folders=[str(hidden_test_env)], extensions=[".txt"], exclude_hidden=False)
    files = scanner.scan()
    assert len(files) == 2


@pytest.mark.skipif(not HAS_RUST_ENGINE, reason="Rust engine required")
def test_rust_engine_exclude_hidden(hidden_test_env):
    """test_rust_engine_exclude_hidden ?⑥닔."""
    res = search_directory_fast([str(hidden_test_env)], "find", extensions=["txt"], exclude_hidden=True)
    results = res.get("results", [])
    paths = [r[0] for r in results]
    assert any("normal_dir" in p for p in paths)
    assert not any("hidden_dir" in p for p in paths)

    res = search_directory_fast([str(hidden_test_env)], "find", extensions=["txt"], exclude_hidden=False)
    results = res.get("results", [])
    assert len(results) == 2


@pytest.mark.skipif(not HAS_RUST_ENGINE, reason="Rust engine required")
def test_rust_smart_scan_exclude_hidden(hidden_test_env):
    """test_rust_smart_scan_exclude_hidden ?⑥닔."""
    found = find_files_with_keyword_fast([str(hidden_test_env)], "find", extensions=["txt"], exclude_hidden=True)
    if isinstance(found, tuple):
        found_files = found[0]
    else:
        found_files = found

    paths = [f[0] for f in found_files]
    assert any("normal_dir" in p for p in paths)
    assert not any("hidden_dir" in p for p in paths)
    assert len(found_files) == 1

    found = find_files_with_keyword_fast([str(hidden_test_env)], "find", extensions=["txt"], exclude_hidden=False)
    if isinstance(found, tuple):
        found_files = found[0]
    else:
        found_files = found
    assert len(found_files) == 2


@pytest.mark.skipif(not HAS_RUST_ENGINE, reason="Rust engine required")
def test_rust_files_list_exclude_hidden(hidden_test_env):
    """Validate exclude_hidden behavior for explicit file list scans."""
    from core.search_engine import search_files_list_fast

    normal_dir = hidden_test_env / "normal_dir"
    hidden_dir = hidden_test_env / "hidden_dir"
    hidden_file = hidden_dir / "target.txt"
    set_hidden(hidden_file)
    files = [str(normal_dir / "target.txt"), str(hidden_file)]

    res = search_files_list_fast(files, "find", exclude_hidden=True)
    results = res.get("results", [])
    paths = [r[0] for r in results]
    assert any("normal_dir" in p for p in paths)
    assert not any("hidden_dir" in p for p in paths)

    res = search_files_list_fast(files, "find", exclude_hidden=False)
    results = res.get("results", [])
    paths = [r[0] for r in results]
    assert any("normal_dir" in p for p in paths)
    assert any("hidden_dir" in p for p in paths)
    assert len(results) == 2
