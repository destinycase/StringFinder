from PySide6.QtGui import QValidator

from sf_utils.constants import Constants
from ui.panels import FilenameFilterPanel


def test_filename_filter_input_rejects_glob_metacharacters(qtbot):
    panel = FilenameFilterPanel()
    qtbot.addWidget(panel)

    validator = panel.add_edit.validator()
    assert validator is not None
    for value in ("report*", "report?", "[report]", r"report\name"):
        state, _text, _position = validator.validate(value, len(value))
        assert state == QValidator.State.Invalid


def test_filename_filter_add_and_restore_reject_glob_metacharacters(qtbot):
    panel = FilenameFilterPanel()
    qtbot.addWidget(panel)

    assert panel.add_filename("report")
    for value in ("report*", "report?", "[report]", r"report\name"):
        assert not panel.add_filename(value)

    panel.restore_state(
        {
            Constants.CONFIG_KEY_FILENAMES: {
                "kept-name": True,
                "old*.txt": True,
                "old?.txt": True,
            }
        }
    )
    assert panel.get_selected_filenames() == ["kept-name"]


def test_add_action_warns_and_keeps_invalid_pasted_filter(qtbot, monkeypatch):
    panel = FilenameFilterPanel()
    qtbot.addWidget(panel)
    warnings = []
    monkeypatch.setattr(
        "ui.panels.QMessageBox.warning",
        lambda *args: warnings.append(args[-1]),
    )

    panel.add_edit.setText("report?.txt")
    panel._on_add_clicked()

    assert warnings
    assert panel.filename_list.count() == 0
    assert panel.add_edit.text() == "report?.txt"
