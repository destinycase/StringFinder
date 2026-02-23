"""
[test_performance_hidden.py]

이 테스트는 윈도우 시스템의 '숨김' 속성 파일 및 디렉토리에 대한 필터링 정책을 검증합니다.

- 테스트 목적:
  1. 사용자의 '숨김 파일 제외' 옵션 선택 시, 스캐너와 엔진이 이를 정확히 수행하는지 확인.
  2. Rust 엔진과 Python 엔진 모두에서 동일한 숨김 속성 필터링 행동을 보이는지 보장.

- 주요 검증 사항:
  1. `SetFileAttributesW`를 통한 실제 윈도우 숨김 속성 생성 및 탐지 테스트.
  2. `FileScanner`의 `exclude_hidden` 플래그 작동 여부.
  3. Rust 기반 고속 디렉토리 검색(`search_directory_fast`)에서의 숨김 파일 제외 정밀도.
"""

import ctypes
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.search_engine import HAS_RUST_ENGINE, FileScanner, find_files_with_keyword_fast, search_directory_fast


def set_hidden(path):
    """set_hidden 함수."""
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
    """test_python_scanner_exclude_hidden 함수."""
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
    """test_rust_engine_exclude_hidden 함수."""
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
    """test_rust_smart_scan_exclude_hidden 함수."""
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
    """test_rust_files_list_exclude_hidden 함수."""
    from core.search_engine import search_files_list_fast

    normal_dir = hidden_test_env / "normal_dir"
    hidden_dir = hidden_test_env / "hidden_dir"
    files = [str(normal_dir / "target.txt"), str(hidden_dir / "target.txt")]

    res = search_files_list_fast(files, "find", exclude_hidden=True)
    # 핵심 검증: 인자 처리에서 예외 없이 정상 반환되는지 확인
    assert "results" in res
