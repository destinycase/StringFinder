"""정리 후 실제 기록·필터 경로와 실패 시 진단 계약을 보존합니다."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PySide6.QtCore import QObject, Signal
from shiboken6 import delete

from core.worker import SearchWorker
from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants
from ui.panels import FilenameFilterPanel
from ui.result_view import ResultView


class SignalSource(QObject):
    message = Signal(str)


@pytest.mark.parametrize("error", [ValueError("bad emission"), RuntimeError("unexpected runtime failure")])
def test_emission_failures_are_logged_and_do_not_stop_worker(error):
    signal = Mock(emit=Mock(side_effect=error))
    with patch("core.worker.logger") as logger:
        SearchWorker._safe_emit(object(), signal, "payload")
    logger.warning.assert_called_once()
    assert str(error) in logger.warning.call_args.args[0]
    assert logger.warning.call_args.kwargs["exc_info"] is True


def test_deleted_qt_sender_is_expected_during_shutdown(qapp):
    source = SignalSource()
    signal = source.message
    delete(source)
    with patch("core.worker.logger") as logger:
        SearchWorker._safe_emit(object(), signal, "payload")
    logger.warning.assert_not_called()


def test_logging_failure_does_not_escape_safe_emission():
    with patch("core.worker.logger.warning", side_effect=RuntimeError("deleted log receiver")):
        SearchWorker._safe_emit(object(), Mock(emit=Mock(side_effect=ValueError("emit failure"))), "payload")


def test_qt_slot_errors_are_not_misreported_as_emission_failures(qapp):
    source = SignalSource()
    delivered = []

    def fail(_text):
        raise ValueError("receiver failure")

    source.message.connect(fail)
    source.message.connect(delivered.append)
    with patch("sys.excepthook") as hook, patch("core.worker.logger") as logger:
        SearchWorker._safe_emit(object(), source.message, "payload")
    hook.assert_called_once()
    assert hook.call_args.args[0] is ValueError
    assert delivered == ["payload"]
    logger.warning.assert_not_called()


@pytest.mark.parametrize("failure", [False, ValueError("invalid splitter state")])
def test_splitter_restore_failure_keeps_default_and_records_debug(failure):
    holder = SimpleNamespace(
        config_manager=Mock(get_splitter_states=Mock(return_value=(None, "abcd", None))),
        result_splitter=Mock(),
        _restore_column_widths=Mock(),
    )
    if isinstance(failure, Exception):
        holder.result_splitter.restoreState.side_effect = failure
    else:
        holder.result_splitter.restoreState.return_value = failure
    with patch("ui.result_view.logger") as logger:
        ResultView._restore_states(holder)
    logger.debug.assert_called_once()
    assert logger.debug.call_args.args[0] == AppStrings.LOG_UI_RESULT_LAYOUT_DEFAULT
    assert holder._restore_column_widths.call_count == 2


def test_legacy_filename_history_does_not_replace_filters(qtbot, mock_config_manager):
    manager = mock_config_manager
    manager.set(Constants.CONFIG_KEY_FILENAME_HISTORY, ["old-history", "not-a-filter"])
    manager.add_history("first")
    manager.add_history("second")
    manager.add_history("first")
    assert manager.save_immediately()
    loaded = manager._load()
    assert loaded[Constants.CONFIG_KEY_HISTORY] == ["first", "second"]
    panel = FilenameFilterPanel()
    qtbot.addWidget(panel)
    panel.load_state({Constants.CONFIG_KEY_FILENAMES: {}, Constants.PAYLOAD_FILENAME_FILTER: "report,invoice"})
    assert panel.get_selected_filenames() == ["report", "invoice"]
    assert panel.get_state()[Constants.PAYLOAD_FILENAME_FILTER] == ""
