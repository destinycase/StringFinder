from unittest.mock import patch
from core.search_engine import search_in_file
from sf_utils.constants import Constants
from sf_utils.app_strings import AppStrings

"""
[test_engine_policy_enforcement.py]

이 테스트는 StringFinder의 강화된 엔진 정책을 명시적으로 검증합니다.

- 정책 1: Rust 엔진 오류 시 Python으로 자동 폴백하지 않음.
- 정책 2: '특별한 문자열 검색' 옵션이 없을 경우 Python 엔진 구동 차단.
- 정책 3: 엔진 오류 시 검색 중단 대신 해당 파일 '스킵' 처리.
"""


def test_policy_no_python_fallback_on_rust_error(tmp_path):
    """정책 검증: Rust 엔진 오류 시 Python으로 폴백하지 않고 STATUS_SKIPPED 반환"""
    test_file = tmp_path / "rust_error.txt"
    test_file.write_text("dummy content", encoding="utf-8")

    # 1. Rust 엔진은 존재하지만 작동 중 예외가 발생하는 상황 시뮬레이션
    with patch("core.search_engine.HAS_RUST_ENGINE", True):
        with patch("core.search_engine.sf_engine.search_file", side_effect=RuntimeError("Rust Engine Crash")):
            # 2. search_in_file 호출 (복합 검색 아님)
            result = search_in_file(str(test_file), "dummy", use_complex_search=False)

            # 3. 정책에 따라 STATUS_SKIPPED(2-tuple)가 반환되어야 함 (Python으로 넘어가지 않음)
            assert result is not None
            assert isinstance(result, tuple)
            assert len(result) == 2
            assert result[0] == Constants.STATUS_SKIPPED
            assert AppStrings.ERROR_TITLE in result[1] or "오류" in result[1]


def test_policy_python_restricted_to_complex_mode(tmp_path):
    """정책 검증: use_complex_search=False 일 때 HAS_RUST_ENGINE=False 라면 None 반환 (자동 폴백 금지)"""
    test_file = tmp_path / "policy_test.txt"
    test_file.write_text("target keyword", encoding="utf-8")

    # 1. Rust 엔진이 없는 환경 모킹
    with patch("core.search_engine.HAS_RUST_ENGINE", False):
        # 2. 복합 검색 옵션 없이 호출
        result = search_in_file(str(test_file), "target", use_complex_search=False)

        # 3. 정책에 따라 Python 엔진으로 넘어가지 않고 None 반환
        assert result is None


def test_policy_python_allowed_in_complex_mode(tmp_path):
    """정책 검증: use_complex_search=True 일 때는 Python 엔진 구동 허용"""
    test_file = tmp_path / "complex_mode.txt"
    test_file.write_text("Special Unicode: \u2122", encoding="utf-8")

    # 1. Rust 엔진이 없는 환경 모킹
    with patch("core.search_engine.HAS_RUST_ENGINE", False):
        # 2. 복합 검색 옵션을 켜고 호출
        result = search_in_file(str(test_file), "Special", use_complex_search=True)

        # 3. Python 엔진이 구동되어 결과를 찾아야 함
        assert result is not None
        assert isinstance(result, tuple)
        assert len(result) == 3
        assert result[1] == 1  # 매치 카운트


def test_policy_directory_scan_no_fallback(tmp_path):
    """정책 검증: 디렉토리 스캔 중 Rust 오류 발생 시 Python으로 폴백하지 않음"""
    from core.worker import ScanWorker

    worker = ScanWorker(
        selected_folders=[str(tmp_path)],
        selected_exts=["txt"],
        filename_filter=None,
        search_string="test",
        use_complex_search=False,
    )

    # Rust 엔진이 있지만 스캔 함수에서 에러가 나는 상황 모킹
    with patch("core.search_engine.HAS_RUST_ENGINE", True):
        with patch("core.search_engine.find_files_with_keyword_fast", side_effect=Exception("Scan Crash")):
            # worker.run()에서 Python 스캔으로 전환되지 않고 에러 신호를 보내야 함
            error_signals = []
            worker.signals.error.connect(lambda msg: error_signals.append(msg))

            worker.run()

            # Python 스캔(_python_scan)이 호출되지 않았음을 간접적으로 확인 (또는 직접 모킹 확인)
            assert len(error_signals) > 0
            # 만약 폴백이 일어났다면 error_signals 없이 결과가 나왔을 것임
