import os
import subprocess
import sys
from PySide6.QtCore import Signal, Qt

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QTabWidget,
    QWidget,
)


from sf_utils.app_strings import AppStrings
from sf_utils.logger import logger
from ui.styles import UIStyles



class SettingsDialog(QDialog):
    doctor_finished = Signal(bool)

    def __init__(self, config_manager, parent=None):
        super().__init__(parent)
        self.config_manager = config_manager
        self.setWindowTitle(AppStrings.SETTINGS_TITLE)
        self.setMinimumWidth(300)
        self._doctor_msg_box = None
        self.doctor_finished.connect(self._on_doctor_finished)
        self._init_ui()

    def _init_ui(self):
        INPUT_WIDTH = 150
        main_layout = QVBoxLayout(self)
        self.tab_widget = QTabWidget()
        main_layout.addWidget(self.tab_widget)

        # 1. 일반 설정 탭
        tab_general = QWidget()
        tab_general_layout = QVBoxLayout(tab_general)

        appearance_group = QGroupBox(AppStrings.SETTINGS_GROUP_APPEARANCE)
        appearance_layout = QVBoxLayout(appearance_group)
        theme_row = QHBoxLayout()
        theme_label = QLabel(AppStrings.THEME_LABEL)
        self.theme_combo = QComboBox()
        self.theme_combo.addItem(AppStrings.THEME_DARK, "dark")
        self.theme_combo.addItem(AppStrings.THEME_LIGHT, "light")

        current_theme = self.config_manager.get_theme()
        idx = self.theme_combo.findData(current_theme.lower())
        if idx == -1:
            idx = self.theme_combo.findText(current_theme)
        if idx != -1:
            self.theme_combo.setCurrentIndex(idx)

        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        self.theme_combo.setFixedWidth(INPUT_WIDTH)
        theme_row.addWidget(theme_label)
        theme_row.addStretch()
        theme_row.addWidget(self.theme_combo)
        appearance_layout.addLayout(theme_row)
        layout_lock_row = QHBoxLayout()
        layout_lock_label = QLabel(AppStrings.MENU_LOCK_LAYOUT + ":")
        self.lock_layout_combo = QComboBox()
        self.lock_layout_combo.addItem(AppStrings.COMBO_UNLOCKED, False)
        self.lock_layout_combo.addItem(AppStrings.COMBO_LOCKED, True)
        is_locked = self.config_manager.get_lock_dock_layout()
        self.lock_layout_combo.setCurrentIndex(self.lock_layout_combo.findData(is_locked))
        self.lock_layout_combo.currentIndexChanged.connect(self._on_lock_layout_changed)
        self.lock_layout_combo.setFixedWidth(INPUT_WIDTH)
        layout_lock_row.addWidget(layout_lock_label)
        layout_lock_row.addStretch()
        layout_lock_row.addWidget(self.lock_layout_combo)
        appearance_layout.addLayout(layout_lock_row)
        reset_layout_btn = QPushButton(AppStrings.MENU_RESET_LAYOUT)
        reset_layout_btn.clicked.connect(self._reset_layout)
        appearance_layout.addWidget(reset_layout_btn)
        tab_general_layout.addWidget(appearance_group)
        
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
        self.log_retention_combo.setFixedWidth(INPUT_WIDTH)
        log_retention_row.addWidget(log_retention_label)
        log_retention_row.addStretch()
        log_retention_row.addWidget(self.log_retention_combo)
        log_layout.addLayout(log_retention_row)
        max_files_row = QHBoxLayout()
        max_files_label = QLabel(AppStrings.MAX_FILES_LABEL)
        self.max_files_spinbox = QSpinBox()
        self.max_files_spinbox.setRange(1, 100)
        self.max_files_spinbox.setValue(retention_config.get("max_files", 5))
        self.max_files_spinbox.setEnabled(enabled)
        self.max_files_spinbox.valueChanged.connect(self._on_max_files_changed)
        self.max_files_spinbox.setFixedWidth(INPUT_WIDTH)
        max_files_row.addWidget(max_files_label)
        max_files_row.addStretch()
        max_files_row.addWidget(self.max_files_spinbox)
        log_layout.addLayout(max_files_row)
        max_days_row = QHBoxLayout()
        max_days_label = QLabel(AppStrings.MAX_DAYS_LABEL)
        self.max_days_spinbox = QSpinBox()
        self.max_days_spinbox.setRange(1, 365)
        self.max_days_spinbox.setValue(retention_config.get("max_days", 3))
        self.max_days_spinbox.setEnabled(enabled)
        self.max_days_spinbox.valueChanged.connect(self._on_max_days_changed)
        self.max_days_spinbox.setFixedWidth(INPUT_WIDTH)
        max_days_row.addWidget(max_days_label)
        max_days_row.addStretch()
        max_days_row.addWidget(self.max_days_spinbox)
        log_layout.addLayout(max_days_row)
        log_layout.addSpacing(5)
        delete_logs_btn = QPushButton(AppStrings.BTN_DELETE_ALL_LOGS)
        delete_logs_btn.clicked.connect(self._clear_all_logs)
        log_layout.addWidget(delete_logs_btn)
        tab_general_layout.addWidget(log_group)
        
        # 시스템 자가 진단 그룹 추가
        doctor_group = QGroupBox(AppStrings.BTN_SYSTEM_DOCTOR)
        doctor_layout = QVBoxLayout(doctor_group)
        doctor_btn = QPushButton(AppStrings.BTN_SYSTEM_DOCTOR)
        doctor_btn.clicked.connect(self._run_system_doctor)
        doctor_layout.addWidget(doctor_btn)
        tab_general_layout.addWidget(doctor_group)

        tab_general_layout.addSpacing(10)
        open_dir_btn = QPushButton(AppStrings.OPEN_DATA_DIR_BTN)
        open_dir_btn.clicked.connect(self._open_data_dir)
        tab_general_layout.addWidget(open_dir_btn)
        clear_data_btn = QPushButton(AppStrings.CLEAR_ALL_DATA_BTN)
        clear_data_btn.setStyleSheet(f"QPushButton {{ {UIStyles.STYLE_DANGER_TEXT} }}")
        clear_data_btn.clicked.connect(self._clear_all_data)
        tab_general_layout.addWidget(clear_data_btn)
        tab_general_layout.addStretch()
        
        self.tab_widget.addTab(tab_general, AppStrings.SETTINGS_GROUP_GENERAL)

        # 2. 고급 설정 탭
        tab_advanced = QWidget()
        tab_advanced_layout = QVBoxLayout(tab_advanced)

        advanced_group = QGroupBox(AppStrings.SETTINGS_GROUP_ADVANCED)
        advanced_layout = QVBoxLayout(advanced_group)
        
        eb_row = QHBoxLayout()
        eb_label = QLabel(AppStrings.EXCLUDE_BINARY_LABEL)
        eb_label.setToolTip(AppStrings.EXCLUDE_BINARY_TOOLTIP)
        self.exclude_binary_check = QCheckBox()
        self.exclude_binary_check.setToolTip(AppStrings.EXCLUDE_BINARY_TOOLTIP)
        self.exclude_binary_check.setChecked(self.config_manager.get_exclude_binary())
        self.exclude_binary_check.stateChanged.connect(self._on_exclude_binary_changed)
        self.exclude_binary_check.setFixedWidth(INPUT_WIDTH)
        eb_row.addWidget(eb_label)
        eb_row.addStretch()
        eb_row.addWidget(self.exclude_binary_check)
        advanced_layout.addLayout(eb_row)

        adv_settings = self.config_manager.get_advanced_settings()
        
        def create_spinbox_row(label_text, tooltip, key_name, min_val, max_val, unit_text):
            row = QHBoxLayout()
            label = QLabel(label_text)
            label.setToolTip(tooltip)
            
            spinbox = QSpinBox()
            spinbox.setRange(min_val, max_val)
            spinbox.setSuffix(f" {unit_text}")
            spinbox.setValue(adv_settings.get(key_name, 0))
            spinbox.setToolTip(tooltip)
            spinbox.setFixedWidth(150)
            
            spinbox.valueChanged.connect(lambda v, k=key_name: self._on_advanced_setting_changed(k, v))
            
            row.addWidget(label)
            row.addStretch()
            row.addWidget(spinbox)
            advanced_layout.addLayout(row)
            return spinbox
            
        self.adv_spinboxes = {}
        from sf_utils.constants import Constants
        self.adv_spinboxes[Constants.CONFIG_KEY_MAX_TOTAL_MATCHES] = create_spinbox_row(
            AppStrings.ADVANCED_MAX_TOTAL_MATCHES, AppStrings.TOOLTIP_MAX_TOTAL_MATCHES,
            Constants.CONFIG_KEY_MAX_TOTAL_MATCHES, 1000, 10_000_000, AppStrings.UNIT_COUNT
        )
        self.adv_spinboxes[Constants.CONFIG_KEY_MAX_PER_FILE_MATCHES] = create_spinbox_row(
            AppStrings.ADVANCED_MAX_PER_FILE_MATCHES, AppStrings.TOOLTIP_MAX_PER_FILE_MATCHES,
            Constants.CONFIG_KEY_MAX_PER_FILE_MATCHES, 10, 1_000_000, AppStrings.UNIT_COUNT
        )
        self.adv_spinboxes[Constants.CONFIG_KEY_MAX_JSON_DOM_SIZE] = create_spinbox_row(
            AppStrings.ADVANCED_MAX_JSON_DOM_SIZE, AppStrings.TOOLTIP_MAX_JSON_DOM_SIZE,
            Constants.CONFIG_KEY_MAX_JSON_DOM_SIZE, 1, Constants.DEFAULT_MAX_JSON_DOM_SIZE_MB, AppStrings.UNIT_MB
        )
        self.adv_spinboxes[Constants.CONFIG_KEY_MAX_SMALL_FILE_SIZE] = create_spinbox_row(
            AppStrings.ADVANCED_MAX_SMALL_FILE_SIZE, AppStrings.TOOLTIP_MAX_SMALL_FILE_SIZE,
            Constants.CONFIG_KEY_MAX_SMALL_FILE_SIZE, 1, 100, AppStrings.UNIT_MB
        )
        self.adv_spinboxes[Constants.CONFIG_KEY_JSON_MMAP_THRESHOLD] = create_spinbox_row(
            AppStrings.ADVANCED_JSON_MMAP_THRESHOLD, AppStrings.TOOLTIP_JSON_MMAP_THRESHOLD,
            Constants.CONFIG_KEY_JSON_MMAP_THRESHOLD, 1, 100, AppStrings.UNIT_MB
        )
        self.adv_spinboxes[Constants.CONFIG_KEY_TIMEOUT_WORKER_HANG] = create_spinbox_row(
            AppStrings.ADVANCED_TIMEOUT_WORKER_HANG, AppStrings.TOOLTIP_TIMEOUT_WORKER_HANG,
            Constants.CONFIG_KEY_TIMEOUT_WORKER_HANG, 10, 3600, AppStrings.UNIT_SEC
        )
        self.adv_spinboxes[Constants.CONFIG_KEY_MAX_CHECK_CELLS] = create_spinbox_row(
            AppStrings.ADVANCED_MAX_CHECK_CELLS, AppStrings.TOOLTIP_MAX_CHECK_CELLS,
            Constants.CONFIG_KEY_MAX_CHECK_CELLS, 1000, 10_000_000, AppStrings.UNIT_CELL
        )
        self.adv_spinboxes[Constants.CONFIG_KEY_MAX_JSON_DEPTH] = create_spinbox_row(
            AppStrings.ADVANCED_MAX_JSON_DEPTH, AppStrings.TOOLTIP_MAX_JSON_DEPTH,
            Constants.CONFIG_KEY_MAX_JSON_DEPTH, 10, Constants.DEFAULT_MAX_JSON_DEPTH, AppStrings.UNIT_DEPTH
        )

        advanced_layout.addSpacing(10)
        reset_adv_btn = QPushButton(AppStrings.BTN_RESET_ADVANCED)
        reset_adv_btn.clicked.connect(self._reset_advanced_settings)
        advanced_layout.addWidget(reset_adv_btn)

        tab_advanced_layout.addWidget(advanced_group)
        tab_advanced_layout.addStretch()
        self.tab_widget.addTab(tab_advanced, AppStrings.SETTINGS_GROUP_ADVANCED)

        close_btn = QPushButton(AppStrings.BTN_CLOSE)
        close_btn.clicked.connect(self.accept)
        main_layout.addWidget(close_btn)

    def _run_system_doctor(self):
        """시스템 자가 진단을 실행하고 결과를 보여줍니다."""
        from core.doctor import run_doctor_and_open
        import threading
        
        # 진단 중임을 알리는 팝업 (버튼 없이 표시)
        if self._doctor_msg_box:
            self._doctor_msg_box.close()
            self._doctor_msg_box.deleteLater()

        self._doctor_msg_box = QMessageBox(self)
        self._doctor_msg_box.setWindowTitle(AppStrings.INFO_TITLE)
        self._doctor_msg_box.setText(AppStrings.LOG_SYS_DOCTOR_RUNNING)
        self._doctor_msg_box.setStandardButtons(QMessageBox.StandardButton.NoButton)
        self._doctor_msg_box.setWindowModality(Qt.WindowModality.WindowModal)
        self._doctor_msg_box.show()

        def thread_target():
            try:
                success = run_doctor_and_open()
                self.doctor_finished.emit(success)
            except Exception as e:
                logger.error(f"Doctor thread error: {e}")
                self.doctor_finished.emit(False)
        
        threading.Thread(target=thread_target, daemon=True).start()

    def _on_doctor_finished(self, success):
        """자가 진단 완료 시 호출되는 슬롯."""
        if self._doctor_msg_box:
            logger.debug("Closing system doctor progress popup via signal.")
            self._doctor_msg_box.accept()
            self._doctor_msg_box.deleteLater()
            self._doctor_msg_box = None
        
        if success:
            QMessageBox.information(self, AppStrings.INFO_TITLE, AppStrings.LOG_SYS_DOCTOR_DONE)
        else:
            QMessageBox.warning(self, AppStrings.ERROR_TITLE, AppStrings.LOG_SYS_DOCTOR_FAIL.format("Internal Error"))

    def _on_theme_changed(self, index):
        theme = self.theme_combo.itemData(index)
        if theme:
            self.config_manager.set_theme(theme)
            parent = self.parent()
            if parent and hasattr(parent, "_apply_theme"):
                parent._apply_theme()  # 테마 변경 시 메인 윈도우의 스타일을 즉시 갱신합니다.


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
                else:  # 운영체제별 데이터 디렉터리 열기 명령 수행 (macOS/Linux)
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

    def _on_exclude_binary_changed(self, state):
        enabled = state == 2  # Qt.CheckState.Checked
        self.config_manager.set_exclude_binary(enabled)

    def _on_advanced_setting_changed(self, key, value):
        settings = self.config_manager.get_advanced_settings()
        settings[key] = value
        self.config_manager.set_advanced_settings(settings)

    def _reset_advanced_settings(self):
        defaults = self.config_manager.reset_advanced_settings()
        for key, spinbox in self.adv_spinboxes.items():
            spinbox.blockSignals(True)
            spinbox.setValue(defaults.get(key, 0))
            spinbox.blockSignals(False)
        QMessageBox.information(self, AppStrings.SUCCESS_TITLE, AppStrings.INFO_CLEAR_SUCCESS)

