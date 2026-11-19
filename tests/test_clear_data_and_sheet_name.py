"""7.3.4: "모든 데이터 삭제"가 불러오기 전 백업까지 지우는지, `|`가 든 시트 이름이 잘리지 않는지 확인합니다."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core import search_engine  # noqa: E402
from folder_search_helper import FakeSearchMatch  # noqa: E402


def _sheet_error(detail):
    return FakeSearchMatch(0, "__SF_EXCEL_SHEET_ERR__|" + detail)


def test_clear_all_data_removes_import_backups_and_title_cache(mock_config_manager):
    backup = os.path.join(mock_config_manager.config_dir, "config_backup_before_import_20261119_000000.json")
    with open(backup, "w", encoding="utf-8") as handle:
        handle.write("{}")
    mock_config_manager._session_title_cache = {"x": ((0, 0), "title")}

    mock_config_manager.clear_all_data()

    assert not os.path.exists(backup)
    assert "_session_title_cache" not in mock_config_manager.__dict__


def test_sheet_name_with_pipe_is_kept_whole():
    _, _, sheet_skips = search_engine._normalize_rust_matches([_sheet_error("A|B|parse failure")], "excel")
    assert sheet_skips[0][0] == "A|B"
    reason = search_engine._extract_partial_skip_reason([_sheet_error("A|B|parse failure")])
    assert "A|B" in (reason or "")


def test_sheet_error_without_separator_keeps_whole_text_as_sheet_name():
    assert search_engine._split_sheet_error("Sheet1") == ("Sheet1", "")
    assert search_engine._split_sheet_error(None) == ("", "")
