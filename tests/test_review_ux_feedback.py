"""Exercise actual UI events and exclusion feedback without changing search policy."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from core import search_engine as se
from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants as C
from sf_utils.localization import get_language, set_language
from ui.panels import FilenameFilterPanel
from ui.search_tab import SearchTab
from ui.settings_dialog import SettingsDialog


@pytest.fixture(params=["ko", "en"])
def language(request):
    original = get_language()
    set_language(request.param)
    yield request.param
    set_language(original)


def test_rejected_typing_and_paste_show_inline_feedback(qtbot, language):
    panel = FilenameFilterPanel()
    qtbot.addWidget(panel)
    panel.show()
    qtbot.keyClicks(panel.add_edit, "report*")
    assert panel.add_edit.text() == "report"
    assert panel.filename_input_notice.isVisible()
    assert panel.filename_input_notice.text() == AppStrings.FILENAME_FILTER_WILDCARD_NOT_ALLOWED
    panel.add_edit.clear()
    QApplication.clipboard().setText("report?.txt")
    panel.add_edit.paste()
    assert panel.add_edit.text() == ""
    assert panel.filename_input_notice.isVisible()
    panel.add_edit.setText("report")
    panel._on_add_clicked()
    assert panel.get_selected_filenames() == ["report"]
    assert not panel.filename_input_notice.isVisible()
    assert not panel.add_edit.toolTip()


@pytest.mark.parametrize("editor", [False, True])
@pytest.mark.parametrize("success", [False, True])
def test_open_feedback(qtbot, mock_config_manager, monkeypatch, language, editor, success):
    tab = SearchTab(mock_config_manager)
    qtbot.addWidget(tab)
    messages = []
    tab.status_message_requested.connect(lambda text, duration: messages.append((text, duration)))
    if editor:
        monkeypatch.setattr("ui.search_tab.open_in_external_editor", lambda *a, **kw: success)
        tab._open_match_in_editor("missing.txt", 12)
    else:
        monkeypatch.setattr("sf_utils.file_helper.open_file", lambda *a: success)
        tab._open_file_from_view("missing.txt")
    assert messages == ([] if success else [(AppStrings.ERROR_OPEN_FILE.format("missing.txt"), 5000)])


def test_rejected_execution_does_not_report_open_failure(qtbot, mock_config_manager, monkeypatch):
    tab = SearchTab(mock_config_manager)
    qtbot.addWidget(tab)
    messages = []
    tab.status_message_requested.connect(lambda *a: messages.append(a))
    monkeypatch.setattr(tab, "_confirm_potentially_executable_open", lambda *a: False)
    opener = Mock(return_value=False)
    monkeypatch.setattr("sf_utils.file_helper.open_file", opener)
    tab._open_file_from_view("unsafe.exe")
    opener.assert_not_called()
    assert messages == []


@pytest.mark.parametrize("include", [False, True])
def test_selected_junction_both_paths(tmp_path, monkeypatch, language, include):
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(se.os.path, "isjunction", lambda path: str(path) == str(root))
    monkeypatch.setattr(se.sf_engine, "search_dir", lambda **kw: ([], []))
    with se.use_search_settings_snapshot({C.CONFIG_KEY_INCLUDE_JUNCTIONS: include}):
        native = se.search_directory_fast([str(root)], "needle")
        scanner = se.FileScanner([str(root)], [])
        assert scanner.scan() == []
    expected = [] if include else [(str(root), AppStrings.SKIP_ROOT_JUNCTION)]
    assert native["skipped"] == scanner.skipped == expected
    if expected:
        assert se.is_supported_skip_reason(expected[0][1])
        assert se.localize_skip_reason_for_display(expected[0][1]) == expected[0][1]


def test_junction_notice_survives_other_results(tmp_path, monkeypatch):
    root, normal = str(tmp_path / "link"), str(tmp_path / "normal")
    monkeypatch.setattr(se.os.path, "isjunction", lambda path: path == root)
    search = Mock(return_value=([("good.txt", [(1, "needle", None, None)])], [("bad.txt", "ERR_OPEN|denied")]))
    monkeypatch.setattr(se.sf_engine, "search_dir", search)
    with se.use_search_settings_snapshot({C.CONFIG_KEY_INCLUDE_JUNCTIONS: False}):
        result = se.search_directory_fast([root, normal, root], "needle")
    assert search.call_args.kwargs["root_paths"] == [normal]
    assert len(result["results"]) == 1
    assert len(result["skipped"]) == 2
    assert result["skipped"][0] == (root, AppStrings.SKIP_ROOT_JUNCTION)


def test_saved_junction_reason_follows_language(language):
    saved = AppStrings.SKIP_ROOT_JUNCTION
    set_language("en" if language == "ko" else "ko")
    assert se.is_supported_skip_reason(saved)
    assert se.localize_skip_reason_for_display(saved) == AppStrings.SKIP_ROOT_JUNCTION


@pytest.mark.parametrize("dismiss", [None, "escape", "button", "close"])
@pytest.mark.parametrize("success", [False, True])
def test_doctor_dismiss_and_completion(qtbot, mock_config_manager, monkeypatch, language, dismiss, success):
    callbacks = []
    monkeypatch.setattr("threading.Thread", lambda target, **kw: SimpleNamespace(start=lambda: callbacks.append(target)))
    run = Mock(return_value=success)
    monkeypatch.setattr("core.doctor.run_doctor_and_open", run)
    information, warning = Mock(), Mock()
    monkeypatch.setattr("ui.settings_dialog.QMessageBox.information", information)
    monkeypatch.setattr("ui.settings_dialog.QMessageBox.warning", warning)
    dialog = SettingsDialog(mock_config_manager)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog._run_system_doctor()
    dialog._run_system_doctor()
    assert len(callbacks) == 1
    assert not dialog.doctor_btn.isEnabled()
    progress = dialog._doctor_msg_box
    assert progress.labelText() == AppStrings.DOCTOR_WAIT_DESCRIPTION
    if dismiss:
        if dismiss == "escape":
            qtbot.keyClick(progress, Qt.Key.Key_Escape)
        elif dismiss == "close":
            progress.close()
        else:
            from PySide6.QtWidgets import QPushButton
            qtbot.mouseClick(progress.findChild(QPushButton), Qt.MouseButton.LeftButton)
        assert not progress.isVisible()
        assert dialog._doctor_wait_dismissed
        assert dialog._doctor_running
    callbacks[0]()
    assert dialog.doctor_btn.isEnabled()
    assert not dialog._doctor_running
    assert dialog._doctor_msg_box is None
    assert information.call_count == int(success and not dismiss)
    assert warning.call_count == int(not success and not dismiss)
    dialog._run_system_doctor()
    assert len(callbacks) == 2
    callbacks[1]()


def test_doctor_thread_start_failure_recovers(qtbot, mock_config_manager, monkeypatch):
    thread = SimpleNamespace(start=Mock(side_effect=RuntimeError("cannot start thread")))
    monkeypatch.setattr("threading.Thread", lambda **kw: thread)
    warning = Mock()
    monkeypatch.setattr("ui.settings_dialog.QMessageBox.warning", warning)
    dialog = SettingsDialog(mock_config_manager)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog._run_system_doctor()
    assert dialog.doctor_btn.isEnabled()
    assert not dialog._doctor_running
    assert dialog._doctor_msg_box is None
    warning.assert_called_once()


def test_doctor_real_thread_completion(qtbot, mock_config_manager, monkeypatch):
    monkeypatch.setattr("core.doctor.run_doctor_and_open", lambda: True)
    notice = Mock()
    monkeypatch.setattr("ui.settings_dialog.QMessageBox.information", notice)
    dialog = SettingsDialog(mock_config_manager)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog._run_system_doctor()
    qtbot.waitUntil(lambda: not dialog._doctor_running)
    assert dialog.doctor_btn.isEnabled()
    notice.assert_called_once()
