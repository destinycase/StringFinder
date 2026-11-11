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


def test_rust_files_list_exclude_hidden(hidden_test_env):
    """Validate exclude_hidden behavior for explicit file list scans."""

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


def test_normal_search_deduplicates_overlapping_roots(tmp_path):
    nested = tmp_path / "nested"
    nested.mkdir()
    target = nested / "match.txt"
    target.write_text("needle", encoding="utf-8")

    result = search_directory_fast(
        [str(tmp_path), str(nested)], "needle", extensions=["txt"], exclude_hidden=False
    )

    assert [item[0] for item in result["results"]] == [str(target)]


def test_all_search_modes_apply_the_configured_common_file_size_limit(tmp_path, monkeypatch):
    from core import search_engine
    from sf_utils.constants import Constants

    monkeypatch.setattr(
        search_engine.ConfigManager,
        "get_advanced_settings",
        lambda _self: {Constants.CONFIG_KEY_MAX_SEARCH_FILE_SIZE_MB: 1},
    )

    # Rust는 실제 파일 크기로 상한을 판정하므로 상한(1MB)보다 큰 파일을 만듭니다.
    contents = {
        "txt": "needle " + "x" * (2 * 1024 * 1024),
        "json": '{"v": "needle", "pad": "' + "x" * (2 * 1024 * 1024) + '"}',
        "xml": "<root><v>needle</v><pad>" + "x" * (2 * 1024 * 1024) + "</pad></root>",
    }
    for extension, special_mode in (
        ("txt", None),
        ("json", Constants.MODE_JSON),
        ("xml", Constants.MODE_XML),
    ):
        target = tmp_path / f"oversized.{extension}"
        target.write_text(contents[extension], encoding="utf-8")
        oversized_size = target.stat().st_size
        result = search_in_file(
            str(target),
            "needle",
            special_mode=special_mode,
        )
        assert result is not None
        assert result[0] == Constants.STATUS_SKIPPED
        assert str(oversized_size) in result[1]


