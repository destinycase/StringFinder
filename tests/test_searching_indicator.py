"""검색 중에는 결과가 먼저 나와도 진행 중임이 분명히 보이는지 확인합니다(7.1.11)."""
from PySide6.QtWidgets import QFileIconProvider

from sf_utils.app_strings import AppStrings
from ui.result_view import ResultView


def _view(qtbot, mock_config_manager):
    view = ResultView(QFileIconProvider(), mock_config_manager)
    qtbot.addWidget(view)
    view.show()
    return view


def test_searching_line_shows_checked_files_and_elapsed_without_results(qtbot, mock_config_manager):
    view = _view(qtbot, mock_config_manager)
    view.set_summary_info(0, 0, 75.4, state_prefix=AppStrings.SUMMARY_PREFIX_SEARCHING, scanned_count=1234)
    text = view.summary_label.text()
    assert view.summary_label.isVisible()
    assert text.startswith(AppStrings.SUMMARY_PREFIX_SEARCHING)
    spinner = view.search_spinner
    assert spinner.is_spinning() and spinner.isVisible()
    # 글자 기호가 아니라 고정 크기로 그리므로 돌아도 크기가 변하지 않습니다.
    size = spinner.size()
    angle = spinner._angle
    spinner._advance()
    assert spinner._angle != angle and spinner.size() == size
    assert "1,234" in text and "01:15" in text
    # 아래 막대 대신 요약 줄 안에서 빛이 좌→우로 흐릅니다(7.1.20).
    shimmer = view.search_shimmer
    assert shimmer.is_running() and shimmer.isVisible()
    assert shimmer.geometry() == view.summary_label.rect()
    phase = shimmer._phase
    shimmer._advance()
    assert shimmer._phase > phase
    assert not hasattr(view, "searching_bar")


def test_finished_line_hides_moving_bar(qtbot, mock_config_manager):
    view = _view(qtbot, mock_config_manager)
    view.set_summary_info(3, 5, 1.0, state_prefix=AppStrings.SUMMARY_PREFIX_SEARCHING, scanned_count=10)
    view.set_summary_info(3, 5, 2.5, state_prefix=AppStrings.SUMMARY_PREFIX_FINISHED)
    assert not view.search_shimmer.is_running()
    assert not view.search_spinner.is_spinning()
    assert view.summary_label.text().startswith(AppStrings.SUMMARY_PREFIX_FINISHED)


def test_clear_hides_moving_bar(qtbot, mock_config_manager):
    view = _view(qtbot, mock_config_manager)
    view.set_summary_info(0, 0, 1.0, state_prefix=AppStrings.SUMMARY_PREFIX_SEARCHING, scanned_count=1)
    view.clear()
    assert not view.search_shimmer.is_running() and not view.search_shimmer.isVisible()


def test_no_result_lists_conditions_as_entered(qtbot, mock_config_manager):
    view = _view(qtbot, mock_config_manager)
    folders = ["D:/a", "D:/b", "D:/c", "D:/d", "D:/e"]
    view.show_no_result_conditions("needle", "기본", folders, ["txt", "json"], ["String_", "Item_"])
    text = view.empty_label.text()
    assert "needle" in text
    assert "D:/a" in text and "D:/c" in text and "D:/d" not in text
    assert AppStrings.RESULT_COND_MORE_FOLDERS.format(2) in text
    assert "txt, json" in text
    assert "String_, Item_" in text and "String_*" not in text
    assert "NFD" not in text


def test_no_result_with_search_mode_shows_mode_scope_and_not_used(qtbot, mock_config_manager):
    view = _view(qtbot, mock_config_manager)
    view.show_no_result_conditions("<id>", "JSON (정확히 일치)", ["D:/a"], [], [], mode_extensions=["json"])
    text = view.empty_label.text()
    assert "&lt;id&gt;" in text  # 검색어는 글자 그대로 보입니다.
    assert AppStrings.RESULT_COND_MODE_EXTENSIONS.format(".json") in text
    assert AppStrings.RESULT_COND_NOT_USED in text
