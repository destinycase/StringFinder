"""설정·검색 히스토리를 모든 탭이 함께 쓰는지 확인합니다(7.1.8)."""
from sf_utils.constants import Constants as C


def _window(qtbot):
    from ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    return window


def _items(combo):
    return [combo.itemText(i) for i in range(combo.count())]


def test_history_added_in_one_tab_appears_in_other_tab(qtbot, mock_config_manager):
    window = _window(qtbot)
    first = window.add_new_tab("A")
    second = window.add_new_tab("B")
    window.tab_widget.setCurrentWidget(second)
    mock_config_manager.add_history("shared-word")
    second._load_histories()
    window.tab_widget.setCurrentWidget(first)
    assert "shared-word" in _items(first.search_panel.search_combo)


def test_context_lines_changed_in_one_tab_apply_to_other_tab(qtbot, mock_config_manager):
    window = _window(qtbot)
    first = window.add_new_tab("A")
    second = window.add_new_tab("B")
    second.result_view_panel.context_before_combo.setCurrentIndex(5)
    assert mock_config_manager.get(C.CONFIG_KEY_CONTEXT_BEFORE_LINES) == 5
    window.tab_widget.setCurrentWidget(first)
    assert first.result_view_panel.context_before_combo.currentIndex() == 5


def test_result_splitter_changed_in_one_tab_applies_to_other_tab(qtbot, mock_config_manager):
    window = _window(qtbot)
    window.resize(1200, 800)
    window.show()
    first = window.add_new_tab("A")
    second = window.add_new_tab("B")
    splitter = second.result_view_panel.result_splitter
    total = sum(splitter.sizes())
    splitter.setSizes([total // 4, total - total // 4])
    expected = splitter.sizes()
    window.tab_widget.setCurrentWidget(first)
    assert first.result_view_panel.result_splitter.sizes() == expected


def test_dock_layout_is_saved_when_leaving_a_tab_and_applied_to_next(qtbot, mock_config_manager):
    from PySide6.QtCore import Qt

    window = _window(qtbot)
    window.show()
    qtbot.waitUntil(lambda: window._shared_layout_applied)
    first = window.add_new_tab("A")
    second = window.add_new_tab("B")
    second.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, second.folder_dock)
    window.tab_widget.setCurrentWidget(first)
    assert mock_config_manager.get_dock_state()
    assert first.dockWidgetArea(first.folder_dock) == Qt.DockWidgetArea.RightDockWidgetArea


def test_search_dock_height_survives_restart(qtbot, mock_config_manager):
    """검색 설정 창 높이를 줄이고 다시 켜도 같은 높이로 열립니다(7.1.15)."""
    from PySide6.QtCore import Qt
    from ui.main_window import MainWindow

    def open_window():
        window = MainWindow()
        qtbot.addWidget(window)
        window.resize(1400, 900)
        window.show()
        qtbot.waitUntil(lambda: window._shared_layout_applied)
        qtbot.wait(20)
        return window, window.tab_widget.currentWidget()

    window, tab = open_window()
    tab.resizeDocks([tab.search_dock], [60], Qt.Orientation.Vertical)
    qtbot.wait(20)
    height = tab.search_dock.height()
    window.close()

    window, tab = open_window()
    assert tab.search_dock.height() == height
    window.close()
