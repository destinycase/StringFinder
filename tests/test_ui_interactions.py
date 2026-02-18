import pytest
import os
import sys
from PySide6.QtCore import Qt

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from ui.search_tab import SearchTab


# conftest.py의 mock_config_manager 사용
@pytest.fixture
def search_tab_fixture(qtbot, mock_config_manager):
    # 임시 디렉토리를 사용하는 모킹된 설정 관리자로 SearchTab 생성
    widget = SearchTab(mock_config_manager)
    qtbot.addWidget(widget)
    yield widget
    widget.close()
    widget.deleteLater()


def test_initial_ui_state(search_tab_fixture):
    """초기 UI 컴포넌트 상태 확인."""
    # 임시 디렉토리의 설정이 비어있으므로 히스토리는 0이어야 함
    assert search_tab_fixture.search_combo.count() == 0
    # 검색 버튼은 초기에 활성화 상태 (클릭 시 유효성 검사 수행)
    assert search_tab_fixture.search_btn.isEnabled()

    # 리스트 확인
    assert search_tab_fixture.folder_list.count() == 0
    # ConfigManager는 4개의 기본 확장자를 가짐
    assert search_tab_fixture.ext_list.count() == 4


def test_add_folder_updates_list(search_tab_fixture, tmp_path):
    """폴더 추가 시 폴더 리스트가 업데이트되는지 확인."""
    folder_path = str(tmp_path)
    # _add_folder는 다이얼로그를 열므로 _add_folder_item을 직접 호출
    search_tab_fixture._add_folder_item(folder_path)

    assert search_tab_fixture.folder_list.count() == 1
    item = search_tab_fixture.folder_list.item(0)
    widget = search_tab_fixture.folder_list.itemWidget(item)
    assert widget.text() == folder_path
    assert widget.isChecked()


def test_add_extension_updates_list(search_tab_fixture, qtbot):
    """확장자 필터 추가 테스트."""
    # 초기 개수
    initial_count = search_tab_fixture.ext_list.count()

    # 사용자가 "py" 입력 후 추가 클릭 시뮬레이션
    qtbot.keyClicks(search_tab_fixture.ext_edit, "py")
    qtbot.mouseClick(search_tab_fixture.add_ext_btn, Qt.MouseButton.LeftButton)

    assert search_tab_fixture.ext_list.count() == initial_count + 1
    item = search_tab_fixture.ext_list.item(initial_count)  # 마지막 항목
    widget = search_tab_fixture.ext_list.itemWidget(item)
    assert widget.text() == "py"  # 코드가 점(.)을 제거함
    assert widget.isChecked()

    # 점을 포함하여 다른 확장자 추가
    search_tab_fixture.ext_edit.clear()
    qtbot.keyClicks(search_tab_fixture.ext_edit, ".cpp")
    qtbot.mouseClick(search_tab_fixture.add_ext_btn, Qt.MouseButton.LeftButton)

    assert search_tab_fixture.ext_list.count() == initial_count + 2
    item2 = search_tab_fixture.ext_list.item(initial_count + 1)
    widget2 = search_tab_fixture.ext_list.itemWidget(item2)
    assert widget2.text() == "cpp"  # 코드가 점(.)을 제거함


def test_ui_disabled_during_search(qtbot, search_tab_fixture):
    """검색 중 UI 요소가 비활성화되는지 테스트합니다."""
    assert search_tab_fixture.search_combo.isEnabled()
    assert search_tab_fixture.filename_dock.widget().isEnabled()
    assert search_tab_fixture.folder_dock.widget().isEnabled()
    assert search_tab_fixture.ext_dock.widget().isEnabled()

    search_tab_fixture._set_inputs_enabled(False)

    assert not search_tab_fixture.search_combo.isEnabled()
    assert not search_tab_fixture.filename_dock.widget().isEnabled()
    assert not search_tab_fixture.folder_dock.widget().isEnabled()
    assert not search_tab_fixture.ext_dock.widget().isEnabled()

    search_tab_fixture._set_inputs_enabled(True)

    assert search_tab_fixture.search_combo.isEnabled()
    assert search_tab_fixture.filename_dock.widget().isEnabled()
    assert search_tab_fixture.folder_dock.widget().isEnabled()
    assert search_tab_fixture.ext_dock.widget().isEnabled()


def test_ui_disabled_special_mode_restoration(qtbot, search_tab_fixture):
    """특수 모드에서 확장자 필터가 비활성 상태로 유지되는지 테스트합니다."""
    search_tab_fixture.special_search_combo.setCurrentIndex(3)

    search_tab_fixture._set_inputs_enabled(False)
    assert not search_tab_fixture.ext_dock.widget().isEnabled()

    search_tab_fixture._set_inputs_enabled(True)
    assert search_tab_fixture.ext_dock.widget().isEnabled()

    assert not search_tab_fixture.ext_list.isEnabled()
    assert not search_tab_fixture.ext_edit.isEnabled()


def test_search_state_reset_on_completion(qtbot, search_tab_fixture):
    """검색 완료 후 상태가 IDLE로 초기화되는지 테스트합니다."""
    from sf_utils.constants import Constants

    search_tab_fixture.search_state = Constants.SearchState.SEARCHING
    search_tab_fixture.results_buffer = []
    search_tab_fixture.start_timer = 0
    search_tab_fixture.search_stage_start = 0

    search_tab_fixture._on_search_finished(10, 0)

    assert search_tab_fixture.search_state == Constants.SearchState.IDLE


def test_search_state_reset_on_error(qtbot, search_tab_fixture):
    """검색 오류 발생 시 상태가 IDLE로 초기화되는지 테스트합니다."""
    from sf_utils.constants import Constants

    search_tab_fixture.search_state = Constants.SearchState.SEARCHING

    search_tab_fixture._on_search_error("Test Error")

    assert search_tab_fixture.search_state == Constants.SearchState.IDLE


def test_preview_logic(qtbot, search_tab_fixture, tmp_path):
    """다양한 파일 타입 및 모드에 대한 미리보기 로직 확인."""
    from sf_utils.app_strings import AppStrings

    # 1. JSON 파일 - 미리보기 표시되어야 함
    json_path = tmp_path / "test.json"
    json_path.write_text('{"key": "value"}', encoding="utf-8")

    # JSON 모드 선택
    search_tab_fixture.special_search_combo.setCurrentText("JSON (부분 일치)")
    search_tab_fixture._update_preview(str(json_path), 1)
    # 미리보기에 텍스트가 포함되어 있는지 확인 (HTML 사용)
    assert "key" in search_tab_fixture.preview_text.toPlainText()
    assert AppStrings.RESULT_PREVIEW_ERROR not in search_tab_fixture.preview_text.toPlainText()

    # 2. 텍스트 파일 - 미리보기 표시되어야 함
    txt_path = tmp_path / "test.txt"
    txt_path.write_text("Hello World", encoding="utf-8")

    # 모드를 Off로 리셋
    search_tab_fixture.special_search_combo.setCurrentText(AppStrings.SPECIAL_SEARCH_OFF)
    search_tab_fixture._update_preview(str(txt_path), 1)
    assert "Hello" in search_tab_fixture.preview_text.toPlainText()

    # 3. 이진 파일 - 오류 표시되어야 함
    bin_path = tmp_path / "test.bin"
    bin_path.write_bytes(b"Binary\x00Data")

    search_tab_fixture._update_preview(str(bin_path), 1)
    assert search_tab_fixture.preview_text.toPlainText() == AppStrings.RESULT_PREVIEW_ERROR

    # 4. 엑셀 모드 - 미리보기 제한 확인
    search_tab_fixture.special_search_combo.setCurrentText("Excel (부분 일치)")
    search_tab_fixture._update_preview(str(txt_path), 1)
    # 엑셀 모드에서는 미리보기를 제공하지 않으므로 에러 메시지가 표시되어야 함
    assert search_tab_fixture.preview_text.toPlainText() == AppStrings.RESULT_PREVIEW_ERROR


def test_excel_location_in_normal_mode(qtbot, search_tab_fixture):
    """일반 모드에서 엑셀 파일 결과가 정확한 헤더/위치를 표시하는지 확인."""
    from sf_utils.app_strings import AppStrings

    # 엑셀의 전형적인 3-튜플 결과 모킹
    excel_path = "D:/test/data.xlsx"
    matches = [(0, "Sheet1!A1", "Match 1"), (0, "Sheet1!B5", "Match 2")]

    # 1. 일반 모드 (특수 검색 OFF)
    search_tab_fixture.special_search_combo.setCurrentText(AppStrings.SPECIAL_SEARCH_OFF)

    # 모델에 직접 set_matches 호출
    # 보통 _show_matches_from_view에 의해 호출됨
    search_tab_fixture.match_model.set_matches(
        excel_path, matches, search_text="Match", search_mode=AppStrings.SPECIAL_SEARCH_OFF
    )

    # 헤더가 엑셀 헤더인지 확인
    assert search_tab_fixture.match_model._headers == [AppStrings.HEADER_EXCEL_POSITION, AppStrings.HEADER_EXCEL_VALUE]

    # 데이터가 2-튜플(위치, 값)인지 확인
    assert search_tab_fixture.match_model.rowCount() == 2
    assert (
        search_tab_fixture.match_model.data(search_tab_fixture.match_model.index(0, 0), Qt.ItemDataRole.EditRole)
        == "Sheet1!A1"
    )
    assert (
        search_tab_fixture.match_model.data(search_tab_fixture.match_model.index(0, 1), Qt.ItemDataRole.EditRole)
        == "Match 1"
    )
