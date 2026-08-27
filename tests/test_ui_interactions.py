"""
[test_ui_interactions.py]

이 테스트는 SearchTab을 중심으로 하는 메인 UI의 상태 관리와 사용자 상호작용 로직을 검증합니다.

- 테스트 목적:
  1. 초기 UI 상태 및 설정값 로드 확인.
  2. 폴더/확장자 추가 및 검색어 입력 등 핵심 인터페이스 컨트롤 작동 검증.
  3. 검색 진행 상태에 따른 UI 비활성화/복구 로직 확인.
  4. 미리보기 및 결과 뷰의 데이터 렌더링 정확도 보장.

- 주요 검증 사항:
  1. 폴더 목록 및 확장자 필터의 동적 업데이트.
  2. 검색 중 입력창 및 옵션 패널의 원자적 활성/비활성 제어.
  3. 텍스트/JSON/이진 파일 및 엑셀 모드에 따른 필터링.
  4. 엑셀 결과 항목의 특수 헤더 및 위치 정보 표시 무결성.
"""

import os
import sys
from unittest.mock import patch

import pytest
from PySide6.QtCore import Qt

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from ui.search_tab import SearchTab


@pytest.fixture
def search_tab_fixture(qtbot, mock_config_manager):
    widget = SearchTab(mock_config_manager)
    qtbot.addWidget(widget)
    yield widget
    widget.close()
    widget.deleteLater()


def test_initial_ui_state(search_tab_fixture):
    """test_initial_ui_state 함수."""
    # 임시 디렉토리의 설정이 비어있으므로 히스토리는 0이어야 함
    assert search_tab_fixture.search_panel.search_combo.count() == 0
    # 검색 버튼은 초기에 활성화 상태 (클릭 시 유효성 검사 수행)
    assert search_tab_fixture.search_panel.search_btn.isEnabled()

    # 리스트 확인
    assert search_tab_fixture.folder_panel.folder_list.count() == 0
    assert search_tab_fixture.ext_panel.ext_list.count() == 4


def test_add_folder_updates_list(search_tab_fixture, tmp_path):
    """폴더 추가 시 폴더 리스트가 업데이트되는지 확인."""
    folder_path = str(tmp_path)
    search_tab_fixture.folder_panel.add_folder(folder_path)

    assert search_tab_fixture.folder_panel.folder_list.count() == 1
    item = search_tab_fixture.folder_panel.folder_list.item(0)
    widget = search_tab_fixture.folder_panel.folder_list.itemWidget(item)
    assert widget.text() == folder_path
    assert widget.isChecked()


def test_add_extension_updates_list(search_tab_fixture, qtbot):
    """확장자 필터 추가 테스트."""
    # 초기 개수
    initial_count = search_tab_fixture.ext_panel.ext_list.count()

    qtbot.keyClicks(search_tab_fixture.ext_panel.ext_edit, "py")
    qtbot.mouseClick(search_tab_fixture.ext_panel.add_btn, Qt.MouseButton.LeftButton)

    assert search_tab_fixture.ext_panel.ext_list.count() == initial_count + 1
    item = search_tab_fixture.ext_panel.ext_list.item(initial_count)  # 마지막 항목
    widget = search_tab_fixture.ext_panel.ext_list.itemWidget(item)
    assert widget.text() == "py"  # 코드가 점(.)을 제거함
    assert widget.isChecked()

    # 점을 포함하여 다른 확장자 추가
    search_tab_fixture.ext_panel.ext_edit.clear()
    qtbot.keyClicks(search_tab_fixture.ext_panel.ext_edit, ".cpp")
    qtbot.mouseClick(search_tab_fixture.ext_panel.add_btn, Qt.MouseButton.LeftButton)

    assert search_tab_fixture.ext_panel.ext_list.count() == initial_count + 2
    item2 = search_tab_fixture.ext_panel.ext_list.item(initial_count + 1)
    widget2 = search_tab_fixture.ext_panel.ext_list.itemWidget(item2)
    assert widget2.text() == "cpp"  # 코드가 점(.)을 제거함


def test_ui_disabled_during_search(qtbot, search_tab_fixture):
    """test_ui_disabled_during_search 함수."""
    assert search_tab_fixture.search_panel.search_combo.isEnabled()
    assert search_tab_fixture.filename_dock.widget().isEnabled()
    assert search_tab_fixture.folder_dock.widget().isEnabled()
    assert search_tab_fixture.ext_dock.widget().isEnabled()

    search_tab_fixture._set_inputs_enabled(False)

    assert not search_tab_fixture.search_panel.search_combo.isEnabled()
    assert not search_tab_fixture.filename_dock.widget().isEnabled()
    assert not search_tab_fixture.folder_dock.widget().isEnabled()
    assert not search_tab_fixture.ext_dock.widget().isEnabled()

    search_tab_fixture._set_inputs_enabled(True)

    assert search_tab_fixture.search_panel.search_combo.isEnabled()
    assert search_tab_fixture.filename_dock.widget().isEnabled()
    assert search_tab_fixture.folder_dock.widget().isEnabled()
    assert search_tab_fixture.ext_dock.widget().isEnabled()


def test_ui_disabled_special_mode_restoration(qtbot, search_tab_fixture):
    """특수 모드에서 확장자 필터가 비활성 상태로 유지되는지 테스트합니다."""
    search_tab_fixture.ext_panel.special_combo.setCurrentIndex(3)

    search_tab_fixture._set_inputs_enabled(False)
    assert not search_tab_fixture.ext_dock.widget().isEnabled()

    search_tab_fixture._set_inputs_enabled(True)
    assert search_tab_fixture.ext_dock.widget().isEnabled()

    assert not search_tab_fixture.ext_panel.ext_list.isEnabled()
    assert not search_tab_fixture.ext_panel.ext_edit.isEnabled()


def test_search_state_reset_on_completion(qtbot, search_tab_fixture):
    """test_search_state_reset_on_completion 함수."""
    from sf_utils.constants import Constants

    search_tab_fixture.search_state = Constants.SearchState.SEARCHING
    search_tab_fixture.results_buffer = []
    search_tab_fixture.start_timer = 0
    search_tab_fixture.search_stage_start = 0

    search_tab_fixture._on_search_finished(10, 0, 0)
    search_tab_fixture._on_worker_finished()

    assert search_tab_fixture.search_state == Constants.SearchState.IDLE


def test_search_state_reset_on_error(qtbot, search_tab_fixture):
    """test_search_state_reset_on_error 함수."""
    from sf_utils.constants import Constants

    search_tab_fixture.search_state = Constants.SearchState.SEARCHING

    search_tab_fixture._on_search_error("Test Error")

    assert search_tab_fixture.search_state == Constants.SearchState.IDLE


def test_memory_error_shows_user_popup(search_tab_fixture):
    """시스템 메모리 보호로 검색이 중단되면 사용자 안내 팝업을 표시한다."""
    from sf_utils.app_strings import AppStrings

    with patch("ui.search_tab.QMessageBox.warning") as warning:
        search_tab_fixture._on_search_error(AppStrings.ERROR_MEMORY_CRITICAL)

    warning.assert_called_once_with(
        search_tab_fixture,
        AppStrings.ERROR_MEMORY_CRITICAL_TITLE,
        AppStrings.ERROR_MEMORY_CRITICAL_DETAIL,
    )


def test_generic_search_error_does_not_show_memory_popup(search_tab_fixture):
    """일반 검색 오류에는 메모리 부족 전용 팝업을 표시하지 않는다."""
    with patch("ui.search_tab.QMessageBox.warning") as warning:
        search_tab_fixture._on_search_error("Test Error")

    warning.assert_not_called()


def test_excel_location_in_normal_mode(qtbot, search_tab_fixture):
    """일반 모드에서 엑셀 파일 결과가 정확한 헤더/위치를 표시하는지 확인."""
    from sf_utils.app_strings import AppStrings

    # 엑셀의 개선된 4-튜플 구조 모킹 (Line, Sheet, Cell, Value)
    excel_path = "D:/test/data.xlsx"
    matches = [(0, "Sheet1", "A1", "Match 1"), (0, "Sheet1", "B5", "Match 2")]

    search_tab_fixture.ext_panel.special_combo.setCurrentText(AppStrings.SPECIAL_SEARCH_OFF)

    search_tab_fixture.result_view_panel.match_model.set_matches(
        excel_path, matches, search_text="Match", search_mode=AppStrings.SPECIAL_SEARCH_OFF
    )

    # [v4.45.4] 일반 모드 엑셀 헤더는 일반 텍스트와 동일한 규격 적용
    assert search_tab_fixture.result_view_panel.match_model._headers == [
        AppStrings.HEADER_POSITION,
        AppStrings.HEADER_CONTENT,
    ]

    # [v4.48.0] Normal 모드에서는 단일 컬럼(0)에 "위치 | 내용" 형식으로 표시됨
    assert search_tab_fixture.result_view_panel.match_model.columnCount() == 1
    # Column 0: 위치 | 내용 (Sheet1!A1 | Match 1)
    display_text = search_tab_fixture.result_view_panel.match_model.data(
        search_tab_fixture.result_view_panel.match_model.index(0, 0), Qt.ItemDataRole.EditRole
    )
    assert "Sheet1!A1" in display_text
    assert "Match 1" in display_text


def test_excel_rust_normal_tuple_no_none_suffix(search_tab_fixture):
    from sf_utils.app_strings import AppStrings

    excel_path = "D:/test/data.xlsx"
    # Rust normal mode tuple: (line, "Sheet | Cell | Value", offset, length)
    matches = [(110, "Sheet1 | AG110 | 사냥꾼 증표", None, None)]

    search_tab_fixture.result_view_panel.match_model.set_matches(
        excel_path, matches, search_text="증", search_mode=AppStrings.SPECIAL_SEARCH_OFF
    )

    model = search_tab_fixture.result_view_panel.match_model
    text = model.data(model.index(0, 0), Qt.ItemDataRole.EditRole)

    assert "None" not in str(text)
    assert "Sheet1" in str(text)
    assert "AG110" in str(text)
    assert "사냥꾼 증표" in str(text)


def test_excel_rust_normal_tuple_highlight_in_normal_mode(search_tab_fixture):
    from sf_utils.app_strings import AppStrings

    excel_path = "D:/test/data.xlsx"
    matches = [(110, "Sheet1 | AG110 | 사냥꾼 증표", None, None)]

    search_tab_fixture.result_view_panel.match_model.set_matches(
        excel_path, matches, search_text="증", search_mode=AppStrings.SPECIAL_SEARCH_OFF
    )

    model = search_tab_fixture.result_view_panel.match_model
    html = model.data(model.index(0, 0), Qt.ItemDataRole.DisplayRole)

    assert "<span" in str(html)
    assert "증" in str(html)


def test_archive_multi_column_mapping(qtbot, search_tab_fixture):
    """Archive 모드에서 5-튜플 결과가 정확히 5개 컬럼으로 매핑되는지 확인."""
    from sf_utils.app_strings import AppStrings

    archive_path = "D:/test/lang.archive"
    matches = [(10, "Common", "UI_OK", "OK", "확인"), (25, "System", "ERR_INVALID", "Invalid Input", "잘못된 입력")]

    search_tab_fixture.ext_panel.special_combo.setCurrentText(AppStrings.SPECIAL_SEARCH_ARCHIVE)

    # set_matches 호출 시 Archive 모드 명시
    search_tab_fixture.result_view_panel.match_model.set_matches(
        archive_path, matches, search_text="OK", search_mode=AppStrings.SPECIAL_SEARCH_ARCHIVE
    )

    # 헤더 검증
    assert search_tab_fixture.result_view_panel.match_model._headers == [
        AppStrings.HEADER_POSITION,
        AppStrings.HEADER_ARCHIVE_NAMESPACE,
        AppStrings.HEADER_ARCHIVE_KEY,
        AppStrings.HEADER_ARCHIVE_SOURCE,
        AppStrings.HEADER_ARCHIVE_TRANSLATION,
    ]

    # 데이터 매핑 검증 (첫 번째 행)
    model = search_tab_fixture.result_view_panel.match_model
    assert model.rowCount() == 2
    assert model.data(model.index(0, 0), Qt.ItemDataRole.EditRole) == "10"  # 위치(줄)
    assert model.data(model.index(0, 1), Qt.ItemDataRole.EditRole) == "Common"  # 네임스페이스
    assert model.data(model.index(0, 2), Qt.ItemDataRole.EditRole) == "UI_OK"  # 키
    assert model.data(model.index(0, 3), Qt.ItemDataRole.EditRole) == "OK"  # 소스
    assert model.data(model.index(0, 4), Qt.ItemDataRole.EditRole) == "확인"  # 번역
