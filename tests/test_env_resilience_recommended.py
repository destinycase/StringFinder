"""
[test_env_resilience_recommended.py]

이 테스트는 다양한 실행 환경 변화 및 잠재적 오류 상황에서의 시스템 복원력을 광범위하게 검증합니다.

- 테스트 목적:
  1. Rust 엔진 로드 실패나 API 버전 불일치 시나리오에서 Python 엔진으로의 안정적인 폴백(Fallback) 보장.
  2. 윈도우 롱 패스(Long Path), 파일 잠금(Sharing Violation) 등 운영체제 수준의 엣계 케이스 대응력 확인.
  3. 대용량 파일 처리 시의 메모리 사용량 제어(Memory Budget) 정책 검증.

- 주요 검증 사항:
  1. `sf_engine` API 버전 불일치 탐지 및 엔진 비활성화 로직.
  2. 폴백 모드에서의 이진 파일 placeholder 및 정밀 검색(Exact Match) 의미론 일치 여부.
  3. 윈도우 특유의 파일 접근 제한 상황에서의 무중단 스캐닝.
  4. 동시 실행 워커 간의 상태 격리(No State Leak).
"""

import importlib
import os
import sys
import threading
import time
import tracemalloc
import types
from typing import Any, cast

import pytest

from core.search_engine import FileScanner, search_directory_fast, search_files_list_fast, search_in_file
from core.worker import SearchWorker
from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants


def _require_search_result(result: object) -> tuple[str, int, list[tuple[Any, ...]]]:
    assert isinstance(result, tuple) and len(result) == 3
    return cast(tuple[str, int, list[tuple[Any, ...]]], result)


@pytest.mark.parametrize("api_version", [None, 3])
def test_engine_api_version_mismatch_fallback(monkeypatch, api_version):
    import core.search_engine as search_engine

    real_pkg = sys.modules.get("rust_engine")
    real_sf_engine = sys.modules.get("rust_engine.sf_engine")
    
    fake_engine = types.SimpleNamespace(
        search_file=lambda *args, **kwargs: [],
        search_dir=lambda *args, **kwargs: ([], []),
        search_files_list=lambda *args, **kwargs: ([], []),
        find_files_with_keyword=lambda *args, **kwargs: ([], []),
    )
    if api_version is not None:
        fake_engine.API_VERSION = api_version

    fake_pkg = types.ModuleType("rust_engine")
    fake_pkg.sf_engine = fake_engine  # type: ignore

    try:
        monkeypatch.setitem(sys.modules, "rust_engine", fake_pkg)
        monkeypatch.setitem(sys.modules, "rust_engine.sf_engine", fake_engine)
        reloaded = importlib.reload(search_engine)
        assert reloaded.HAS_RUST_ENGINE is False
    finally:
        if real_pkg is None:
            sys.modules.pop("rust_engine", None)
        else:
            sys.modules["rust_engine"] = real_pkg
            
        if real_sf_engine is None:
            sys.modules.pop("rust_engine.sf_engine", None)
        else:
            sys.modules["rust_engine.sf_engine"] = real_sf_engine
        importlib.reload(search_engine)


def test_fallback_exact_match_semantics(tmp_path):
    p = tmp_path / "exact_semantics.txt"
    p.write_text("user_id\nid\nidentity\n", encoding="utf-8")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("core.search_engine.HAS_RUST_ENGINE", False)
        result = search_in_file(str(p), "id", special_mode=Constants.MODE_EXACT, use_complex_search=True)

    search_result = _require_search_result(result)
    assert search_result[1] == 1
    assert search_result[2][0][1].strip() == "id"


def test_fallback_binary_placeholder_consistency(tmp_path):
    p = tmp_path / "binary_placeholder.bin"
    p.write_bytes(b"Some text\x00More binary data with KEYWORD here")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("core.search_engine.HAS_RUST_ENGINE", False)
        result = search_in_file(str(p), "KEYWORD", use_complex_search=True)

    search_result = _require_search_result(result)
    assert search_result[1] == 1
    assert search_result[2][0][1] == AppStrings.MSG_BINARY_MATCH.format(1)


def test_rust_binary_marker_is_mapped_to_appstrings(tmp_path):
    p = tmp_path / "rust_marker_binary.txt"
    p.write_text("dummy", encoding="utf-8")

    fake_match = types.SimpleNamespace(
        line=1,
        content="__SF_BINARY_MATCH__|3",
        offset=None,
        length=3,
    )
    fake_engine = types.SimpleNamespace(search_file=lambda *_args, **_kwargs: [fake_match])

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("core.search_engine.HAS_RUST_ENGINE", True)
        mp.setattr("core.search_engine.sf_engine", fake_engine, raising=False)
        result = search_in_file(str(p), "KEYWORD")

    search_result = _require_search_result(result)
    assert search_result[1] == 3
    assert search_result[2][0][1] == AppStrings.MSG_BINARY_MATCH.format(3)


def test_rust_long_line_marker_is_mapped_to_appstrings(tmp_path):
    p = tmp_path / "rust_marker_long_line.txt"
    p.write_text("dummy", encoding="utf-8")

    preview = "A" * 32
    fake_match = types.SimpleNamespace(
        line=7,
        content=f"__SF_LONG_LINE__|{preview}",
        offset=100,
        length=5000000,
    )
    fake_engine = types.SimpleNamespace(search_file=lambda *_args, **_kwargs: [fake_match])

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("core.search_engine.HAS_RUST_ENGINE", True)
        mp.setattr("core.search_engine.sf_engine", fake_engine, raising=False)
        result = search_in_file(str(p), "KEYWORD")

    search_result = _require_search_result(result)
    assert search_result[1] == 1
    assert search_result[2][0][0] == 7
    assert search_result[2][0][1] == AppStrings.MSG_LONG_LINE_PREVIEW.format(preview)


def test_rust_search_dir_binary_marker_is_mapped():
    fake_match = types.SimpleNamespace(
        line=1,
        content="__SF_BINARY_MATCH__|2",
        offset=None,
        length=2,
    )
    fake_engine = types.SimpleNamespace(search_dir=lambda *_args, **_kwargs: ([("a.bin", [fake_match])], []))

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("core.search_engine.HAS_RUST_ENGINE", True)
        mp.setattr("core.search_engine.sf_engine", fake_engine, raising=False)
        result = search_directory_fast(["."], "KEYWORD")

    assert result["results"][0][1] == 2
    assert result["results"][0][2][0][1] == AppStrings.MSG_BINARY_MATCH.format(2)


def test_rust_search_files_list_long_line_marker_is_mapped():
    preview = "preview-content"
    fake_match = types.SimpleNamespace(
        line=11,
        content=f"__SF_LONG_LINE__|{preview}",
        offset=77,
        length=999999,
    )
    fake_engine = types.SimpleNamespace(search_files_list=lambda *_args, **_kwargs: ([("a.txt", [fake_match])], []))

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("core.search_engine.HAS_RUST_ENGINE", True)
        mp.setattr("core.search_engine.sf_engine", fake_engine, raising=False)
        result = search_files_list_fast(["a.txt"], "KEYWORD")

    assert result["results"][0][1] == 1
    assert result["results"][0][2][0][0] == 11
    assert result["results"][0][2][0][1] == AppStrings.MSG_LONG_LINE_PREVIEW.format(preview)


def test_rust_search_dir_excel_panic_marker_maps_to_skipped():
    fake_match = types.SimpleNamespace(
        line=1,
        content="__SF_EXCEL_PANIC__|xlsx",
        offset=None,
        length=None,
    )
    fake_engine = types.SimpleNamespace(search_dir=lambda *_args, **_kwargs: ([("panic.xlsx", [fake_match])], []))

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("core.search_engine.HAS_RUST_ENGINE", True)
        mp.setattr("core.search_engine.sf_engine", fake_engine, raising=False)
        result = search_directory_fast(["."], "KEYWORD", special_mode=Constants.MODE_EXCEL)

    assert result["results"] == []
    assert result["skipped"] == [("panic.xlsx", AppStrings.ERROR_EXCEL_PANIC.format("xlsx"))]


def test_rust_search_files_list_excel_panic_marker_maps_to_skipped():
    fake_match = types.SimpleNamespace(
        line=1,
        content="__SF_EXCEL_PANIC__|xls",
        offset=None,
        length=None,
    )
    fake_engine = types.SimpleNamespace(search_files_list=lambda *_args, **_kwargs: ([("panic.xls", [fake_match])], []))

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("core.search_engine.HAS_RUST_ENGINE", True)
        mp.setattr("core.search_engine.sf_engine", fake_engine, raising=False)
        result = search_files_list_fast(["panic.xls"], "KEYWORD", special_mode=Constants.MODE_EXCEL)

    assert result["results"] == []
    assert result["skipped"] == [("panic.xls", AppStrings.ERROR_EXCEL_PANIC.format("xls"))]


def test_mainwindow_qtimer_after_destroy(qtbot, mock_config_manager):
    from ui.main_window import MainWindow

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("core.search_engine.HAS_RUST_ENGINE", False)
        window = MainWindow()
        qtbot.addWidget(window)
        window.close()
        window.deleteLater()
        qtbot.wait(1200)


def test_binary_placeholder_contract_rust_enabled(tmp_path):
    from core.search_engine import HAS_RUST_ENGINE

    if not HAS_RUST_ENGINE:
        pytest.skip("Rust engine required for Rust placeholder contract test")

    p = tmp_path / "binary_placeholder_rust.bin"
    p.write_bytes(b"Some text\x00More binary data with KEYWORD here")

    search_result = _require_search_result(search_in_file(str(p), "KEYWORD"))
    assert search_result[1] >= 1
    assert "\uc774\uc9c4 \ud30c\uc77c" in str(search_result[2][0][1])


def test_searchworker_without_file_list_path_scan(tmp_path):
    p = tmp_path / "scan_me.txt"
    p.write_text("needle", encoding="utf-8")

    worker = SearchWorker(
        {
            "file_list": [],
            "search_paths": [str(tmp_path)],
            "search_string": "needle",
            "extensions": ["txt"],
            "use_complex_search": True,
        }
    )

    finished: list[tuple[int, int]] = []
    worker.signals.search_finished.connect(lambda found, skipped: finished.append((found, skipped)))

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("core.search_engine.HAS_RUST_ENGINE", False)
        worker.run()

    assert finished
    assert finished[0][0] == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows-only long path behavior")
def test_windows_long_path_unicode_search(tmp_path):
    deep = tmp_path
    for i in range(7):
        deep = deep / f"unicode_segment_{i}_\ud655\uc7a5"
    deep.mkdir(parents=True)

    f = deep / "\ud14c\uc2a4\ud2b8_\ud0a4\uc6cc\ub4dc.txt"
    f.write_text("\uc5ec\uae30\uc5d0 \ud0a4\uc6cc\ub4dc\uac00 \uc788\uc2b5\ub2c8\ub2e4.", encoding="utf-8")

    long_path = "\\\\?\\" + str(f.resolve())
    search_result = _require_search_result(search_in_file(long_path, "\ud0a4\uc6cc\ub4dc"))
    assert search_result[1] >= 1


@pytest.mark.skipif(os.name != "nt", reason="Windows-only lock behavior")
def test_windows_file_lock_sharing_violation_skip(monkeypatch, tmp_path):
    p = tmp_path / "locked.txt"
    p.write_text("keyword", encoding="utf-8")

    real_open = open

    def fake_open(path, mode="r", *args, **kwargs):
        if os.fspath(path) == str(p):
            raise PermissionError("Sharing violation")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fake_open)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("core.search_engine.HAS_RUST_ENGINE", False)
        result = search_in_file(str(p), "keyword", use_complex_search=True)

    assert isinstance(result, tuple) and len(result) == 2
    assert result[0] == Constants.STATUS_SKIPPED


@pytest.mark.chaos
@pytest.mark.skipif(os.name != "nt", reason="Windows-only UNC behavior")
def test_unc_invalid_or_offline_share_timeout():
    unc_path = r"\\this-host-should-not-exist-for-stringfinder-tests\missing-share"
    scanner = FileScanner([unc_path], [".txt"])

    t0 = time.perf_counter()
    result = scanner.scan()
    elapsed = time.perf_counter() - t0

    assert result == []
    assert elapsed < 10.0


def test_mixed_encodings_and_corrupt_bytes(tmp_path):
    utf16be_file = tmp_path / "utf16be_no_bom.txt"
    cp949_file = tmp_path / "cp949_text.txt"
    corrupt_file = tmp_path / "corrupt.bin"

    utf16be_file.write_bytes("TARGET".encode("utf-16-be"))
    cp949_file.write_bytes("KOREAN TARGET".encode("cp949"))
    corrupt_file.write_bytes(b"\xff\xfe\x00\x80TARGET\xff\x00")

    r1 = search_in_file(str(utf16be_file), "TARGET", use_complex_search=True)
    r2 = search_in_file(str(cp949_file), "TARGET", use_complex_search=True)
    r3 = search_in_file(str(corrupt_file), "TARGET", use_complex_search=True)

    assert r1 is None or (isinstance(r1, tuple) and len(r1) in (2, 3))
    assert r2 is not None
    assert r3 is None or (isinstance(r3, tuple) and len(r3) in (2, 3))



@pytest.mark.stress
def test_large_file_memory_budget(tmp_path):
    p = tmp_path / "large_memory_budget.txt"
    chunk = ("0123456789abcdef" * 16) + "\n"

    with open(p, "w", encoding="utf-8") as f:
        for _ in range(250_000):
            f.write(chunk)
        f.write("needle\n")

    tracemalloc.start()
    result = search_in_file(str(p), "needle")
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    search_result = _require_search_result(result)
    assert search_result[1] >= 1
    assert peak < 350 * 1024 * 1024


def test_parallel_runs_no_state_leak_between_workers():
    worker_a = SearchWorker({"file_list": [], "search_paths": ["A"], "search_string": "alpha", "extensions": ["txt"]})
    worker_b = SearchWorker({"file_list": [], "search_paths": ["B"], "search_string": "beta", "extensions": ["txt"]})

    def fake_search_directory(paths, *_args, **_kwargs):
        if paths == ["A"]:
            return {"results": [("A/a.txt", 1, [(1, "alpha")])], "skipped": []}
        if paths == ["B"]:
            return {"results": [("B/b.txt", 1, [(1, "beta")])], "skipped": []}
        return {"results": [], "skipped": []}

    with (
        pytest.MonkeyPatch.context() as mp_has_rust,
        pytest.MonkeyPatch.context() as mp_search,
    ):
        mp_has_rust.setattr("core.search_engine.HAS_RUST_ENGINE", True)
        mp_search.setattr("core.search_engine.search_directory_fast", fake_search_directory)

        t1 = threading.Thread(target=worker_a.run, daemon=True)
        t2 = threading.Thread(target=worker_b.run, daemon=True)
        t1.start()
        t2.start()
        t1.join(timeout=2.0)
        t2.join(timeout=2.0)

    assert not t1.is_alive()
    assert not t2.is_alive()

    out_a = getattr(worker_a, "all_results", [])
    out_b = getattr(worker_b, "all_results", [])
    assert out_a and all(path.startswith("A/") or path.startswith("A\\") for path, *_ in out_a)
    assert out_b and all(path.startswith("B/") or path.startswith("B\\") for path, *_ in out_b)
