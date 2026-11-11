"""Combined filter tabs and selection-only status must preserve search inputs."""
import os

import pytest
from PySide6.QtWidgets import QDockWidget

from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants as C
from sf_utils.localization import get_language, set_language
from ui.main_window import MainWindow
from ui.search_tab import SearchTab

def choose_search_mode(panel, mode):
    """사용자가 검색 모드 콤보 박스에서 ``mode``(저장 값)를 고른 것과 같이 처리합니다."""
    index = panel.search_mode_combo.findData(mode)
    assert index >= 0
    panel.search_mode_combo.setCurrentIndex(index)
    panel.search_mode_combo.activated.emit(index)



@pytest.fixture(params=["ko", "en"])
def window(qtbot, mock_config_manager, request):
    previous = get_language()
    set_language(request.param)
    widget = MainWindow()
    qtbot.addWidget(widget)
    yield widget
    widget.close()
    set_language(previous)


def test_single_filter_dock_and_distinct_extension_count(window):
    tab = window.tab_widget.currentWidget()
    # 기본은 위아래 패널: 확장자·파일명 필터가 한 도크 안에 동시에 보입니다.
    assert tab.filter_tabs.count() == 0 and tab.filter_tabs.isHidden()
    assert tab.ext_group.isAncestorOf(tab.ext_panel)
    assert tab.filename_group.isAncestorOf(tab.filename_panel)
    assert {d.objectName() for d in tab.findChildren(QDockWidget)} == {
        C.OBJ_NAME_SEARCH_DOCK, C.OBJ_NAME_FOLDER_DOCK, "filtersDockV2", "resultsDock"
    }
    tab.ext_panel.restore_state({})
    tab.filename_panel.restore_state({})
    tab._refresh_filter_ui()
    tab.ext_panel.add_extension("xlsx,xlsm,xls,xlsb")
    tab.ext_panel.add_extension("xlsx,txt")
    assert tab.filter_titles()[0] == AppStrings.FILTER_EXTENSION_TAB.format(5)
    tab.ext_panel.ext_list.itemWidget(tab.ext_panel.ext_list.item(1)).checkbox.setChecked(False)
    assert tab.filter_titles()[0] == AppStrings.FILTER_EXTENSION_TAB.format(4)
    tab.filename_panel.add_filename("report", checked=False)
    assert tab.filter_titles()[1] == AppStrings.FILTER_FILENAME_TAB.format(0)
    tab.filename_panel.toggle_all(True)
    assert tab.filter_titles()[1] == AppStrings.FILTER_FILENAME_TAB.format(1)
    tab.ext_panel.set_special_mode(AppStrings.SPECIAL_SEARCH_ITEMS[3])
    assert tab.ext_panel.get_selected_extensions() == [C.EXT_JSON]
    assert tab.filter_titles()[0] == AppStrings.FILTER_EXTENSION_TAB.format(4)
    assert ".." not in window.filter_status_label.text()
    for panel in (tab.folder_panel, tab.ext_panel, tab.filename_panel):
        assert panel.status_label.isHidden()


def test_active_tab_status_is_not_overwritten_by_search_or_background_tab(window):
    first = window.tab_widget.currentWidget()
    first.folder_panel.restore_state({"C:/one": True})
    first._refresh_filter_ui()
    first_status = first.get_filter_status_text()
    second = window.add_new_tab()
    second.folder_panel.restore_state({"C:/two": True, "C:/three": True})
    second._refresh_filter_ui()
    assert window.filter_status_label.text() == second.get_filter_status_text()
    window.tab_widget.setCurrentWidget(first)
    assert window.filter_status_label.text() == first_status
    second.ext_panel.add_extension("rare_extension")
    assert window.filter_status_label.text() == first_status
    first._on_progress(1, 2)
    first._on_worker_finished()
    assert window.filter_status_label.text() == first_status
    assert window.statusBar().currentMessage() == ""
    assert not hasattr(window, "status_timer_label")
    assert not hasattr(window, "status_spinner")


def test_search_settings_prefix_refreshes_immediately_and_follows_active_tab(window):
    first = window.tab_widget.currentWidget()
    second = window.add_new_tab()
    window.tab_widget.setCurrentWidget(first)
    for index in (0, 4, 6):
        choose_search_mode(first.search_panel, AppStrings.SPECIAL_SEARCH_ITEMS[index])
        parts = window.filter_status_label.text().split(" | ")
        assert len(parts) == 4
        assert parts[0] == first.search_panel.search_mode_combo.currentText()
        assert parts[1] == first.folder_panel.status_label.text()
    original = window.filter_status_label.text()
    second.search_panel.set_special_mode(AppStrings.SPECIAL_SEARCH_ITEMS[3])
    assert window.filter_status_label.text() == original
    window.tab_widget.setCurrentWidget(second)
    assert window.filter_status_label.text() == second.get_filter_status_text()


def test_restoration_lock_and_layout_fallback(window):
    tab = window.tab_widget.currentWidget()
    tab.ext_panel.restore_state({})
    tab.filename_panel.restore_state({})
    tab.ext_panel.add_extension("xlsx,xlsm,xls,xlsb")
    tab.filename_panel.add_filename("report")
    saved = tab.get_state()
    restored = window.add_new_tab(state=saved)
    assert restored.filter_titles()[0] == AppStrings.FILTER_EXTENSION_TAB.format(4)
    assert restored.filter_titles()[1] == AppStrings.FILTER_FILENAME_TAB.format(1)
    assert window.filter_status_label.text() == restored.get_filter_status_text()
    restored._set_inputs_enabled(False)
    assert not restored.filter_dock.isEnabled()
    assert not restored.search_panel.search_mode_combo.isEnabled()
    restored._set_inputs_enabled(True)
    restored.reset_layout()
    assert not restored.filter_dock.isHidden()
    # Pre-merge Qt layouts must not resurrect obsolete individual docks.
    legacy = SearchTab(window.config_manager)
    old_state = legacy.saveState(0).toHex().data().decode()
    legacy.cleanup()
    legacy.deleteLater()
    window.config_manager.set(C.CONFIG_KEY_DOCK_LAYOUT_STATE, old_state)
    new = window.add_new_tab()
    assert not new.filter_dock.isHidden()
    assert new.filter_splitter.count() == 2 and not new.filter_splitter.isHidden()


def test_errors_remain_visible_without_replacing_filter_status(window):
    before = window.filter_status_label.text()
    window.tab_widget.currentWidget()._report_file_open_failure("missing.txt")
    assert window.filter_status_label.text() == before
    assert window._notice_dialog.isVisible()
    assert not window._notice_dialog.isModal()
    assert "missing.txt" in window._notice_dialog.text()
    window._notice_dialog.close()


def test_session_save_failure_does_not_hide_filters_or_block(window, monkeypatch):
    before = window.filter_status_label.text()
    monkeypatch.setattr(window.config_manager, "save_session", lambda *args: False)
    assert window._save_tab(0) is False
    assert window.filter_status_label.text() == before
    assert window._notice_dialog.isVisible() and not window._notice_dialog.isModal()
    assert window._notice_dialog.text() == AppStrings.ERROR_SESSION_SAVE.format(window.tab_widget.tabText(0))
    window._notice_dialog.close()


def test_filter_layout_option_switches_between_stacked_panels_and_tabs(window):
    from ui.settings_dialog import SettingsDialog

    tab = window.tab_widget.currentWidget()
    second = window.add_new_tab()
    window.tab_widget.setCurrentWidget(tab)
    assert window.config_manager.get(C.CONFIG_KEY_FILTER_LAYOUT) == C.FILTER_LAYOUT_STACKED
    tab.ext_panel.restore_state({"txt": True, "log": False})
    tab.filename_panel.restore_state({})
    tab.filename_panel.add_filename("report")
    tab._refresh_filter_ui()
    assert tab.ext_group.title() == AppStrings.FILTER_EXTENSION_TAB.format(1)
    assert tab.filename_group.title() == AppStrings.FILTER_FILENAME_TAB.format(1)
    dialog = SettingsDialog(window.config_manager, window)
    dialog.display_density_changed.connect(window._apply_display_density)
    combo = dialog.filter_layout_combo
    assert [combo.itemData(i) for i in range(combo.count())] == [C.FILTER_LAYOUT_STACKED, C.FILTER_LAYOUT_TABS]
    combo.setCurrentIndex(combo.findData(C.FILTER_LAYOUT_TABS))
    assert window.config_manager.get(C.CONFIG_KEY_FILTER_LAYOUT) == C.FILTER_LAYOUT_TABS
    for current in (tab, second):
        # 열린 모든 탭에 바로 반영되고 선택 상태는 그대로입니다.
        assert current.filter_tabs.count() == 2 and current.filter_splitter.isHidden()
        assert current.filter_tabs.widget(0) is current.ext_panel
        assert current.filter_tabs.widget(1) is current.filename_panel
    assert tab.filter_tabs.tabText(0) == AppStrings.FILTER_EXTENSION_TAB.format(1)
    assert tab.filter_tabs.tabText(1) == AppStrings.FILTER_FILENAME_TAB.format(1)
    assert tab.ext_panel.get_selected_extensions() == ["txt"]
    assert tab.filename_panel.get_selected_filenames() == ["report"]
    combo.setCurrentIndex(combo.findData(C.FILTER_LAYOUT_STACKED))
    assert tab.filter_tabs.count() == 0 and not tab.filter_splitter.isHidden()
    # 탭에서 돌아온 패널의 내용이 숨겨진 채로 남지 않아야 합니다(6.4.12 오류).
    window.show()
    for current in (tab, second):
        window.tab_widget.setCurrentWidget(current)
        assert current.ext_panel.isVisible() and current.filename_panel.isVisible()
    window.tab_widget.setCurrentWidget(tab)
    for _ in range(2):
        combo.setCurrentIndex(combo.findData(C.FILTER_LAYOUT_TABS))
        assert tab.filter_tabs.currentWidget().isVisible()
        combo.setCurrentIndex(combo.findData(C.FILTER_LAYOUT_STACKED))
        assert tab.ext_panel.isVisible() and tab.filename_panel.isVisible()
    assert tab.filename_panel.get_selected_filenames() == ["report"]
    dialog.close()


def test_invalid_saved_filter_layout_falls_back_to_stacked(mock_config_manager):
    import json
    from sf_utils.config_manager import ConfigManager

    path = mock_config_manager.config_path
    data = json.loads(open(path, encoding="utf-8").read()) if os.path.exists(path) else {}
    data[C.CONFIG_KEY_FILTER_LAYOUT] = "sideways"
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    mock_config_manager.stop()
    ConfigManager._instance = None
    reloaded = ConfigManager()
    try:
        assert reloaded.get(C.CONFIG_KEY_FILTER_LAYOUT) == C.FILTER_LAYOUT_STACKED
    finally:
        reloaded.stop()
