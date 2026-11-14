"""다시 켰을 때 마지막으로 보던 탭을 보여 주는지 확인합니다(7.1.16)."""
import pytest
from PySide6.QtCore import Qt

from sf_utils.constants import Constants as C


def _open(qtbot):
    from ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    window.resize(1400, 900)
    window.show()
    qtbot.waitUntil(lambda: window._shared_layout_applied)
    qtbot.wait(20)
    return window


def _names(window):
    return [window.tab_widget.tabText(i) for i in range(window.tab_widget.count())]


def _make_three_tabs(qtbot):
    window = _open(qtbot)
    first_name = window.tab_widget.tabText(0)
    window.add_new_tab("B")
    window.add_new_tab("C")
    return window, first_name


def _reload_config_from_disk(manager, monkeypatch):
    """정상 종료 없이 꺼진 상황: 저장된 파일만 남기고 설정을 새로 읽습니다."""
    from sf_utils.config_manager import ConfigManager

    manager.stop()
    ConfigManager._instance = None
    return ConfigManager()


def test_middle_tab_is_shown_after_restart(qtbot, mock_config_manager):
    window, _ = _make_three_tabs(qtbot)
    window.tab_widget.setCurrentIndex(1)
    window.close()
    window = _open(qtbot)
    assert window.tab_widget.tabText(window.tab_widget.currentIndex()) == "B"
    window.close()


@pytest.mark.parametrize("index", [0, 2])
def test_first_and_last_tab_are_shown_after_restart(qtbot, mock_config_manager, index):
    window, _ = _make_three_tabs(qtbot)
    window.tab_widget.setCurrentIndex(index)
    expected = window.tab_widget.tabText(index)
    window.close()
    window = _open(qtbot)
    assert window.tab_widget.tabText(window.tab_widget.currentIndex()) == expected
    window.close()


def test_last_switched_tab_survives_without_normal_shutdown(qtbot, mock_config_manager, monkeypatch):
    window, _ = _make_three_tabs(qtbot)
    for i in range(window.tab_widget.count()):
        window._save_tab(i)
    window.tab_widget.setCurrentIndex(1)
    manager = _reload_config_from_disk(mock_config_manager, monkeypatch)
    try:
        assert manager.get_current_tab() == "B"
    finally:
        manager.stop()


def test_startup_restore_does_not_overwrite_saved_tab(qtbot, mock_config_manager):
    window, _ = _make_three_tabs(qtbot)
    window.tab_widget.setCurrentIndex(1)
    window.close()
    window = _open(qtbot)
    # 시작 중 탭을 하나씩 추가하며 마지막 탭이 잠깐 보였어도 저장값은 그대로입니다.
    assert mock_config_manager.get_current_tab() == "B"
    window.close()


def test_old_config_without_saved_tab_opens_last_tab(qtbot, mock_config_manager):
    window, _ = _make_three_tabs(qtbot)
    window.close()
    mock_config_manager._config.pop(C.CONFIG_KEY_CURRENT_TAB, None)
    window = _open(qtbot)
    assert window.tab_widget.currentIndex() == window.tab_widget.count() - 1
    window.close()


def test_missing_saved_tab_opens_without_error(qtbot, mock_config_manager):
    window, _ = _make_three_tabs(qtbot)
    window.close()
    mock_config_manager.set_current_tab("없는 탭")
    window = _open(qtbot)
    assert _names(window)[window.tab_widget.currentIndex()] == "C"
    window.close()


def test_renamed_and_closed_tabs_update_saved_tab(qtbot, mock_config_manager, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    window, _ = _make_three_tabs(qtbot)
    window.tab_widget.setCurrentIndex(1)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("B2", True))
    window._rename_tab(1)
    assert mock_config_manager.get_current_tab() == "B2"
    window._close_tab(1)
    assert mock_config_manager.get_current_tab() == window.tab_widget.tabText(window.tab_widget.currentIndex())
    assert "B2" not in _names(window)
    window.close()


@pytest.mark.parametrize("index", [0, 1, 2])
def test_dock_height_kept_when_restored_tab_varies(qtbot, mock_config_manager, index):
    window, _ = _make_three_tabs(qtbot)
    window.tab_widget.setCurrentIndex(index)
    tab = window.tab_widget.currentWidget()
    tab.resizeDocks([tab.search_dock], [60], Qt.Orientation.Vertical)
    qtbot.wait(20)
    height = tab.search_dock.height()
    window.close()
    window = _open(qtbot)
    assert window.tab_widget.currentIndex() == index
    assert window.tab_widget.currentWidget().search_dock.height() == height
    window.close()


def test_searching_message_shows_search_text_as_plain_text(qtbot, mock_config_manager):
    window = _open(qtbot)
    view = window.tab_widget.currentWidget().result_view_panel
    view.show_empty_message("<b>x</b>", emphasized=True, plain=True)
    assert view.empty_label.textFormat() == Qt.TextFormat.PlainText
    view.show_empty_message("a", emphasized=True)
    assert view.empty_label.textFormat() == Qt.TextFormat.AutoText
    window.close()
