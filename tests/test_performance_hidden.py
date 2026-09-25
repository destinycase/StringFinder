"""
[test_performance_hidden.py]

이 테스트는 윈도우 시스템의 '숨김' 속성 파일 및 디렉토리에 대한 필터링 정책을 검증합니다.

- 테스트 목적:
  1. 사용자의 '숨김 파일 제외' 옵션 선택 시 스캔 엔진이 이를 정확히 인지하는지 확인.
  2. Rust 엔진과 Python 엔진 모두에서 동일한 숨김 속성 필터링 동작 보장.

- 주요 검증 사항:
  1. `SetFileAttributesW`를 통한 실제 윈도우 숨김 속성 생성 및 탐지 테스트.
  2. `FileScanner` 및 Rust 기반 고속 검색 함수에서의 `exclude_hidden` 작동 여부.
"""

import ctypes
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.search_engine import (
    FileScanner,
    find_files_with_keyword_fast,
    search_directory_fast,
    search_files_list_fast,
    search_in_file,
)


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


def test_rust_engine_search_includes_gitignored_files(tmp_path):
    """Normal search should not apply repository .gitignore exclusions."""
    (tmp_path / ".gitignore").write_text("ignored.json\n", encoding="utf-8")
    (tmp_path / "ignored.json").write_text('{"value": "needle"}', encoding="utf-8")

    result = search_directory_fast(
        [str(tmp_path)], "needle", extensions=["json"], exclude_hidden=False
    )

    results = result.get("results", [])
    assert len(results) == 1
    assert results[0][0].endswith("ignored.json")


def test_normal_and_precise_search_both_ignore_no_ignore_files(tmp_path):
    (tmp_path / ".ignore").write_text("ignored.txt\n", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("needle in ignored file", encoding="utf-8")
    (tmp_path / "kept.txt").write_text("needle in kept file", encoding="utf-8")

    precise_names = {
        os.path.basename(path)
        for path, _size in FileScanner(
            [str(tmp_path)], ["txt"], exclude_hidden=False
        ).scan()
    }
    normal_names = {
        os.path.basename(item[0])
        for item in search_directory_fast(
            [str(tmp_path)], "needle", extensions=["txt"], exclude_hidden=False
        )["results"]
    }

    assert precise_names == normal_names == {"ignored.txt", "kept.txt"}


def test_normal_search_deduplicates_overlapping_roots(tmp_path):
    nested = tmp_path / "nested"
    nested.mkdir()
    target = nested / "match.txt"
    target.write_text("needle", encoding="utf-8")

    result = search_directory_fast(
        [str(tmp_path), str(nested)], "needle", extensions=["txt"], exclude_hidden=False
    )

    assert [item[0] for item in result["results"]] == [str(target)]


def test_empty_extension_filter_means_all_files_for_precise_scanner(tmp_path):
    with_extension = tmp_path / "with-extension.txt"
    without_extension = tmp_path / "without-extension"
    with_extension.write_text("needle", encoding="utf-8")
    without_extension.write_text("needle", encoding="utf-8")

    scanned = FileScanner(
        [str(tmp_path)], [], exclude_hidden=False
    ).scan()

    assert {os.path.basename(path) for path, _size in scanned} == {
        with_extension.name,
        without_extension.name,
    }
    normal = search_directory_fast(
        [str(tmp_path)], "needle", extensions=[], exclude_hidden=False
    )
    assert {os.path.basename(item[0]) for item in normal["results"]} == {
        with_extension.name,
        without_extension.name,
    }


def test_precise_scanner_does_not_follow_symlinked_directories(tmp_path):
    root = tmp_path / "root"
    external = tmp_path / "external"
    root.mkdir()
    external.mkdir()
    target = external / "linked.txt"
    target.write_text("needle", encoding="utf-8")
    try:
        os.symlink(external, root / "alias", target_is_directory=True)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Creating symlinks requires Windows Developer Mode or elevation")
        raise

    scanned = FileScanner([str(root)], ["txt"], exclude_hidden=False).scan()

    assert scanned == []


def test_precise_scanner_rejects_symlink_entries_without_following_them(tmp_path, monkeypatch):
    class SymlinkEntry:
        name = "linked.txt"
        path = str(tmp_path / name)

        def is_symlink(self):
            return True

        def is_dir(self, *, follow_symlinks=True):
            raise AssertionError("symlink target must not be queried as a directory")

        def is_file(self, *, follow_symlinks=True):
            raise AssertionError("symlink target must not be queried as a file")

    class ScandirContext:
        def __enter__(self):
            return iter([SymlinkEntry()])

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(os, "scandir", lambda _path: ScandirContext())
    scanner = FileScanner([str(tmp_path)], ["txt"], exclude_hidden=False)
    files = []

    scanner._scan_recursive(str(tmp_path), files, set())

    assert files == []


def test_all_search_modes_apply_the_configured_common_file_size_limit(tmp_path, monkeypatch):
    from core import search_engine
    from sf_utils.constants import Constants

    target = tmp_path / "oversized.txt"
    target.write_text("small fixture", encoding="utf-8")
    oversized_size = 2 * 1024 * 1024
    monkeypatch.setattr(
        search_engine.ConfigManager,
        "get_advanced_settings",
        lambda _self: {Constants.CONFIG_KEY_MAX_SEARCH_FILE_SIZE_MB: 1},
    )

    for extension, special_mode in (
        ("txt", None),
        ("json", Constants.MODE_JSON),
        ("xml", Constants.MODE_XML),
        ("xlsx", None),
    ):
        result = search_in_file(
            str(target.with_suffix(f".{extension}")),
            "needle",
            file_size=oversized_size,
            special_mode=special_mode,
            use_complex_search=True,
        )
        assert result is not None
        assert result[0] == Constants.STATUS_SKIPPED
        assert str(oversized_size) in result[1]


def test_python_scanner_matches_rust_hidden_dotfile_policy(tmp_path):
    """Dot-prefixed files are hidden for both normal and precise searches."""
    dotfile = tmp_path / ".package-lock.json"
    dotfile.write_text('{"needle": "le"}', encoding="utf-8")

    excluded = FileScanner(
        folders=[str(tmp_path)], extensions=[".json"], exclude_hidden=True
    ).scan()
    included = FileScanner(
        folders=[str(tmp_path)], extensions=[".json"], exclude_hidden=False
    ).scan()

    assert not any(os.path.basename(item[0]) == dotfile.name for item in excluded)
    assert any(os.path.basename(item[0]) == dotfile.name for item in included)


def test_recycle_bin_is_always_excluded_from_every_search_entry_point(tmp_path):
    normal = tmp_path / "normal.txt"
    normal.write_text("needle", encoding="utf-8")
    recycle = tmp_path / "$RECYCLE.BIN" / "user" / "recycled.txt"
    recycle.parent.mkdir(parents=True)
    recycle.write_text("needle", encoding="utf-8")

    scanned = FileScanner(
        folders=[str(tmp_path)],
        extensions=[".txt"],
        exclude_hidden=False,
    ).scan()
    assert [path for path, _size in scanned] == [str(normal)]

    directory_result = search_directory_fast(
        [str(tmp_path)],
        "needle",
        extensions=["txt"],
        exclude_hidden=False,
    )
    assert [item[0] for item in directory_result["results"]] == [str(normal)]

    listed_result = search_files_list_fast(
        [str(normal), str(recycle)],
        "needle",
        exclude_hidden=False,
    )
    assert [item[0] for item in listed_result["results"]] == [str(normal)]

    found = find_files_with_keyword_fast(
        [str(tmp_path)],
        "needle",
        extensions=["txt"],
        exclude_hidden=False,
    )
    assert [item[0] for item in found] == [str(normal)]
    assert search_in_file(str(recycle), "needle", force_python=True) is None
