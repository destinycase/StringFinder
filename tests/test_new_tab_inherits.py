"""새 탭이 지금 보이는 탭의 폴더·필터 구성을 이어받고 검색 모드는 기본으로 여는지 확인합니다(7.1.7)."""
from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants as C

SOURCE = {
    C.PAYLOAD_INPUTS: {
        C.STATE_KEY_SEARCH: "needle",
        C.PAYLOAD_SPECIAL_MODE: AppStrings.SPECIAL_SEARCH_ITEMS[1],
        C.CONFIG_KEY_FOLDERS: {"C:/a": True, "C:/b": False},
        C.CONFIG_KEY_FOLDER_GROUPS: [{"name": "G", "collapsed": False, "folders": ["C:/b"]}],
        C.CONFIG_KEY_EXTENSIONS: {".txt": True, ".log": False},
    }
}


def _window(qtbot):
    from ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    return window


def test_new_tab_copies_folders_groups_and_filters_of_current_tab(qtbot, mock_config_manager):
    window = _window(qtbot)
    source = window.add_new_tab("원본", SOURCE)
    window.tab_widget.setCurrentWidget(source)
    new = window.add_new_tab()
    assert new.folder_panel.get_state() == source.folder_panel.get_state()
    assert new.folder_panel.get_group_state() == source.folder_panel.get_group_state()
    assert new.ext_panel.get_state()[C.CONFIG_KEY_EXTENSIONS] == source.ext_panel.get_state()[C.CONFIG_KEY_EXTENSIONS]
    assert new.filename_panel.get_state() == source.filename_panel.get_state()


def test_new_tab_opens_in_basic_mode_without_search_text(qtbot, mock_config_manager):
    window = _window(qtbot)
    source = window.add_new_tab("원본", SOURCE)
    assert source.ext_panel.get_special_mode() != AppStrings.SPECIAL_SEARCH_OFF
    window.tab_widget.setCurrentWidget(source)
    new = window.add_new_tab()
    assert new.ext_panel.get_special_mode() == AppStrings.SPECIAL_SEARCH_OFF
    assert new.search_panel.get_state()[C.PAYLOAD_SPECIAL_MODE] == AppStrings.SPECIAL_SEARCH_OFF
    assert new.search_panel.search_combo.currentText() == ""
    # 원본 탭은 그대로입니다.
    assert source.ext_panel.get_special_mode() == AppStrings.SPECIAL_SEARCH_ITEMS[1]


def test_new_tab_inputs_are_saved_without_search(qtbot, mock_config_manager):
    window = _window(qtbot)
    source = window.add_new_tab("원본", SOURCE)
    window.tab_widget.setCurrentWidget(source)
    window.add_new_tab()
    name = window.tab_widget.tabText(window.tab_widget.currentIndex())
    mock_config_manager.wait_for_session_saves()
    saved = mock_config_manager._read_inputs_file(
        mock_config_manager._resolve_session_file_path(name) + C.SESSION_INPUTS_SUFFIX
    )
    assert saved[C.CONFIG_KEY_FOLDERS] == {"C:/a": True, "C:/b": False}
