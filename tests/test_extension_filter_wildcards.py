"""Extension filters reject patterns, while content queries remain literal."""
import pytest
from PySide6.QtGui import QValidator
from PySide6.QtWidgets import QApplication

from core import search_engine as se
from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants as C
from sf_utils.localization import get_language, set_language
from ui.panels import ExtensionFilterPanel, SearchOptionsPanel


@pytest.fixture(params=["ko", "en"])
def panel(qtbot, request):
    original = get_language()
    set_language(request.param)
    widget = ExtensionFilterPanel()
    qtbot.addWidget(widget)
    widget.show()
    yield widget
    set_language(original)


@pytest.mark.parametrize("pattern", ["*.json", "j?on", "[json]", r"folder\json"])
def test_patterns_cannot_be_added_or_restored(panel, pattern):
    state, _, _ = panel.ext_edit.validator().validate(pattern, len(pattern))
    assert state == QValidator.State.Invalid
    assert not panel.add_extension(pattern)
    assert panel.ext_list.count() == 0
    assert panel.extension_input_notice.isVisible()
    assert panel.extension_input_notice.text() == AppStrings.EXTENSION_FILTER_WILDCARD_NOT_ALLOWED
    panel.restore_state({C.CONFIG_KEY_EXTENSIONS: {"json": True, pattern: True, "xml": False}})
    assert panel.get_selected_extensions() == ["json"]
    assert panel.get_state()[C.CONFIG_KEY_EXTENSIONS] == {"json": True, "xml": False}
    assert panel.extension_input_notice.isVisible()


def test_actual_typing_and_paste_show_notice(panel, qtbot):
    qtbot.keyClicks(panel.ext_edit, "json*")
    assert panel.ext_edit.text() == "json"
    assert panel.extension_input_notice.isVisible()
    panel.ext_edit.clear()
    QApplication.clipboard().setText("*.json")
    panel.ext_edit.paste()
    assert panel.ext_edit.text() == ""
    assert panel.extension_input_notice.isVisible()
    assert not panel.ext_edit.toolTip()


def test_programmatic_invalid_input_remains_for_correction(panel):
    panel.ext_edit.setText("*.json")
    panel._on_add_clicked()
    assert panel.ext_edit.text() == "*.json"
    assert panel.ext_list.count() == 0
    assert panel.extension_input_notice.isVisible()


@pytest.mark.parametrize("text", ["json", ".json", " .JSON "])
def test_valid_extension_and_duplicate_reset_notice(panel, text):
    panel.add_extension("*.json")
    panel.ext_edit.setText(text)
    panel._on_add_clicked()
    assert panel.get_selected_extensions() == ["json"]
    assert panel.ext_edit.text() == ""
    assert not panel.extension_input_notice.isVisible()
    panel.ext_edit.setText(text)
    panel._on_add_clicked()
    assert panel.ext_list.count() == 1


def test_clean_restore_clears_old_notice(panel):
    panel.add_extension("*.txt")
    panel.restore_state(["txt", "json"])
    assert panel.get_selected_extensions() == ["txt", "json"]
    assert not panel.extension_input_notice.isVisible()


@pytest.mark.parametrize("query", ["*", "?", "[text]", r"folder\file"])
@pytest.mark.parametrize("precise", [False, True])
def test_search_text_special_characters_still_literal(tmp_path, qtbot, query, precise):
    panel = SearchOptionsPanel()
    qtbot.addWidget(panel)
    panel.search_combo.setEditText(query)
    assert panel.get_search_text() == query
    file = tmp_path / "literal.txt"
    file.write_text("prefix " + query + " suffix\n", encoding="utf-8")
    result = se.search_in_file(str(file), query, use_complex_search=precise)
    assert result and result[0] == str(file) and result[1] == 1
