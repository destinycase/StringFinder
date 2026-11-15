"""설정 내보내기·불러오기(7.1.30).

내보내기는 설정·창 크기·검색 조건·검색 히스토리 네 묶음을 한 파일에 담고,
불러오기는 StringFinder 설정 파일인지 확인한 뒤 고른 묶음만 덮어쓰고 프로그램을 종료합니다.
"""
import json
import os
import tempfile
from datetime import datetime

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
)

from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants
from ui.panels import check_paths_async
from ui.styles import UIStyles

SECTION_LABELS = {
    Constants.SETTINGS_SECTION_SETTINGS: ("SETTINGS_SECTION_SETTINGS", "SETTINGS_SECTION_SETTINGS_DESC"),
    Constants.SETTINGS_SECTION_LAYOUT: ("SETTINGS_SECTION_LAYOUT", "SETTINGS_SECTION_LAYOUT_DESC"),
    Constants.SETTINGS_SECTION_CONDITIONS: ("SETTINGS_SECTION_CONDITIONS", "SETTINGS_SECTION_CONDITIONS_DESC"),
    Constants.SETTINGS_SECTION_HISTORY: ("SETTINGS_SECTION_HISTORY", "SETTINGS_SECTION_HISTORY_DESC"),
}


def export_file_name(now=None) -> str:
    """내보내기 파일 이름: 고정 이름 + _년월일_시분초."""
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    return f"{Constants.SETTINGS_EXPORT_FILE_PREFIX}_{stamp}.json"


def write_export_file(path: str, payload: dict) -> None:
    """임시 파일에 쓴 뒤 바꿔 넣어, 쓰다 실패해도 반쯤 쓴 파일이 남지 않게 합니다."""
    folder = os.path.dirname(os.path.abspath(path)) or "."
    handle, temp_path = tempfile.mkstemp(prefix=".sf_settings_", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
        os.replace(temp_path, path)
    except BaseException:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise


def read_export_file(path: str):
    """파일을 읽어 JSON으로 돌려줍니다. 읽거나 해석하지 못하면 ValueError("invalid")."""
    try:
        with open(path, "r", encoding="utf-8-sig") as file:
            return json.load(file)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid") from error


def _conditions_folders(section) -> list:
    filters = section.get(Constants.CONFIG_KEY_FILTERS) if isinstance(section, dict) else None
    folders = filters.get(Constants.CONFIG_KEY_FOLDERS) if isinstance(filters, dict) else None
    if isinstance(folders, dict):
        return [path for path in folders if isinstance(path, str) and path.strip()]
    if isinstance(folders, list):
        return [path for path in folders if isinstance(path, str) and path.strip()]
    return []


class SettingsImportDialog(QDialog):
    """불러올 묶음을 체크 상자로 고르는 창입니다."""

    def __init__(self, data: dict, file_path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(AppStrings.SETTINGS_IMPORT_TITLE)
        self.setMinimumWidth(460)
        sections = data.get("sections", {})
        layout = QVBoxLayout(self)
        info = QLabel(AppStrings.SETTINGS_IMPORT_FILE_INFO.format(
            os.path.basename(file_path), str(data.get("app_version", "?")), str(data.get("exported_at", "?"))))
        info.setWordWrap(True)
        layout.addWidget(info)

        self.checkboxes = {}
        self.folder_status_label = None
        for section in Constants.SETTINGS_SECTIONS:
            title_name, desc_name = SECTION_LABELS[section]
            available = isinstance(sections.get(section), dict)
            desc = getattr(AppStrings, desc_name) if available else AppStrings.SETTINGS_SECTION_MISSING
            box = QCheckBox(f"{getattr(AppStrings, title_name)}  —  {desc}")
            box.setChecked(available)
            box.setEnabled(available)
            box.toggled.connect(self._update_ok_button)
            layout.addWidget(box)
            self.checkboxes[section] = box
            if section == Constants.SETTINGS_SECTION_CONDITIONS and available:
                folders = _conditions_folders(sections[section])
                if folders:
                    self.folder_status_label = QLabel(AppStrings.SETTINGS_IMPORT_CHECKING_FOLDERS)
                    self.folder_status_label.setContentsMargins(24, 0, 0, 0)
                    layout.addWidget(self.folder_status_label)
                    self._folder_worker = check_paths_async(folders, self._on_folders_checked)

        note = QLabel(AppStrings.SETTINGS_IMPORT_RESTART_NOTE)
        note.setWordWrap(True)
        note.setContentsMargins(*UIStyles.GUIDE_BOX_PADDING)
        note.setStyleSheet(UIStyles.STYLE_GUIDE_BOX)
        layout.addWidget(note)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(AppStrings.SETTINGS_IMPORT_BUTTON)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(AppStrings.BTN_CANCEL)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._update_ok_button()

    def _on_folders_checked(self, _request, result: dict):
        if self.folder_status_label is None:
            return
        missing = [path for path, exists in result.items() if not exists]
        if missing:
            self.folder_status_label.setText(AppStrings.SETTINGS_IMPORT_MISSING_FOLDERS.format(len(result), len(missing)))
            self.folder_status_label.setToolTip("\n".join(missing))
        else:
            self.folder_status_label.setText(AppStrings.SETTINGS_IMPORT_ALL_FOLDERS_FOUND.format(len(result)))

    def _update_ok_button(self, *_):
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(self.selected_sections()))

    def selected_sections(self) -> list:
        return [section for section, box in self.checkboxes.items() if box.isEnabled() and box.isChecked()]
