"""Rust 엔진 오류는 다른 검색 경로로 넘기지 않고 오류로 알립니다.

- 파일 하나 검색 중 엔진 예외는 그대로 올라갑니다(워커가 검색 실패로 표시).
- 폴더 검색 중 엔진 예외는 워커의 오류 신호로 끝납니다.
"""

from unittest.mock import patch
import pytest

from folder_search_helper import fake_dir_engine, search_in_file



def test_single_file_engine_error_is_raised(tmp_path):
    """파일 하나 검색 중 엔진 예외는 그대로 알립니다."""
    test_file = tmp_path / "rust_error.txt"
    test_file.write_text("dummy content", encoding="utf-8")

    with patch("core.search_engine.sf_engine", fake_dir_engine(error=RuntimeError("Rust Engine Crash"))):
        with pytest.raises(RuntimeError, match="Rust Engine Crash"):
            search_in_file(str(test_file), "dummy")



def test_directory_engine_error_ends_with_error_signal(tmp_path):
    """폴더 검색 중 엔진 예외는 오류 신호로 끝납니다."""
    from core.worker import SearchWorker

    worker = SearchWorker(
        {
            "search_paths": [str(tmp_path)],
            "extensions": ["txt"],
            "filename_filter": None,
            "search_string": "test",
        }
    )

    # Rust 엔진이 있지만 스캔 함수에서 에러가 나는 상황 모킹
    with patch("core.search_engine.HAS_RUST_ENGINE", True):
        with patch("core.search_engine.search_directory_fast", side_effect=Exception("Scan Crash")):
            error_signals = []
            worker.signals.error.connect(lambda msg: error_signals.append(msg))

            worker.run()

            assert len(error_signals) > 0
