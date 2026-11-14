"""필터를 바꾸면 검색하지 않아도 그 탭의 입력값이 세션에 남는지 확인합니다(7.1.4).

세션 파일(결과 포함)은 검색 끝·정상 종료 때만 저장됩니다. 그 사이에 폴더·그룹·필터를 바꾸고
정상 종료 없이 꺼져도, 다음 시작 때 바꾼 값으로 복원되어야 합니다.
"""
import os

from sf_utils.constants import Constants as C

FULL = {
    C.PAYLOAD_INPUTS: {C.CONFIG_KEY_FOLDERS: {"C:/old": True}, C.CONFIG_KEY_FOLDER_GROUPS: []},
    C.PAYLOAD_RESULTS: [["C:/old/a.txt", 1, [[1, "needle"]]]],
}
NEW_INPUTS = {
    C.CONFIG_KEY_FOLDERS: {"C:/old": True, "C:/new": False},
    C.CONFIG_KEY_FOLDER_GROUPS: [{"name": "G", "collapsed": False, "folders": ["C:/new"]}],
}


def _files(manager):
    return sorted(os.listdir(manager.sessions_dir))


def test_inputs_saved_on_change_override_the_full_session(mock_config_manager):
    m = mock_config_manager
    assert m.save_session("탭", FULL)
    assert m.save_session_inputs("탭", NEW_INPUTS)
    loaded = m.load_session("탭")
    assert loaded[C.PAYLOAD_INPUTS] == NEW_INPUTS
    assert loaded[C.PAYLOAD_RESULTS] == FULL[C.PAYLOAD_RESULTS]
    # 입력값 파일은 세션 목록에 끼지 않습니다.
    assert m.get_all_session_names() == ["탭"]


def test_full_save_with_same_inputs_removes_the_inputs_file(mock_config_manager):
    m = mock_config_manager
    m.save_session("탭", FULL)
    m.save_session_inputs("탭", NEW_INPUTS)
    before = _files(m)
    m.save_session("탭", {**FULL, C.PAYLOAD_INPUTS: NEW_INPUTS})
    assert len(_files(m)) == len(before) - 1
    assert m.load_session("탭")[C.PAYLOAD_INPUTS] == NEW_INPUTS


def test_late_full_save_with_older_inputs_keeps_newer_inputs(mock_config_manager):
    """검색 끝의 백그라운드 저장이 늦게 끝나도, 그 뒤에 바꾼 입력값을 지우지 않습니다."""
    m = mock_config_manager
    m.save_session_inputs("탭", NEW_INPUTS)
    m.save_session("탭", FULL)
    assert m.load_session("탭")[C.PAYLOAD_INPUTS] == NEW_INPUTS


def test_delete_session_removes_the_inputs_file(mock_config_manager):
    m = mock_config_manager
    m.save_session("탭", FULL)
    m.save_session_inputs("탭", NEW_INPUTS)
    m.delete_session("탭")
    assert _files(m) == []


def test_filter_change_without_search_survives_abnormal_exit(qtbot, mock_config_manager):
    from ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    tab = window.tab_widget.widget(0)
    name = window.tab_widget.tabText(0)
    tab.folder_panel.restore_state({"C:/old": True}, [])
    window._save_tab(0)  # 검색 끝에 저장된 세션
    tab.folder_panel.restore_state({"C:/old": True, "C:/new": True}, [{"name": "G", "folders": ["C:/new"]}])
    tab.ext_panel.add_extension("md")
    tab.folder_panel.filter_changed.emit()
    qtbot.waitUntil(lambda: bool(mock_config_manager.load_session(name)[C.PAYLOAD_INPUTS]
                                 .get(C.CONFIG_KEY_FOLDER_GROUPS)), timeout=3000)
    # 정상 종료 없이 꺼진 뒤 다시 켬: 세션을 그대로 읽어 새 탭에 복원
    state = mock_config_manager.load_session(name)
    from ui.search_tab import SearchTab
    restored = SearchTab(mock_config_manager)
    qtbot.addWidget(restored)
    restored.load_state(state)
    assert restored.folder_panel.get_state() == {"C:/old": True, "C:/new": True}
    assert restored.folder_panel.get_group_state() == [{"name": "G", "collapsed": False, "folders": ["C:/new"]}]
    assert "md" in restored.ext_panel.get_state().get(C.CONFIG_KEY_EXTENSIONS, {})
    restored.close()


def test_inputs_save_does_not_wait_for_a_running_session_save(qtbot, mock_config_manager):
    """큰 결과를 저장하는 동안 필터를 바꿔도 화면 스레드가 기다리지 않습니다(검수 4.1)."""
    import threading
    import time

    from ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    tab = window.tab_widget.widget(0)
    name = window.tab_widget.tabText(0)
    lock = mock_config_manager._session_io_lock()
    held = threading.Event()
    release = threading.Event()

    def hold():
        with lock:
            held.set()
            release.wait(5)

    worker = threading.Thread(target=hold)
    worker.start()
    held.wait(5)
    started = time.perf_counter()
    window._save_tab_inputs(tab)
    elapsed = time.perf_counter() - started
    release.set()
    worker.join()
    mock_config_manager.wait_for_session_saves()
    assert elapsed < 0.5
    assert os.path.exists(mock_config_manager._resolve_session_file_path(name) + C.SESSION_INPUTS_SUFFIX)


def test_new_tab_discards_leftover_inputs_with_the_same_name(qtbot, mock_config_manager):
    """복원되지 못한 탭의 입력값 파일이 같은 이름의 새 탭에 붙지 않습니다(검수 4.2)."""
    from ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    first = window.tab_widget.tabText(0)
    from sf_utils.app_strings import AppStrings
    next_name = AppStrings.SEARCH_TAB_TITLE_TEMPLATE.format(AppStrings.SEARCH_TAB_DEFAULT_TITLE, 2)
    assert next_name != first
    mock_config_manager.save_session_inputs(next_name, NEW_INPUTS)
    window.add_new_tab()
    assert window.tab_widget.tabText(1) == next_name
    mock_config_manager.wait_for_session_saves()
    # 새 탭은 현재 탭 구성을 이어받아 저장하므로(7.1.7), 남은 파일의 값이 아니라 새 탭의 값이 남습니다.
    saved = mock_config_manager._read_inputs_file(
        mock_config_manager._resolve_session_file_path(next_name) + C.SESSION_INPUTS_SUFFIX
    )
    assert saved is None or saved.get(C.CONFIG_KEY_FOLDERS) != NEW_INPUTS[C.CONFIG_KEY_FOLDERS]
