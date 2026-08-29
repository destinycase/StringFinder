"""추가 UX 사양의 핵심 동작을 검증합니다."""

from unittest.mock import patch

import pytest

from sf_utils.constants import Constants
from ui.result_view import ResultView
from ui.search_tab import SearchTab


@pytest.fixture
def search_tab_fixture(qtbot, mock_config_manager):
    widget = SearchTab(mock_config_manager)
    qtbot.addWidget(widget)
    yield widget
    widget.close()
    widget.deleteLater()


def test_result_context_preview_is_bounded_and_highlights_match(search_tab_fixture, qtbot, tmp_path):
    file_path = tmp_path / "context.txt"
    file_path.write_text(
        "\n".join(["one", "two", "three", "four", "five", "needle", "seven", "eight", "nine", "ten", "eleven"]),
        encoding="utf-8",
    )
    panel = search_tab_fixture.result_view_panel
    panel.set_search_context("needle", Constants.MODE_NORMAL)
    panel.set_results([[1, file_path.name, str(tmp_path), str(file_path), [[6, "needle", 28, 6]]]])

    panel._on_result_clicked(panel.proxy_model.index(0, 0))
    panel._on_match_clicked(panel.match_proxy_model.index(0, 0))
    qtbot.waitUntil(lambda: "needle" in panel.context_preview.toPlainText(), timeout=2000)

    assert str(file_path) in panel.file_info_label.text()
    assert panel.file_info_header.height() <= 32
    assert panel.file_info_label.toolTip() == ""
    assert panel.open_file_btn.toolTip() == ""
    assert panel.open_folder_btn.toolTip() == ""
    assert "needle" in panel.context_preview.toPlainText()
    assert "▶" in panel.context_preview.toPlainText()
    assert panel.context_preview.extraSelections()
    assert "      1 | one" in panel.context_preview.toPlainText()
    assert "     11 | eleven" in panel.context_preview.toPlainText()


def test_context_preview_line_controls_are_configurable(search_tab_fixture, qtbot, tmp_path):
    file_path = tmp_path / "context-controls.txt"
    file_path.write_text(
        "\n".join(["one", "two", "three", "four", "five", "needle", "seven", "eight", "nine", "ten"]),
        encoding="utf-8",
    )
    panel = search_tab_fixture.result_view_panel
    panel.set_search_context("needle", Constants.MODE_NORMAL)
    panel.set_results([[1, file_path.name, str(tmp_path), str(file_path), [[6, "needle", 28, 6]]]])
    panel._on_result_clicked(panel.proxy_model.index(0, 0))
    panel._on_match_clicked(panel.match_proxy_model.index(0, 0))
    qtbot.waitUntil(lambda: "needle" in panel.context_preview.toPlainText(), timeout=2000)

    assert panel.context_before_combo.count() == 21
    assert panel.context_after_combo.count() == 21
    assert panel.context_before_combo.currentText() == "5"
    assert panel.context_after_combo.currentText() == "5"

    panel.context_before_combo.setCurrentText("2")
    panel.context_after_combo.setCurrentText("3")
    qtbot.waitUntil(lambda: "      4 | four" in panel.context_preview.toPlainText(), timeout=2000)
    preview = panel.context_preview.toPlainText()

    assert search_tab_fixture.config_manager.get(Constants.CONFIG_KEY_CONTEXT_BEFORE_LINES) == 2
    assert search_tab_fixture.config_manager.get(Constants.CONFIG_KEY_CONTEXT_AFTER_LINES) == 3
    assert "      3 | three" not in preview
    assert "      4 | four" in preview
    assert "      9 | nine" in preview
    assert "     10 | ten" not in preview

    restored_panel = ResultView(None, search_tab_fixture.config_manager)
    qtbot.addWidget(restored_panel)
    assert restored_panel.context_before_combo.currentText() == "2"
    assert restored_panel.context_after_combo.currentText() == "3"


def test_special_match_does_not_attempt_file_read(search_tab_fixture):
    panel = search_tab_fixture.result_view_panel
    panel.match_model.set_matches("missing.xlsx", [(0, "Sheet1", "A1", "needle")], "needle", "Excel")

    panel._on_match_clicked(panel.match_proxy_model.index(0, 0))

    assert "missing.xlsx" not in panel.context_preview.toPlainText()
    assert "A1" in panel.context_preview.toPlainText()


def test_selected_file_open_button_uses_default_file_handler(search_tab_fixture, tmp_path):
    panel = search_tab_fixture.result_view_panel
    file_path = tmp_path / "open.txt"
    panel.selected_file_path = str(file_path)

    with patch("sf_utils.file_helper.open_file") as open_file:
        panel.open_file_btn.click()

    open_file.assert_called_once_with(str(file_path))


def test_search_and_stop_buttons_have_no_description_tooltips(search_tab_fixture):
    search_panel = search_tab_fixture.search_panel
    result_panel = search_tab_fixture.result_view_panel
    assert search_panel.search_combo.toolTip() == ""
    assert search_panel.search_btn.toolTip() == ""
    assert search_panel.stop_btn.toolTip() == ""
    assert search_panel.complex_search_check.toolTip() == ""
    assert search_panel.boolean_search_check.toolTip() == ""
    assert result_panel.result_view.toolTip() == ""
    assert result_panel.match_view.toolTip() == ""
    assert result_panel.context_preview.toolTip() == ""
    assert search_tab_fixture.result_view_panel.match_pagination_widget.height() == 36


def test_ui_source_does_not_restore_tooltips():
    """사용자 UI에 설명/경로 호버 툴팁이 다시 추가되지 않도록 보장한다."""
    from pathlib import Path

    source_root = Path(__file__).resolve().parents[1] / "src" / "ui"
    ui_source = "\n".join(path.read_text(encoding="utf-8") for path in source_root.glob("*.py"))
    assert ".setToolTip(" not in ui_source
    assert "ItemDataRole.ToolTipRole" not in ui_source


def test_skipped_count_signal_is_updated(search_tab_fixture):
    received = []
    search_tab_fixture.skipped_count_updated.connect(received.append)

    search_tab_fixture._on_skipped_found([("a.txt", "permission denied")])

    assert search_tab_fixture.skipped_count == 1
    assert received == [1]
