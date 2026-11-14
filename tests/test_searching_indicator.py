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
    assert "1,234" in text and "01:15" in text
    assert view.searching_bar.isVisible()
    assert view.searching_bar.maximum() == 0  # 진행률 없이 움직이기만 합니다.


def test_finished_line_hides_moving_bar(qtbot, mock_config_manager):
    view = _view(qtbot, mock_config_manager)
    view.set_summary_info(3, 5, 1.0, state_prefix=AppStrings.SUMMARY_PREFIX_SEARCHING, scanned_count=10)
    view.set_summary_info(3, 5, 2.5, state_prefix=AppStrings.SUMMARY_PREFIX_FINISHED)
    assert not view.searching_bar.isVisible()
    assert view.summary_label.text().startswith(AppStrings.SUMMARY_PREFIX_FINISHED)


def test_clear_hides_moving_bar(qtbot, mock_config_manager):
    view = _view(qtbot, mock_config_manager)
    view.set_summary_info(0, 0, 1.0, state_prefix=AppStrings.SUMMARY_PREFIX_SEARCHING, scanned_count=1)
    view.clear()
    assert not view.searching_bar.isVisible()
