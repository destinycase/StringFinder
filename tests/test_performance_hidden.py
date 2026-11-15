"""숨김 파일 제외, gitignore 파일 포함, 겹치는 폴더 중복 제거, 공통 파일 크기 제한."""

import ctypes
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.search_engine import search_directory_fast
from folder_search_helper import search_in_file


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


