"""실제 파일 줄이 엔진 안내 표식처럼 생겨도 일치 줄로 나오는지 실제 엔진으로 확인합니다(7.2.9).

예전 엔진은 결과 종류를 줄 내용의 글자 모양으로 정해, ``ERR_…|``로 시작하는 줄이 일치하면 파일 전체가
"알 수 없는 오류로 건너뜀"으로 빠졌습니다. 지금은 결과를 만드는 곳에서 종류를 정합니다.
"""
import pytest

from core import search_engine
from core.search_engine import search_directory_fast
from sf_utils.constants import Constants

pytestmark = pytest.mark.skipif(not search_engine.HAS_RUST_ENGINE, reason="Rust engine required")

LINES = [
    "ERR_LOGIN_FAILED|로그인 needle 실패",
    "OK_LOGIN|needle",
    "plain needle",
    "__SF_TRUNCATED__ needle",
    "__SF_LONG_LINE__|needle",
    "__SF_BINARY_MATCH__|needle",
    "__SF_EXCEL_SHEET_ERR__|needle|x",
    "__SF_JSON_DEPTH_LIMIT__|needle",
    "__SF_DECODE_PARTIAL__|needle",
]


@pytest.mark.parametrize("encoding", ["utf-8", "cp949"])
def test_marker_like_lines_are_matches(tmp_path, encoding):
    path = tmp_path / "codes.txt"
    path.write_text("\n".join(LINES) + "\n", encoding=encoding)

    result = search_directory_fast([str(tmp_path)], "needle")

    assert result["skipped"] == []
    assert len(result["results"]) == 1
    file_path, count, rows = result["results"][0]
    assert file_path == str(path)
    assert count == len(LINES)
    assert [row[0] for row in rows] == list(range(1, len(LINES) + 1))
    assert [row[1] for row in rows] == LINES


def test_marker_like_line_keeps_match_position(tmp_path):
    path = tmp_path / "pos.txt"
    path.write_text("__SF_LONG_LINE__|needle\n", encoding="utf-8")

    rows = search_directory_fast([str(tmp_path)], "needle")["results"][0][2]

    # 짧은 줄은 긴 줄 미리보기가 아니므로 일치 위치를 잃지 않습니다.
    assert rows[0][2] == len("__SF_LONG_LINE__|") and rows[0][3] == len("needle")


def test_marker_like_exact_line_is_a_match(tmp_path):
    path = tmp_path / "exact.txt"
    path.write_text("ERR_A|needle\nother\n", encoding="utf-8")

    result = search_directory_fast([str(tmp_path)], "ERR_A|needle", special_mode=Constants.MODE_EXACT)

    assert result["skipped"] == []
    assert [row[1] for row in result["results"][0][2]] == ["ERR_A|needle"]


def test_real_notices_still_arrive(tmp_path):
    """진짜 안내(파일당 결과 수 제한)는 그대로 옵니다."""
    path = tmp_path / "many.txt"
    path.write_text("needle\n" * 5, encoding="utf-8")

    with search_engine.use_search_settings_snapshot({Constants.CONFIG_KEY_MAX_PER_FILE_MATCHES: 2}):
        result = search_directory_fast([str(tmp_path)], "needle")

    assert result["results"][0][1] == 2
    assert [p for p, _ in result["skipped"]] == [str(path)]
    assert result["skipped"][0][1].startswith("INFO_FILE_MATCH_LIMIT|")
