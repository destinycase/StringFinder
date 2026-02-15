import pytest
import os
import sys
from PySide6.QtCore import Qt

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from ui.search_tab import SearchTab


# Use mock_config_manager from conftest.py
@pytest.fixture
def search_tab_fixture(qtbot, mock_config_manager):
    # Create SearchTab with mocked config manager (which uses temp dir)
    widget = SearchTab(mock_config_manager)
    qtbot.addWidget(widget)
    return widget


def test_initial_ui_state(search_tab_fixture):
    """Verify initial UI components state."""
    # Since config is empty in temp dir, history should be empty
    assert search_tab_fixture.search_combo.count() == 0
    # Search button is always enabled initially (validation happens on click)
    assert search_tab_fixture.search_btn.isEnabled()

    # Check lists
    assert search_tab_fixture.folder_list.count() == 0
    # ConfigManager has 6 default extensions
    assert search_tab_fixture.ext_list.count() == 6


def test_add_folder_updates_list(search_tab_fixture, tmp_path):
    """Verify that adding a folder updates the folder list."""
    folder_path = str(tmp_path)
    # Manually call _add_folder_item as _add_folder opens dialog
    search_tab_fixture._add_folder_item(folder_path)

    assert search_tab_fixture.folder_list.count() == 1
    item = search_tab_fixture.folder_list.item(0)
    widget = search_tab_fixture.folder_list.itemWidget(item)
    assert widget.text() == folder_path
    assert widget.isChecked()


def test_add_extension_updates_list(search_tab_fixture, qtbot):
    """Test adding extension filters."""
    # Initial count
    initial_count = search_tab_fixture.ext_list.count()

    # Simulate user typing "py" and clicking add
    qtbot.keyClicks(search_tab_fixture.ext_edit, "py")
    qtbot.mouseClick(search_tab_fixture.add_ext_btn, Qt.MouseButton.LeftButton)

    assert search_tab_fixture.ext_list.count() == initial_count + 1
    item = search_tab_fixture.ext_list.item(initial_count)  # Last item
    widget = search_tab_fixture.ext_list.itemWidget(item)
    assert widget.text() == "py"  # Code removes dot
    assert widget.isChecked()

    # Add another with dot
    search_tab_fixture.ext_edit.clear()
    qtbot.keyClicks(search_tab_fixture.ext_edit, ".cpp")
    qtbot.mouseClick(search_tab_fixture.add_ext_btn, Qt.MouseButton.LeftButton)

    assert search_tab_fixture.ext_list.count() == initial_count + 2
    item2 = search_tab_fixture.ext_list.item(initial_count + 1)
    widget2 = search_tab_fixture.ext_list.itemWidget(item2)
    assert widget2.text() == "cpp"  # Code removes dot
    # ... existing tests ...


def test_ui_disabled_during_search(qtbot, search_tab_fixture):
    """검색 중 UI 요소가 비활성화되는지 테스트합니다."""
    # 초기 상태 확인
    assert search_tab_fixture.search_combo.isEnabled()
    assert search_tab_fixture.filename_dock.widget().isEnabled()
    assert search_tab_fixture.folder_dock.widget().isEnabled()
    assert search_tab_fixture.ext_dock.widget().isEnabled()

    # 검색 시작 시뮬레이션
    search_tab_fixture._set_inputs_enabled(False)

    assert not search_tab_fixture.search_combo.isEnabled()
    assert not search_tab_fixture.filename_dock.widget().isEnabled()
    assert not search_tab_fixture.folder_dock.widget().isEnabled()
    assert not search_tab_fixture.ext_dock.widget().isEnabled()

    # 검색 종료 시뮬레이션
    search_tab_fixture._set_inputs_enabled(True)

    assert search_tab_fixture.search_combo.isEnabled()
    assert search_tab_fixture.filename_dock.widget().isEnabled()
    assert search_tab_fixture.folder_dock.widget().isEnabled()
    assert search_tab_fixture.ext_dock.widget().isEnabled()


def test_ui_disabled_special_mode_restoration(qtbot, search_tab_fixture):
    """특수 모드일 때 확장자 필터가 비활성 상태로 유지되는지 테스트합니다."""
    # 특수 모드 설정 (예: JSON)
    search_tab_fixture.special_search_combo.setCurrentIndex(3)  # JSON (부분 일치)

    # 검색 시작
    search_tab_fixture._set_inputs_enabled(False)
    assert not search_tab_fixture.ext_dock.widget().isEnabled()

    # 검색 종료
    search_tab_fixture._set_inputs_enabled(True)
    assert search_tab_fixture.ext_dock.widget().isEnabled()

    # 내부 위젯 상태 확인 (Dock은 활성화되지만 내부 입력은 비활성화여야 함)
    assert not search_tab_fixture.ext_list.isEnabled()
    assert not search_tab_fixture.ext_edit.isEnabled()


def test_search_state_reset_on_completion(qtbot, search_tab_fixture):
    """검색 완료 후 상태가 IDLE로 초기화되는지 테스트합니다."""
    from utils.constants import Constants

    # 강제로 SEARCHING 상태 설정
    search_tab_fixture.search_state = Constants.SearchState.SEARCHING
    search_tab_fixture.results_buffer = []  # 테스트를 위한 초기화
    search_tab_fixture.start_timer = 0
    search_tab_fixture.search_stage_start = 0

    # 검색 완료 시그널 호출 (found=10, skipped=0)
    search_tab_fixture._on_search_finished(10, 0)

    # 상태가 IDLE로 변경되었는지 확인
    assert search_tab_fixture.search_state == Constants.SearchState.IDLE


def test_search_state_reset_on_error(qtbot, search_tab_fixture):
    """검색 오류 발생 후 상태가 IDLE로 초기화되는지 테스트합니다."""
    from utils.constants import Constants

    # 강제로 SEARCHING 상태 설정
    search_tab_fixture.search_state = Constants.SearchState.SEARCHING

    # 오류 발생 시그널 호출
    search_tab_fixture._on_search_error("Test Error")

    # 상태가 IDLE로 변경되었는지 확인
    assert search_tab_fixture.search_state == Constants.SearchState.IDLE
