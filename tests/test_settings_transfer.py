"""설정 내보내기·불러오기와 폴더 없음 표시(7.1.30)."""
import json
import os
import re
from datetime import datetime

import pytest

from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants as C
from ui.settings_transfer import (
    SettingsImportDialog,
    export_file_name,
    read_export_file,
    write_export_file,
)

ALL = list(C.SETTINGS_SECTIONS)


def _customize(manager, tmp_path):
    manager.set(C.CONFIG_KEY_THEME, "Light")
    manager.set(C.CONFIG_KEY_CONTEXT_BEFORE_LINES, 7)
    manager.set(C.CONFIG_KEY_GEOMETRY, "R0VPTQ==")
    manager.set(C.CONFIG_KEY_HISTORY, ["alpha", "beta"])
    manager.set(C.CONFIG_KEY_FILENAME_HISTORY, ["fname"])
    manager.update_filters({str(tmp_path): True, "Z:/nowhere": False}, {"extensions": {"txt": True}},
                           {"filenames": {"String_": True}},
                           folder_groups=[{"name": "G", "collapsed": False, "folders": ["Z:/nowhere"]}])


def test_export_file_name_has_fixed_prefix_and_timestamp():
    name = export_file_name(datetime(2026, 11, 15, 14, 25, 30))
    assert name == "StringFinder_Settings_20261115_142530.json"
    assert re.fullmatch(r"StringFinder_Settings_\d{8}_\d{6}\.json", export_file_name())


def test_export_contains_four_sections_and_no_tabs_or_filename_history(mock_config_manager, tmp_path):
    _customize(mock_config_manager, tmp_path)
    mock_config_manager.set_tab_order(["A", "B"])
    payload = mock_config_manager.build_settings_export("2026-11-15 14:25:30")
    assert payload["format"] == C.SETTINGS_EXPORT_FORMAT and payload["format_version"] == 1
    sections = payload["sections"]
    assert set(sections) == set(ALL)
    assert sections["settings"][C.CONFIG_KEY_THEME] == "Light"
    assert sections["layout"][C.CONFIG_KEY_GEOMETRY] == "R0VPTQ=="
    assert sections["history"][C.CONFIG_KEY_HISTORY] == ["alpha", "beta"]
    assert str(tmp_path) in sections["conditions"][C.CONFIG_KEY_FILTERS][C.CONFIG_KEY_FOLDERS]
    flat = json.dumps(sections)
    assert C.CONFIG_KEY_TABS not in sections["settings"] and "fname" not in flat
    assert C.CONFIG_KEY_CURRENT_TAB not in sections["settings"]


def test_round_trip_restores_all_sections_backs_up_and_freezes(mock_config_manager, tmp_path):
    _customize(mock_config_manager, tmp_path)
    path = tmp_path / export_file_name()
    write_export_file(str(path), mock_config_manager.build_settings_export("now"))
    for key in (C.CONFIG_KEY_THEME, C.CONFIG_KEY_GEOMETRY, C.CONFIG_KEY_HISTORY):
        mock_config_manager.set(key, mock_config_manager._defaults[key])
    mock_config_manager.update_filters({}, {}, {})
    mock_config_manager.save_immediately()

    data = mock_config_manager.parse_settings_export(read_export_file(str(path)))
    backup = mock_config_manager.import_settings(data, ALL, "backup_test.json")
    assert os.path.exists(backup)
    saved = json.loads(open(mock_config_manager.config_path, encoding="utf-8").read())
    assert saved[C.CONFIG_KEY_THEME] == "Light" and saved[C.CONFIG_KEY_GEOMETRY] == "R0VPTQ=="
    assert saved[C.CONFIG_KEY_HISTORY] == ["alpha", "beta"]
    assert str(tmp_path) in saved[C.CONFIG_KEY_FILTERS][C.CONFIG_KEY_FOLDERS]
    assert saved[C.CONFIG_KEY_IMPORTED_CONDITIONS] is True
    # 종료하는 동안 다른 값이 저장되어 불러온 설정을 덮어쓰지 않습니다.
    mock_config_manager.set(C.CONFIG_KEY_THEME, "Dark")
    mock_config_manager.save_immediately()
    assert json.loads(open(mock_config_manager.config_path, encoding="utf-8").read())[C.CONFIG_KEY_THEME] == "Light"


def test_only_chosen_sections_are_overwritten(mock_config_manager, tmp_path):
    _customize(mock_config_manager, tmp_path)
    payload = mock_config_manager.build_settings_export("now")
    mock_config_manager.set(C.CONFIG_KEY_THEME, "Dark")
    mock_config_manager.set(C.CONFIG_KEY_HISTORY, ["mine"])
    mock_config_manager.import_settings(payload, ["history"], "b.json")
    assert mock_config_manager.get(C.CONFIG_KEY_THEME) == "Dark"
    assert mock_config_manager.get(C.CONFIG_KEY_HISTORY) == ["alpha", "beta"]
    assert mock_config_manager.get(C.CONFIG_KEY_IMPORTED_CONDITIONS) is False


def test_old_file_ignores_unknown_and_defaults_missing_or_invalid(mock_config_manager):
    mock_config_manager.set(C.CONFIG_KEY_THEME, "Light")
    mock_config_manager.set(C.CONFIG_KEY_CONTEXT_BEFORE_LINES, 7)
    old = {
        "format": C.SETTINGS_EXPORT_FORMAT, "format_version": 1, "config_version": 1,
        "sections": {"settings": {
            "obsolete_setting": 123,                       # 지금은 없는 설정 → 무시
            C.CONFIG_KEY_CONTEXT_BEFORE_LINES: 99999,      # 범위 밖 → 범위 안으로
            # 테마 없음 → 기본값
        }},
    }
    mock_config_manager.import_settings(old, ALL, "b.json")
    assert "obsolete_setting" not in mock_config_manager._config
    assert mock_config_manager.get(C.CONFIG_KEY_THEME) == C.DEFAULT_THEME
    assert mock_config_manager.get(C.CONFIG_KEY_CONTEXT_BEFORE_LINES) <= C.MAX_CONTEXT_PREVIEW_LINES


@pytest.mark.parametrize("data,reason", [
    ([1, 2], "invalid"),
    ({"format": "Other", "format_version": 1, "sections": {}}, "invalid"),
    ({"format": C.SETTINGS_EXPORT_FORMAT, "format_version": 1}, "invalid"),
    ({"format": C.SETTINGS_EXPORT_FORMAT, "format_version": "1", "sections": {}}, "invalid"),
    ({"format": C.SETTINGS_EXPORT_FORMAT, "format_version": 99, "sections": {}}, "newer"),
])
def test_non_stringfinder_or_newer_files_are_rejected(mock_config_manager, data, reason):
    before = json.dumps(mock_config_manager._config, sort_keys=True, default=str)
    with pytest.raises(ValueError, match=reason):
        mock_config_manager.import_settings(data, ALL, "b.json")
    assert json.dumps(mock_config_manager._config, sort_keys=True, default=str) == before


def test_unreadable_file_is_invalid(tmp_path):
    bad = tmp_path / "anything.txt"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid"):
        read_export_file(str(bad))


def test_import_dialog_checkboxes_missing_section_and_folder_status(qtbot, mock_config_manager, tmp_path):
    _customize(mock_config_manager, tmp_path)
    payload = mock_config_manager.build_settings_export("2026-11-15 14:25:30")
    del payload["sections"]["layout"]
    dialog = SettingsImportDialog(payload, "x/StringFinder_Settings_1.json")
    qtbot.addWidget(dialog)
    assert not dialog.checkboxes["layout"].isEnabled()
    assert dialog.descriptions["layout"].text() == AppStrings.SETTINGS_SECTION_MISSING
    # 체크 상자와 설명이 두 칸으로 정렬되어 있습니다(7.1.31).
    dialog.show()
    xs = {dialog.descriptions[section].mapTo(dialog, dialog.descriptions[section].rect().topLeft()).x() for section in ALL}
    assert len(xs) == 1
    assert dialog.selected_sections() == ["settings", "conditions", "history"]
    qtbot.waitUntil(lambda: dialog.folder_status_label.text() != AppStrings.SETTINGS_IMPORT_CHECKING_FOLDERS)
    assert dialog.folder_status_label.text() == AppStrings.SETTINGS_IMPORT_MISSING_FOLDERS.format(2, 1)
    assert "Z:/nowhere" in dialog.folder_status_label.toolTip()
    for box in dialog.checkboxes.values():
        box.setChecked(False)
    ok = dialog.buttons.button(dialog.buttons.StandardButton.Ok)
    assert not ok.isEnabled()
    labels = [widget.text() for widget in dialog.findChildren(type(dialog.folder_status_label))]
    assert AppStrings.SETTINGS_IMPORT_RESTART_NOTE in labels


def test_folder_list_marks_missing_folders(qtbot, tmp_path):
    from ui.panels import FolderFilterPanel

    panel = FolderFilterPanel()
    qtbot.addWidget(panel)
    missing = str(tmp_path / "gone")
    panel.restore_state({str(tmp_path): True, missing: True})
    widgets = {panel.folder_list.itemWidget(panel.folder_list.item(r)).text(): panel.folder_list.itemWidget(panel.folder_list.item(r))
               for r in range(panel.folder_list.count())}
    qtbot.waitUntil(lambda: widgets[missing].is_missing())
    assert not widgets[str(tmp_path)].is_missing()
    assert widgets[missing].toolTip() == AppStrings.FOLDER_MISSING_TOOLTIP
    assert widgets[missing].clone().is_missing()
    os.makedirs(missing)
    panel.refresh_folder_status()
    qtbot.waitUntil(lambda: not panel.folder_list.itemWidget(panel.folder_list.item(1)).is_missing())


def test_imported_conditions_are_applied_to_first_visible_tab(qtbot, mock_config_manager, tmp_path):
    from ui.main_window import MainWindow

    mock_config_manager.update_filters({str(tmp_path): True}, {"extensions": {"md": True}}, {"filenames": {}})
    mock_config_manager.set(C.CONFIG_KEY_IMPORTED_CONDITIONS, True)
    window = MainWindow()
    qtbot.addWidget(window)
    tab = window.tab_widget.currentWidget()
    assert str(tmp_path) in tab.folder_panel.get_state()
    assert mock_config_manager.get(C.CONFIG_KEY_IMPORTED_CONDITIONS) is False


def test_settings_dialog_has_export_and_import_buttons(qtbot, mock_config_manager):
    from ui.settings_dialog import SettingsDialog

    dialog = SettingsDialog(mock_config_manager)
    qtbot.addWidget(dialog)
    fired = []
    dialog.export_settings_requested.connect(lambda: fired.append("export"))
    dialog.import_settings_requested.connect(lambda: fired.append("import"))
    dialog.export_settings_btn.click()
    dialog.import_settings_btn.click()
    assert fired == ["export", "import"]
    assert dialog.export_settings_btn.text() == AppStrings.SETTINGS_EXPORT_BTN


def test_main_window_export_then_import_closes_program(qtbot, mock_config_manager, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    from ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    infos, warnings = [], []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: infos.append(a[2]))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a[2]))
    target = tmp_path / "exported.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    assert window.export_settings() == str(target)
    assert json.loads(target.read_text(encoding="utf-8"))["format"] == C.SETTINGS_EXPORT_FORMAT

    bogus = tmp_path / "bogus.json"
    bogus.write_text(json.dumps({"hello": 1}), encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(bogus), ""))
    assert window.import_settings() is False and warnings[-1] == AppStrings.SETTINGS_IMPORT_INVALID

    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(target), ""))
    monkeypatch.setattr(SettingsImportDialog, "exec", lambda self: 1)
    assert window.import_settings() is True
    qtbot.waitUntil(lambda: not window.isVisible())


def test_settings_dialog_groups_logs_and_data_in_advanced_tab(qtbot, mock_config_manager):
    """로그·데이터 폴더·전체 삭제는 고급 탭, 내보내기·불러오기는 일반 탭의 '설정 관리' 묶음입니다(7.1.32)."""
    from PySide6.QtWidgets import QTabWidget

    from ui.settings_dialog import SettingsDialog

    dialog = SettingsDialog(mock_config_manager)
    qtbot.addWidget(dialog)
    tabs = dialog.findChild(QTabWidget)
    titles = [tabs.tabText(i) for i in range(tabs.count())]
    general = tabs.widget(titles.index(AppStrings.SETTINGS_GROUP_GENERAL))
    advanced = tabs.widget(titles.index(AppStrings.SETTINGS_GROUP_ADVANCED))

    def inside(widget, page):
        while widget is not None:
            if widget is page:
                return True
            widget = widget.parentWidget()
        return False

    assert dialog.settings_manage_group.title() == AppStrings.SETTINGS_GROUP_MANAGE
    assert inside(dialog.export_settings_btn, dialog.settings_manage_group)
    assert inside(dialog.import_settings_btn, dialog.settings_manage_group)
    assert inside(dialog.settings_manage_group, general)
    for widget in (dialog.log_group, dialog.open_data_dir_btn, dialog.clear_data_btn):
        assert inside(widget, advanced) and not inside(widget, general)


def test_failed_import_save_leaves_memory_and_later_saves_untouched(mock_config_manager, tmp_path, monkeypatch):
    """저장이 실패하면 메모리 설정이 바뀌지 않고, 이후 저장에도 불러온 값이 들어가지 않습니다(7.1.33)."""
    mock_config_manager.set(C.CONFIG_KEY_THEME, "Dark")
    mock_config_manager.save_immediately()
    payload = mock_config_manager.build_settings_export("now")
    payload["sections"]["settings"][C.CONFIG_KEY_THEME] = "Light"
    original_save = mock_config_manager.save_immediately
    monkeypatch.setattr(mock_config_manager, "save_immediately", lambda snapshot=None: False)
    with pytest.raises(OSError):
        mock_config_manager.import_settings(payload, ALL, "b.json")
    assert mock_config_manager.get(C.CONFIG_KEY_THEME) == "Dark"
    assert mock_config_manager.get(C.CONFIG_KEY_IMPORTED_CONDITIONS) is False
    monkeypatch.setattr(mock_config_manager, "save_immediately", original_save)
    assert mock_config_manager.save_immediately()
    saved = json.loads(open(mock_config_manager.config_path, encoding="utf-8").read())
    assert saved[C.CONFIG_KEY_THEME] == "Dark"


def test_failed_backup_changes_nothing(mock_config_manager, monkeypatch):
    import shutil

    mock_config_manager.set(C.CONFIG_KEY_THEME, "Dark")
    mock_config_manager.save_immediately()
    payload = mock_config_manager.build_settings_export("now")
    payload["sections"]["settings"][C.CONFIG_KEY_THEME] = "Light"
    monkeypatch.setattr(shutil, "copy2", lambda *a, **k: (_ for _ in ()).throw(PermissionError("denied")))
    with pytest.raises(OSError):
        mock_config_manager.import_settings(payload, ALL, "b.json")
    assert mock_config_manager.get(C.CONFIG_KEY_THEME) == "Dark"


def test_only_latest_five_import_backups_are_kept(mock_config_manager):
    mock_config_manager.save_immediately()
    folder = mock_config_manager.config_dir
    for i in range(7):
        open(os.path.join(folder, f"config_backup_before_import_2026010{i}_000000.json"), "w").close()
    payload = mock_config_manager.build_settings_export("now")
    mock_config_manager.import_settings(payload, ["history"], "config_backup_before_import_20991231_235959.json")
    kept = sorted(n for n in os.listdir(folder) if n.startswith("config_backup_before_import_"))
    assert len(kept) == 5 and kept[-1] == "config_backup_before_import_20991231_235959.json"
    assert "config_backup_before_import_20260100_000000.json" not in kept


def test_import_dialog_shows_custom_line_opener(qtbot, mock_config_manager):
    payload = mock_config_manager.build_settings_export("now")
    plain = SettingsImportDialog(payload, "a.json")
    qtbot.addWidget(plain)
    assert plain.editor_label is None
    payload["sections"]["settings"][C.CONFIG_KEY_EXTERNAL_EDITOR] = {
        C.CONFIG_KEY_EDITOR_TYPE: "custom", C.CONFIG_KEY_EDITOR_CUSTOM_PATH: r"C:\Tools\open.exe"}
    dialog = SettingsImportDialog(payload, "a.json")
    qtbot.addWidget(dialog)
    assert dialog.editor_label.text() == AppStrings.SETTINGS_IMPORT_EDITOR_INFO.format(r"C:\Tools\open.exe")
