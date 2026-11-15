"""Exercise actual UI events and exclusion feedback without changing search policy."""
from unittest.mock import Mock

import pytest

from core import search_engine as se
from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants as C
from sf_utils.localization import get_language, set_language
from ui.panels import FilenameFilterPanel
from ui.search_tab import SearchTab


@pytest.fixture(params=["ko", "en"])
def language(request):
    original = get_language()
    set_language(request.param)
    yield request.param
    set_language(original)


def test_rejected_typing_shows_inline_feedback(qtbot, language):
    panel = FilenameFilterPanel()
    qtbot.addWidget(panel)
    panel.show()
    qtbot.keyClicks(panel.add_edit, "report*")
    assert panel.add_edit.text() == "report"
    assert panel.filename_input_notice.isVisible()
    assert panel.filename_input_notice.text() == AppStrings.FILENAME_FILTER_WILDCARD_NOT_ALLOWED
    panel.add_edit.clear()
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
    assert result["skipped"][0] == (root, "INFO_ROOT_JUNCTION|")
    assert se.render_skip_reason(result["skipped"][0][1]) == AppStrings.SKIP_ROOT_JUNCTION


def test_saved_junction_reason_follows_language(language):
    saved = "INFO_ROOT_JUNCTION|"  # 사유는 코드로 저장하고 보여 줄 때 지금 언어로 만듭니다(7.2.5)
    set_language("en" if language == "ko" else "ko")
    assert se.render_skip_reason(saved) == AppStrings.SKIP_ROOT_JUNCTION
