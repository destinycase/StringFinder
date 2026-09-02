import json
import os
import re
import string
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from core.search_engine import (
    format_excel_panic_reason,
    format_skip_reason,
    localize_skip_reason_for_display,
)
from sf_utils.app_strings import AppStrings
from sf_utils.english_strings import ENGLISH_STRINGS
from sf_utils.localization import (
    get_korean_strings,
    get_language,
    load_saved_language,
    set_language,
)
from ui.panels import ExtensionFilterPanel
from ui.settings_dialog import SettingsDialog


@pytest.fixture(autouse=True)
def restore_active_language():
    original_language = get_language()
    yield
    set_language(original_language)


def _format_signature(value):
    formatter = string.Formatter()
    values = value if isinstance(value, (list, tuple)) else [value]
    return [
        [(field_name, format_spec, conversion) for _, field_name, format_spec, conversion in formatter.parse(item) if field_name is not None]
        for item in values
    ]


def test_english_catalog_covers_all_korean_resources_and_preserves_placeholders():
    korean_strings = get_korean_strings()
    localized_names = {
        name
        for name, value in korean_strings.items()
        if re.search(r"[가-힣]", str(value))
    }

    assert localized_names == set(ENGLISH_STRINGS)
    for name, english_value in ENGLISH_STRINGS.items():
        assert _format_signature(korean_strings[name]) == _format_signature(english_value)


def test_language_switch_changes_ui_and_skip_reason_resources():
    set_language("en-US")

    assert AppStrings.SEARCH_BTN == "Search"
    assert AppStrings.SETTINGS_TITLE == "Settings"
    english_reason = format_skip_reason("ERR_OPEN|Access denied")
    assert english_reason.startswith("[Error]")
    assert "The operating system denied access." in english_reason
    assert get_language() == "en"

    set_language("ko")

    assert AppStrings.SEARCH_BTN == "검색"
    assert AppStrings.SETTINGS_TITLE == "설정"
    korean_reason = format_skip_reason("ERR_OPEN|Access denied")
    assert korean_reason.startswith("[오류]")
    assert "운영체제가 접근을 허용하지 않았습니다." in korean_reason
    assert "Access denied" not in korean_reason


def test_parser_and_engine_skip_details_follow_active_language():
    raw_json = "expected value at line 2 column 7"

    set_language("ko")
    korean_json = format_skip_reason(f"ERR_JSON_PARSE|{raw_json}")
    korean_panic = format_skip_reason("ERR_PANIC|Simulated engine crash")
    assert "JSON 문서 형식이 올바르지 않습니다. (2행, 7열)" in korean_json
    assert raw_json not in korean_json
    assert "Simulated engine crash" not in korean_panic

    set_language("en")
    english_json = format_skip_reason(f"ERR_JSON_PARSE|{raw_json}")
    english_panic = format_skip_reason("ERR_PANIC|Simulated engine crash")
    assert "The JSON document format is invalid. (line 2, column 7)" in english_json
    assert "치명" not in english_panic


def test_saved_skip_reason_is_rendered_in_the_current_language():
    set_language("ko")
    saved_korean = format_skip_reason("ERR_OPEN|Access denied")

    set_language("en")
    rendered_english = localize_skip_reason_for_display(saved_korean)
    assert rendered_english.startswith("[Error]")
    assert not re.search(r"[가-힣]", rendered_english)

    set_language("ko")
    rendered_korean = localize_skip_reason_for_display(rendered_english)
    assert rendered_korean.startswith("[오류]")
    assert "The operating system" not in rendered_korean


def test_excel_panic_reason_is_localized_without_raw_engine_text():
    raw_detail = "xls|range start index 6 out of range for slice of length 4"

    set_language("ko")
    korean_reason = format_excel_panic_reason(raw_detail)
    assert korean_reason == AppStrings.ERROR_EXCEL_PANIC.format(
        AppStrings.EXCEL_DETAIL_RANGE_OUT_OF_BOUNDS.format("XLS", "6", "4")
    )
    assert "range start index" not in korean_reason

    set_language("en")
    english_reason = format_excel_panic_reason(raw_detail)
    assert english_reason == AppStrings.ERROR_EXCEL_PANIC.format(
        AppStrings.EXCEL_DETAIL_RANGE_OUT_OF_BOUNDS.format("XLS", "6", "4")
    )


def test_saved_language_is_loaded_before_ui_imports(tmp_path, monkeypatch):
    config_dir = tmp_path / "StringFinder"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps({"language": "en"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("APPDATA", str(tmp_path))

    assert load_saved_language() == "en"


def test_clean_process_applies_saved_english_before_ui_modules(tmp_path):
    config_dir = tmp_path / "StringFinder"
    config_dir.mkdir()
    (config_dir / "config.json").write_text('{"language": "en"}', encoding="utf-8")
    environment = os.environ.copy()
    environment["APPDATA"] = str(tmp_path)
    project_root = Path(__file__).resolve().parents[1]
    environment["PYTHONPATH"] = str(project_root / "src")
    script = """
import sys
import sf_main
assert 'sf_utils.constants' not in sys.modules
assert sf_main.apply_saved_language() == 'en'
from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants
from core.search_engine import format_excel_panic_reason
assert AppStrings.SEARCH_BTN == 'Search'
assert Constants.MODE_EXACT == 'exact'
reason = format_excel_panic_reason('xls|range start index 6 out of range for slice of length 4')
assert reason.startswith('[Error]')
assert 'range start index' not in reason
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(project_root / "src"),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_language_can_be_changed_in_settings(qtbot, mock_config_manager):
    dialog = SettingsDialog(mock_config_manager)
    qtbot.addWidget(dialog)
    english_index = dialog.language_combo.findData("en")

    with patch("ui.settings_dialog.QMessageBox.information") as information:
        dialog.language_combo.setCurrentIndex(english_index)

    assert mock_config_manager.get_language() == "en"
    information.assert_called_once_with(
        dialog,
        AppStrings.INFO_TITLE,
        AppStrings.LANGUAGE_RESTART_REQUIRED,
    )


def test_legacy_korean_special_mode_is_restored_in_english(qtbot):
    set_language("en")
    panel = ExtensionFilterPanel()
    qtbot.addWidget(panel)

    panel.load_state({"special_mode": "XML (정확히 일치)"})

    assert panel.get_special_mode() == "XML (exact)"
