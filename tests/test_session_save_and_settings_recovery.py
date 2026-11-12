"""세션 저장·읽기와 설정 파일 복구: 세션 이름 앞부분 읽기, 백그라운드 저장, 손상 설정 백업."""
import json
import os
import sys

from PySide6.QtWidgets import QFileIconProvider

from ui.result_view import ResultView

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


def _view(qtbot, mock_config_manager):
    view = ResultView(QFileIconProvider(), mock_config_manager)
    qtbot.addWidget(view)
    return view


def test_cleared_data_is_not_resaved_on_close(qtbot, mock_config_manager):
    from ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    window._save_tab(0)
    sessions = mock_config_manager.sessions_dir
    assert os.listdir(sessions)
    mock_config_manager.clear_all_data()
    assert os.listdir(sessions) == []
    window.close()
    assert os.listdir(sessions) == []


def test_unreadable_settings_are_backed_up(mock_config_manager):
    from sf_utils.config_manager import ConfigManager

    path = mock_config_manager.config_path
    mock_config_manager.stop()
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("{ broken json")
    ConfigManager._instance = None
    reloaded = ConfigManager()
    try:
        backups = [name for name in os.listdir(os.path.dirname(path)) if ".corrupt-" in name]
        assert backups
        with open(os.path.join(os.path.dirname(path), backups[0]), encoding="utf-8") as handle:
            assert handle.read() == "{ broken json"
    finally:
        reloaded.stop()


def test_session_title_is_read_from_the_head(mock_config_manager, monkeypatch):
    mock_config_manager.save_session("탭 이름", {"results": [["a", 1, []]] * 10, "title": "탭 이름"})
    path = mock_config_manager._find_existing_session_file("탭 이름")
    with open(path, encoding="utf-8") as handle:
        assert handle.read(40).startswith('{"__sf_session_name":"탭 이름"')
    monkeypatch.setattr(mock_config_manager, "_read_session_title_full",
                        lambda _p: (_ for _ in ()).throw(AssertionError("full parse")))
    mock_config_manager.__dict__.pop("_session_title_cache", None)
    assert mock_config_manager.get_all_session_names() == ["탭 이름"]
    assert mock_config_manager.load_session("탭 이름")["title"] == "탭 이름"


def test_old_session_files_are_parsed_once(mock_config_manager, monkeypatch):
    path = os.path.join(mock_config_manager.sessions_dir, "old.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"results": [], "title": "old", "__sf_session_name": "old"}, handle)
    calls = []
    original = mock_config_manager._read_session_title_full
    monkeypatch.setattr(mock_config_manager, "_read_session_title_full", lambda p: calls.append(p) or original(p))
    for _ in range(3):
        assert mock_config_manager.get_all_session_names() == ["old"]
    assert mock_config_manager.load_session("old") is not None
    assert len(calls) == 1


def test_search_finish_saves_in_background_and_close_waits(qtbot, mock_config_manager, monkeypatch):
    import threading
    from ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    tab = window.tab_widget.widget(0)
    release = threading.Event()
    threads = []
    original = mock_config_manager._save_session_locked

    def slow_save(name, data):
        threads.append(threading.current_thread().name)
        release.wait(5)
        return original(name, data)

    monkeypatch.setattr(mock_config_manager, "_save_session_locked", slow_save)
    window._on_search_finished_in_tab(tab)  # 저장이 끝나지 않아도 바로 돌아옵니다.
    assert not release.is_set()
    release.set()
    mock_config_manager.wait_for_session_saves(5)
    assert threads and threads[0] != threading.main_thread().name
    assert os.listdir(mock_config_manager.sessions_dir)
    window.close()


def test_clear_all_data_waits_for_pending_saves(mock_config_manager, monkeypatch):
    import threading
    import time

    started = threading.Event()
    original = mock_config_manager._save_session_locked

    def slow_save(name, data):
        started.set()
        time.sleep(0.3)
        return original(name, data)

    monkeypatch.setattr(mock_config_manager, "_save_session_locked", slow_save)
    mock_config_manager.save_session_async("late", {"results": []})
    started.wait(5)
    mock_config_manager.clear_all_data()
    # 늦게 끝난 저장이 지운 세션을 되살리지 않습니다.
    assert os.listdir(mock_config_manager.sessions_dir) == []
