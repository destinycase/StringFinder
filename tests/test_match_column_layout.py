"""검색 상세 열 폭 배분: 표 폭에 맞춤, 미리보기 창 크기에 따라 다시 나눔, 직접 끈 비율 유지(7.5.4)."""
import pytest
from PySide6.QtCore import QItemSelectionModel, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileIconProvider

from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants as C
from ui.result_view import ResultView

LONG_SOURCE = "Confirm and proceed to the next stage of the adventure " * 6
LONG_TRANSLATION = "확인 후 다음 모험 단계로 진행합니다 " * 8


@pytest.fixture(autouse=True)
def _reset_user_ratios():
    ResultView._match_user_ratios.clear()
    yield
    ResultView._match_user_ratios.clear()


def _archive_view(qtbot, mock_config_manager):
    view = ResultView(QFileIconProvider(), mock_config_manager)
    qtbot.addWidget(view)
    view.resize(1400, 800)
    view.show()
    view.set_search_context("proceed", AppStrings.SPECIAL_SEARCH_ITEMS[7])
    matches = [
        [7, "System_UI", "ID_BTN_CONFIRM_AND_PROCEED_TO_NEXT", LONG_SOURCE, LONG_TRANSLATION, None, None],
        [9, "System_UI", "ID_BTN_CANCEL", "Do not proceed", "진행 안 함", None, None],
    ]
    view.set_results([[2, "Game.archive", "D:/loc", "D:/loc/Game.archive", matches]])
    view.result_view.selectionModel().setCurrentIndex(
        view.proxy_model.index(0, 0),
        QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows,
    )
    qtbot.waitUntil(lambda: view.match_model.rowCount() == 2)
    qtbot.waitUntil(lambda: view.match_view.viewport().width() > 0)
    view._adjust_match_column_widths()
    return view


def _widths(view):
    return [view.match_view.columnWidth(c) for c in range(view.match_proxy_model.columnCount())]


def test_archive_columns_fill_table_width_and_share_source_translation(qtbot, mock_config_manager):
    view = _archive_view(qtbot, mock_config_manager)
    widths = _widths(view)
    available = view.match_view.viewport().width()
    assert sum(widths) == available
    assert not view.match_view.horizontalScrollBar().isVisible()
    # 원문(3)·번역(4)은 남는 폭을 똑같이 나누고(반올림 차이만 마지막 열), 표 폭의 절반 이상을 차지합니다.
    assert abs(widths[3] - widths[4]) <= 1
    assert widths[3] + widths[4] >= available // 2
    # 키 열은 내용이 길어도 표 폭의 15%(최소 80px)를 넘지 않습니다.
    assert widths[2] <= max(80, int(available * 0.15))


def test_columns_follow_preview_splitter(qtbot, mock_config_manager):
    view = _archive_view(qtbot, mock_config_manager)
    before = view.match_view.viewport().width()
    total = sum(view.match_splitter.sizes())
    view.match_splitter.setSizes([total // 3, total - total // 3])
    qtbot.waitUntil(lambda: view.match_view.viewport().width() != before)
    qtbot.waitUntil(lambda: sum(_widths(view)) == view.match_view.viewport().width())
    # 스크롤바 표시는 Qt가 배치를 마친 뒤 갱신하므로, 잠시 뒤 사라지는지 확인합니다.
    qtbot.waitUntil(lambda: not view.match_view.horizontalScrollBar().isVisible())


def test_user_dragged_ratio_is_kept_for_next_layout(qtbot, mock_config_manager):
    view = _archive_view(qtbot, mock_config_manager)
    header = view.match_view.horizontalHeader()
    # 사용자가 머리글을 누른 채 키 열 경계를 넓게 끈 것처럼 만듭니다.
    press_point = QPoint(header.sectionPosition(2) + 5, header.height() // 2)
    QTest.mousePress(header.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, press_point)
    header.resizeSection(2, header.sectionSize(2) + 200)
    assert view._match_user_resized
    QTest.mouseRelease(header.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, press_point)
    assert ResultView._match_user_ratios
    widths = _widths(view)
    assert sum(widths) == view.match_view.viewport().width()
    ratio_key = widths[2] / sum(widths)
    # 다른 폭으로 다시 나눠도 같은 비율을 씁니다.
    total = sum(view.match_splitter.sizes())
    view.match_splitter.setSizes([total // 2, total - total // 2])
    qtbot.waitUntil(lambda: sum(_widths(view)) == view.match_view.viewport().width())
    widths = _widths(view)
    assert abs(widths[2] / sum(widths) - ratio_key) < 0.01


def test_long_cell_has_full_text_tooltip(qtbot, mock_config_manager):
    view = _archive_view(qtbot, mock_config_manager)
    model = view.match_model
    tooltip = model.data(model.index(0, 3), Qt.ItemDataRole.ToolTipRole)
    assert tooltip.startswith("<qt>") and LONG_SOURCE.strip()[:40] in tooltip
    assert model.data(model.index(0, 0), Qt.ItemDataRole.ToolTipRole) is None


def test_window_resize_keeps_columns_fitted_after_old_adjust_timer(qtbot, mock_config_manager):
    """창 크기를 바꿔도 200ms 뒤의 예전 열 폭 조정이 상세 배분을 덮지 않습니다(7.5.6, 검수 3.1)."""
    view = _archive_view(qtbot, mock_config_manager)
    for width in (1000, 1700):
        view.resize(width, 800)
        # 예전 조정 타이머(200ms)와 상세 배분(50ms)이 모두 돈 뒤를 봅니다.
        qtbot.wait(400)
        widths = _widths(view)
        assert sum(widths) == view.match_view.viewport().width(), (width, widths)
        qtbot.waitUntil(lambda: not view.match_view.horizontalScrollBar().isVisible())
        assert abs(widths[3] - widths[4]) <= 1
    # 프로그램이 바꾼 폭은 "사용자가 끈 비율"로 기억하지 않습니다.
    assert not view._match_user_resized
    assert not ResultView._match_user_ratios


def test_header_click_without_drag_does_not_store_ratio(qtbot, mock_config_manager):
    view = _archive_view(qtbot, mock_config_manager)
    header = view.match_view.horizontalHeader()
    view.resize(1100, 800)
    qtbot.wait(400)
    point = QPoint(header.sectionPosition(3) + 20, header.height() // 2)
    QTest.mouseClick(header.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    qtbot.wait(100)
    assert not ResultView._match_user_ratios
    assert sum(_widths(view)) == view.match_view.viewport().width()


def test_tab_switch_does_not_apply_other_mode_pixel_widths(qtbot, mock_config_manager):
    """다른 검색 방식 탭의 저장 폭(열 수가 다름)을 덮어쓰지 않고 표 폭에 맞춰 다시 나눕니다(7.5.7)."""
    view = _archive_view(qtbot, mock_config_manager)
    for other_widths in ([459], [40, 80, 339]):
        mock_config_manager.save_column_widths(C.VIEW_MATCH, other_widths)
        view.restore_shared_layout()
        widths = _widths(view)
        assert sum(widths) == view.match_view.viewport().width(), (other_widths, widths)
        assert abs(widths[3] - widths[4]) <= 1
