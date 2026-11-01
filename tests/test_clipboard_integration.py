"""Real clipboard integration, deliberately separate from isolated copy tests."""
import pytest
from PySide6.QtWidgets import QApplication

from sf_utils.localization import get_language, set_language
from ui.panels import ExtensionFilterPanel, FilenameFilterPanel
from ui.result_view import SkippedFilesDialog

pytestmark = pytest.mark.os_clipboard


@pytest.fixture(params=["ko", "en"])
def language(request):
    previous = get_language()
    set_language(request.param)
    yield request.param
    set_language(previous)


@pytest.mark.parametrize("kind", ["extension", "filename"])
def test_real_paste_rejects_wildcards(qtbot, language, kind):
    panel = ExtensionFilterPanel() if kind == "extension" else FilenameFilterPanel()
    qtbot.addWidget(panel)
    panel.show()
    edit = panel.ext_edit if kind == "extension" else panel.add_edit
    notice = panel.extension_input_notice if kind == "extension" else panel.filename_input_notice
    value = "*.json" if kind == "extension" else "report?.txt"
    QApplication.clipboard().setText(value)
    qtbot.waitUntil(lambda: QApplication.clipboard().text() == value)
    edit.paste()  # Exercise Qt's real paste path; a Python clipboard mock cannot replace it.
    assert edit.text() == ""
    assert notice.isVisible()
    assert not edit.toolTip()


def test_real_copy_uses_complete_displayed_text(qtbot, language):
    dialog = SkippedFilesDialog([("C:/a.xml", "parse error")], 1)
    qtbot.addWidget(dialog)
    dialog.copy_button.click()
    qtbot.waitUntil(lambda: QApplication.clipboard().text() == dialog.list_edit.toPlainText())
    assert not dialog.copy_confirmation_label.isHidden()
