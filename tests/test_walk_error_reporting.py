"""Traversal failures retain their identity through UI and session normalization."""

import pytest

from core.skip_reason_codes import UNKNOWN_WALK_PATH_PREFIX
from sf_utils.app_strings import AppStrings
from sf_utils.localization import get_language, set_language
from ui.result_view import SkippedFilesDialog, _format_skipped_files_text, normalize_skipped_files


@pytest.mark.parametrize("language", ["ko", "en"])
def test_unknown_walk_errors_are_not_merged(language, qtbot):
    previous = get_language()
    try:
        set_language(language)
        entries = normalize_skipped_files([("walker error", "ERR_WALK|I/O failure")] * 27)
        assert len(entries) == 27
        assert normalize_skipped_files(entries, strict_session=True) == entries
        dialog = SkippedFilesDialog(entries, 27)
        qtbot.addWidget(dialog)
        text = dialog.list_text
        assert "walker error" not in text
        assert UNKNOWN_WALK_PATH_PREFIX not in text
        assert AppStrings.SKIPPED_FILES_DETAILS_MISSING.format(26) not in text
        assert text.count(AppStrings.SKIPPED_WALK_PATH_UNKNOWN) == 27
        assert dialog.windowTitle() == AppStrings.SKIPPED_ITEMS_DIALOG_TITLE
        dialog.copy_to_clipboard()
        from PySide6.QtWidgets import QApplication
        assert QApplication.clipboard().text() == text
    finally:
        set_language(previous)


def test_known_paths_and_unknown_event_identity():
    entries = normalize_skipped_files([
        ("D:/restricted", "ERR_WALK|permission denied"),
        (UNKNOWN_WALK_PATH_PREFIX + "42", "ERR_WALK|unknown"),
        (UNKNOWN_WALK_PATH_PREFIX + "42", "ERR_WALK|unknown"),
        ("D:/file.txt", "ERR_OPEN|not found"),
    ])
    assert len(entries) == 3
    text = _format_skipped_files_text(entries, 3)
    assert AppStrings.SKIPPED_FILES_AND_WALK_ERRORS.format(1, 2) in text
    assert AppStrings.SKIPPED_WALK_ENTRY.format("D:/restricted") in text
    assert "D:/file.txt" in text


def test_walk_failure_banner_and_accumulation(qtbot, mock_config_manager):
    from ui.search_tab import SearchTab

    tab = SearchTab(mock_config_manager)
    qtbot.addWidget(tab)
    tab._on_skipped_found([("walker error", "ERR_WALK|failure")] * 26)
    tab._on_skipped_found([("walker error", "ERR_WALK|failure")])
    entries = tab.skipped_files_list
    assert len(entries) == 27
    view = tab.result_view_panel
    view.set_skipped_files(entries, 27)
    assert view.skipped_files_label.text() == AppStrings.SKIPPED_ITEMS_COUNT.format(27)
    assert view.skipped_files_button.text() == AppStrings.SKIPPED_ITEMS_VIEW_BUTTON
    view.set_skipped_files([("D:/file.txt", "ERR_OPEN|failure")], 1)
    assert view.skipped_files_label.text() == AppStrings.SKIPPED_FILES_COUNT.format(1)
    assert view.skipped_files_button.text() == AppStrings.SKIPPED_FILES_VIEW_BUTTON


@pytest.mark.parametrize("code,resource", [
    (2, "SKIP_DETAIL_FILE_NOT_FOUND"), (3, "SKIP_DETAIL_FILE_NOT_FOUND"),
    (5, "SKIP_DETAIL_PERMISSION_DENIED"), (32, "SKIP_DETAIL_FILE_IN_USE"),
    (206, "SKIP_DETAIL_PATH_TOO_LONG"),
])
def test_windows_walk_error_codes_are_localized(code, resource):
    from core.search_engine import format_skip_reason
    import os
    if os.name != "nt":
        pytest.skip("Windows error codes")
    assert getattr(AppStrings, resource) in format_skip_reason(f"ERR_WALK|folder (os error {code})")


@pytest.mark.parametrize("operation", ["search_dir", "find_files_with_keyword"])
def test_native_traversal_failures_preserve_missing_root_paths(operation, tmp_path):
    from core import search_engine

    if not search_engine.HAS_RUST_ENGINE:
        pytest.skip("compiled Rust engine is unavailable")
    missing = [str(tmp_path / "missing-a"), str(tmp_path / "missing-b")]
    result = getattr(search_engine.sf_engine, operation)(missing, "needle")
    skipped = result["skipped"] if isinstance(result, dict) else result[1]
    assert {path for path, reason in skipped} == set(missing)
    assert all(reason.startswith("ERR_WALK|") for path, reason in skipped)
    entries = normalize_skipped_files(skipped)
    assert len(entries) == 2
    assert all(AppStrings.SKIP_DETAIL_FILE_NOT_FOUND in reason for path, reason in entries)
