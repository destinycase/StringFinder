"""외부 편집기 연결과 문맥 미리보기 구문 강조 회귀 테스트."""

from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QPlainTextEdit

from sf_utils.constants import Constants
from sf_utils.file_helper import open_in_external_editor
from ui.settings_dialog import SettingsDialog
from ui.search_tab import SearchTab
from ui.syntax_highlighter import LightweightSyntaxHighlighter


@pytest.fixture
def search_tab_fixture(qtbot, mock_config_manager):
    widget = SearchTab(mock_config_manager)
    qtbot.addWidget(widget)
    yield widget
    widget.close()
    widget.deleteLater()


def test_external_editor_system_default_delegates_to_file_handler(tmp_path):
    file_path = str(tmp_path / "sample.py")
    with patch("sf_utils.file_helper.open_file", return_value=True) as open_file:
        assert open_in_external_editor(file_path, 12, {"editor_type": "system"}) is True
    open_file.assert_called_once_with(file_path)


def test_external_editor_vscode_receives_file_and_line(tmp_path):
    file_path = str(tmp_path / "sample.py")
    with (
        patch("sf_utils.file_helper.shutil.which", return_value="C:/Tools/code.cmd"),
        patch("sf_utils.file_helper.subprocess.Popen") as popen,
    ):
        assert open_in_external_editor(file_path, 12, {"editor_type": "vscode"}) is True
    popen.assert_called_once_with(["C:/Tools/code.cmd", "--goto", f"{file_path}:12"], shell=False)


def test_external_editor_missing_program_falls_back_to_system_default(tmp_path):
    file_path = str(tmp_path / "sample.py")
    with (
        patch("sf_utils.file_helper.shutil.which", return_value=None),
        patch("sf_utils.file_helper.open_file", return_value=True) as open_file,
    ):
        assert open_in_external_editor(file_path, 12, {"editor_type": "vscode"}) is True
    open_file.assert_called_once_with(file_path)


def test_settings_dialog_external_editor_defaults_to_system(qtbot, mock_config_manager):
    dialog = SettingsDialog(mock_config_manager)
    qtbot.addWidget(dialog)

    assert dialog.external_editor_combo.currentData() == Constants.DEFAULT_EXTERNAL_EDITOR
    assert not dialog.external_editor_path_edit.isEnabled()

    dialog.external_editor_combo.setCurrentIndex(dialog.external_editor_combo.findData("vscode"))
    assert mock_config_manager.get(Constants.CONFIG_KEY_EXTERNAL_EDITOR)[Constants.CONFIG_KEY_EDITOR_TYPE] == "vscode"


def test_search_tab_passes_match_location_to_configured_editor(search_tab_fixture):
    with patch("ui.search_tab.open_in_external_editor") as open_editor:
        search_tab_fixture._open_match_in_editor("C:/data/sample.py", 27)

    open_editor.assert_called_once_with(
        "C:/data/sample.py",
        27,
        search_tab_fixture.config_manager.get(Constants.CONFIG_KEY_EXTERNAL_EDITOR, {}),
    )


def test_syntax_highlighter_maps_supported_file_types():
    assert LightweightSyntaxHighlighter.language_for_path("data.json") == "json"
    assert LightweightSyntaxHighlighter.language_for_path("layout.XML") == "xml"
    assert LightweightSyntaxHighlighter.language_for_path("script.py") == "python"
    assert LightweightSyntaxHighlighter.language_for_path("app.js") == "javascript"
    assert LightweightSyntaxHighlighter.language_for_path("server.log") == "log"
    assert LightweightSyntaxHighlighter.language_for_path("README.md") == "text"


def test_syntax_highlighter_applies_formats_to_context_document(qtbot):
    editor = QPlainTextEdit()
    qtbot.addWidget(editor)
    highlighter = LightweightSyntaxHighlighter(editor.document(), "json", is_dark_mode=False)
    editor.setPlainText('{"name": "StringFinder", "count": 5}')
    highlighter.rehighlight()

    formats = editor.document().firstBlock().layout().formats()
    assert formats
    assert any(item.format.foreground().color().isValid() for item in formats)
