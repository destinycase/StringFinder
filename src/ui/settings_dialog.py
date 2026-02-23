import os
import subprocess
import sys

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from sf_utils.app_strings import AppStrings
from sf_utils.logger import logger
from ui.styles import UIStyles
from ui.widgets import HotkeyLineEdit


class SettingsDialog(QDialog):
    def __init__(self, config_manager, parent=None):
        super().__init__(parent)
        self.config_manager = config_manager
        self.setWindowTitle(AppStrings.SETTINGS_TITLE)
        self.setMinimumWidth(300)
        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        appearance_group = QGroupBox(AppStrings.SETTINGS_GROUP_APPEARANCE)
        appearance_layout = QVBoxLayout(appearance_group)
        theme_row = QHBoxLayout()
        theme_label = QLabel(AppStrings.THEME_LABEL)
        self.theme_combo = QComboBox()
        self.theme_combo.addItem(AppStrings.THEME_DARK, "dark")
        self.theme_combo.addItem(AppStrings.THEME_LIGHT, "light")

        # 현재 설정된 테마가 내부 키일 수도 있고 한국어 명칭일 수도 있으므로 유연하게 처리
        current_theme = self.config_manager.get_theme()
        idx = self.theme_combo.findData(current_theme.lower())
        if idx == -1:
            idx = self.theme_combo.findText(current_theme)
        if idx != -1:
            self.theme_combo.setCurrentIndex(idx)

        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        theme_row.addWidget(theme_label)
        theme_row.addWidget(self.theme_combo, 1)
        appearance_layout.addLayout(theme_row)
        layout_lock_row = QHBoxLayout()
        layout_lock_label = QLabel(AppStrings.MENU_LOCK_LAYOUT + ":")
        self.lock_layout_combo = QComboBox()
        self.lock_layout_combo.addItem(AppStrings.COMBO_UNLOCKED, False)
        self.lock_layout_combo.addItem(AppStrings.COMBO_LOCKED, True)
        is_locked = self.config_manager.get_lock_dock_layout()
        self.lock_layout_combo.setCurrentIndex(self.lock_layout_combo.findData(is_locked))
        self.lock_layout_combo.currentIndexChanged.connect(self._on_lock_layout_changed)
        layout_lock_row.addWidget(layout_lock_label)
        layout_lock_row.addWidget(self.lock_layout_combo, 1)
        appearance_layout.addLayout(layout_lock_row)
        reset_layout_btn = QPushButton(AppStrings.MENU_RESET_LAYOUT)
        reset_layout_btn.clicked.connect(self._reset_layout)
        appearance_layout.addWidget(reset_layout_btn)
        main_layout.addWidget(appearance_group)
        operation_group = QGroupBox(AppStrings.SETTINGS_GROUP_OPERATION)
        operation_layout = QVBoxLayout(operation_group)
        startup_row = QHBoxLayout()
        startup_label = QLabel(AppStrings.STARTUP_LABEL)
        self.startup_combo = QComboBox()
        self.startup_combo.addItem(AppStrings.STARTUP_DISABLE, False)
        self.startup_combo.addItem(AppStrings.STARTUP_ENABLE, True)
        startup_idx = self.startup_combo.findData(self.config_manager.get_run_at_startup())
        if startup_idx != -1:
            self.startup_combo.setCurrentIndex(startup_idx)
        self.startup_combo.currentIndexChanged.connect(self._on_startup_changed)
        startup_row.addWidget(startup_label)
        startup_row.addWidget(self.startup_combo, 1)
        operation_layout.addLayout(startup_row)
        close_row = QHBoxLayout()
        close_label = QLabel(AppStrings.CLOSE_BEHAVIOR_LABEL)
        self.close_behavior_combo = QComboBox()
        self.close_behavior_combo.addItem(AppStrings.CLOSE_QUIT, False)
        self.close_behavior_combo.addItem(AppStrings.CLOSE_TRAY, True)
        close_idx = self.close_behavior_combo.findData(self.config_manager.get_close_to_tray())
        if close_idx != -1:
            self.close_behavior_combo.setCurrentIndex(close_idx)
        self.close_behavior_combo.currentIndexChanged.connect(self._on_close_behavior_changed)
        close_row.addWidget(close_label)
        close_row.addWidget(self.close_behavior_combo, 1)
        operation_layout.addLayout(close_row)
        hotkey_row = QHBoxLayout()
        hotkey_label = QLabel(AppStrings.HOTKEY_LABEL)
        self.hotkey_edit = HotkeyLineEdit()
        self.hotkey_edit.setText(self.config_manager.get_global_hotkey())
        self.hotkey_edit.hotkey_changed.connect(self._on_hotkey_changed)
        hotkey_row.addWidget(hotkey_label)
        hotkey_row.addWidget(self.hotkey_edit, 1)
        operation_layout.addLayout(hotkey_row)
        main_layout.addWidget(operation_group)
        log_group = QGroupBox(AppStrings.SETTINGS_GROUP_LOG)
        log_layout = QVBoxLayout(log_group)
        log_retention_row = QHBoxLayout()
        log_retention_label = QLabel(AppStrings.LOG_RETENTION_LABEL)
        self.log_retention_combo = QComboBox()
        self.log_retention_combo.addItem(AppStrings.COMBO_DISABLE, False)
        self.log_retention_combo.addItem(AppStrings.COMBO_ENABLE, True)
        retention_config = self.config_manager.get("log_retention", {})
        enabled = retention_config.get("enabled", True)
        self.log_retention_combo.setCurrentIndex(self.log_retention_combo.findData(enabled))
        self.log_retention_combo.currentIndexChanged.connect(self._on_log_retention_changed)
        log_retention_row.addWidget(log_retention_label)
        log_retention_row.addWidget(self.log_retention_combo, 1)
        log_layout.addLayout(log_retention_row)
        max_files_row = QHBoxLayout()
        max_files_label = QLabel(AppStrings.MAX_FILES_LABEL)
        self.max_files_spinbox = QSpinBox()
        self.max_files_spinbox.setRange(1, 100)
        self.max_files_spinbox.setValue(retention_config.get("max_files", 5))
        self.max_files_spinbox.setEnabled(enabled)
        self.max_files_spinbox.valueChanged.connect(self._on_max_files_changed)
        max_files_row.addWidget(max_files_label)
        max_files_row.addWidget(self.max_files_spinbox)
        max_files_row.addStretch()
        log_layout.addLayout(max_files_row)
        max_days_row = QHBoxLayout()
        max_days_label = QLabel(AppStrings.MAX_DAYS_LABEL)
        self.max_days_spinbox = QSpinBox()
        self.max_days_spinbox.setRange(1, 365)
        self.max_days_spinbox.setValue(retention_config.get("max_days", 3))
        self.max_days_spinbox.setEnabled(enabled)
        self.max_days_spinbox.valueChanged.connect(self._on_max_days_changed)
        max_days_row.addWidget(max_days_label)
        max_days_row.addWidget(self.max_days_spinbox)
        max_days_row.addStretch()
        log_layout.addLayout(max_days_row)
        log_layout.addSpacing(5)
        delete_logs_btn = QPushButton(AppStrings.BTN_DELETE_ALL_LOGS)
        delete_logs_btn.clicked.connect(self._clear_all_logs)
        log_layout.addWidget(delete_logs_btn)
        main_layout.addWidget(log_group)
        main_layout.addSpacing(10)
        open_dir_btn = QPushButton(AppStrings.OPEN_DATA_DIR_BTN)
        open_dir_btn.clicked.connect(self._open_data_dir)
        main_layout.addWidget(open_dir_btn)
        clear_data_btn = QPushButton(AppStrings.CLEAR_ALL_DATA_BTN)
        clear_data_btn.setStyleSheet(f"QPushButton {{ {UIStyles.STYLE_DANGER_TEXT} }}")
        clear_data_btn.clicked.connect(self._clear_all_data)
        main_layout.addWidget(clear_data_btn)
        main_layout.addStretch()
        main_layout.addStretch()
        close_btn = QPushButton(AppStrings.BTN_CLOSE)
        close_btn.clicked.connect(self.accept)
        main_layout.addWidget(close_btn)

    def _on_theme_changed(self, index):
        theme = self.theme_combo.itemData(index)
        if theme:
            self.config_manager.set_theme(theme)
            parent = self.parent()
            if parent and hasattr(parent, "_apply_theme"):
                parent._apply_theme()  # type: ignore

    def _on_hotkey_changed(self, hotkey):
        self.config_manager.set_global_hotkey(hotkey)
        parent = self.parent()
        if parent and hasattr(parent, "_setup_system_configs"):
            parent._setup_system_configs()  # type: ignore

    def _on_startup_changed(self, index):
        enabled = self.startup_combo.itemData(index)
        self.config_manager.set_run_at_startup(enabled)
        parent = self.parent()
        if parent and hasattr(parent, "_setup_system_configs"):
            parent._setup_system_configs()  # type: ignore

    def _on_close_behavior_changed(self, index):
        to_tray = self.close_behavior_combo.itemData(index)
        self.config_manager.set_close_to_tray(to_tray)

    def _on_log_retention_changed(self, index):
        enabled = self.log_retention_combo.itemData(index)
        retention = self.config_manager.get("log_retention", {})
        retention["enabled"] = enabled
        self.config_manager.config["log_retention"] = retention
        self.config_manager.save()
        self.max_files_spinbox.setEnabled(enabled)
        self.max_days_spinbox.setEnabled(enabled)

    def _on_max_files_changed(self, value):
        retention = self.config_manager.get("log_retention", {})
        retention["max_files"] = value
        self.config_manager.config["log_retention"] = retention
        self.config_manager.save()

    def _on_max_days_changed(self, value):
        retention = self.config_manager.get("log_retention", {})
        retention["max_days"] = value
        self.config_manager.config["log_retention"] = retention
        self.config_manager.save()

    def _on_lock_layout_changed(self, index):
        locked = self.lock_layout_combo.itemData(index)
        self.config_manager.set_lock_dock_layout(locked)
        parent = self.parent()
        if parent and hasattr(parent, "tab_widget"):
            tab_widget = getattr(parent, "tab_widget")
            for i in range(tab_widget.count()):
                tab = tab_widget.widget(i)
                if hasattr(tab, "_apply_lock_layout"):
                    tab._apply_lock_layout()

    def _reset_layout(self):
        parent = self.parent()
        if parent and hasattr(parent, "tab_widget"):
            tab_widget = getattr(parent, "tab_widget")
            current_tab = tab_widget.currentWidget()
            if hasattr(current_tab, "reset_layout"):
                current_tab.reset_layout()
                QMessageBox.information(self, AppStrings.INFO_TITLE, AppStrings.INFO_RESET_LAYOUT_DONE)

    def _open_data_dir(self):
        data_dir = self.config_manager.config_dir
        if os.path.exists(data_dir):
            try:
                if os.name == "nt":  # 윈도우
                    os.startfile(data_dir)
                else:  # macOS/Linux 계열
                    subprocess.run(["open" if sys.platform == "darwin" else "xdg-open", data_dir])
            except Exception as e:
                logger.error(AppStrings.LOG_RES_DATA_DIR_FAIL.format(e))
                QMessageBox.warning(
                    self,
                    AppStrings.ERROR_TITLE,
                    AppStrings.ERROR_OPEN_DIR_FAILED.format(e),
                )

    def _clear_all_data(self):
        reply = QMessageBox.question(
            self,
            AppStrings.CLEAR_ALL_DATA_BTN,
            AppStrings.CLEAR_ALL_DATA_CONFIRM,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.config_manager.clear_all_data()
            QMessageBox.information(self, AppStrings.SUCCESS_TITLE, AppStrings.INFO_CLEAR_SUCCESS)
            self.accept()

    def _clear_all_logs(self):
        reply = QMessageBox.question(
            self,
            AppStrings.BTN_DELETE_ALL_LOGS,
            AppStrings.MSG_DELETE_LOGS_CONFIRM,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.config_manager.clear_all_logs()
            QMessageBox.information(self, AppStrings.SUCCESS_TITLE, AppStrings.INFO_LOGS_DELETED)
