import os
import subprocess
import sys
from PySide6.QtCore import Signal, Qt

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QTabWidget,
    QWidget,
)


from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants
from sf_utils.localization import LANGUAGE_LABELS
from sf_utils.logger import logger
from ui.styles import UIStyles



class SettingsDialog(QDialog):
    doctor_finished = Signal(bool)

    def __init__(self, config_manager, parent=None):
        super().__init__(parent)
        self.config_manager = config_manager
        self.setWindowTitle(AppStrings.SETTINGS_TITLE)
        self.setMinimumWidth(420)
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
        language_row = QHBoxLayout()
        language_label = QLabel(AppStrings.LANGUAGE_LABEL)
        self.language_combo = QComboBox()
        for language_code, language_name in LANGUAGE_LABELS.items():
            self.language_combo.addItem(language_name, language_code)
        language_index = self.language_combo.findData(self.config_manager.get_language())
        self.language_combo.setCurrentIndex(max(0, language_index))
        self.language_combo.setFixedWidth(INPUT_WIDTH)
        self.language_combo.currentIndexChanged.connect(self._on_language_changed)
        language_row.addWidget(language_label)
        language_row.addStretch()
        language_row.addWidget(self.language_combo)
        appearance_layout.addLayout(language_row)
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

        editor_group = QGroupBox(AppStrings.EXTERNAL_EDITOR_GROUP)
        editor_layout = QVBoxLayout(editor_group)
        editor_settings = self.config_manager.get(Constants.CONFIG_KEY_EXTERNAL_EDITOR, {})
        if not isinstance(editor_settings, dict):
            editor_settings = {}

        editor_row = QHBoxLayout()
        editor_label = QLabel(AppStrings.EXTERNAL_EDITOR_LABEL)
        self.external_editor_combo = QComboBox()
        editor_options = [
            (AppStrings.EXTERNAL_EDITOR_SYSTEM, "system"),
            (AppStrings.EXTERNAL_EDITOR_VSCODE, "vscode"),
            (AppStrings.EXTERNAL_EDITOR_CURSOR, "cursor"),
            (AppStrings.EXTERNAL_EDITOR_NOTEPADPP, "notepadpp"),
            (AppStrings.EXTERNAL_EDITOR_SUBLIME, "sublime"),
            (AppStrings.EXTERNAL_EDITOR_CUSTOM, "custom"),
        ]
        for label_text, value in editor_options:
            self.external_editor_combo.addItem(label_text, value)
        current_editor = editor_settings.get(
            Constants.CONFIG_KEY_EDITOR_TYPE, Constants.DEFAULT_EXTERNAL_EDITOR
        )
        editor_index = self.external_editor_combo.findData(current_editor)
        self.external_editor_combo.setCurrentIndex(max(0, editor_index))
        self.external_editor_combo.currentIndexChanged.connect(self._on_external_editor_changed)
        self.external_editor_combo.setFixedWidth(INPUT_WIDTH)
        editor_row.addWidget(editor_label)
        editor_row.addStretch()
        editor_row.addWidget(self.external_editor_combo)
        editor_layout.addLayout(editor_row)

        editor_path_row = QHBoxLayout()
        editor_path_label = QLabel(AppStrings.EXTERNAL_EDITOR_PATH_LABEL)
        self.external_editor_path_edit = QLineEdit(
            str(editor_settings.get(Constants.CONFIG_KEY_EDITOR_CUSTOM_PATH, ""))
        )
        self.external_editor_path_edit.editingFinished.connect(self._on_external_editor_path_changed)
        browse_editor_btn = QPushButton(AppStrings.EXTERNAL_EDITOR_BROWSE)
        browse_editor_btn.clicked.connect(self._browse_external_editor)
        editor_path_row.addWidget(editor_path_label)
        editor_path_row.addWidget(self.external_editor_path_edit, 1)
        editor_path_row.addWidget(browse_editor_btn)
        editor_layout.addLayout(editor_path_row)

        editor_args_row = QHBoxLayout()
        editor_args_label = QLabel(AppStrings.EXTERNAL_EDITOR_ARGS_LABEL)
        self.external_editor_args_edit = QLineEdit(
            str(editor_settings.get(Constants.CONFIG_KEY_EDITOR_CUSTOM_ARGS, "{file}:{line}"))
        )
        self.external_editor_args_edit.setPlaceholderText(AppStrings.EXTERNAL_EDITOR_ARGS_PLACEHOLDER)
        self.external_editor_args_edit.editingFinished.connect(self._on_external_editor_args_changed)
        editor_args_row.addWidget(editor_args_label)
        editor_args_row.addWidget(self.external_editor_args_edit, 1)
        editor_layout.addLayout(editor_args_row)
        self._update_external_editor_path_state()
        tab_general_layout.addWidget(editor_group)
        
        log_group = QGroupBox(AppStrings.SETTINGS_GROUP_LOG)
        log_layout = QVBoxLayout(log_group)
        log_retention_row = QHBoxLayout()
        log_retention_label = QLabel(AppStrings.LOG_RETENTION_LABEL)
        self.log_retention_combo = QComboBox()
        self.log_retention_combo.addItem(AppStrings.COMBO_DISABLE, False)
        self.log_retention_combo.addItem(AppStrings.COMBO_ENABLE, True)
        retention_config = self.config_manager.get_log_retention()
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

        common_group = QGroupBox(AppStrings.ADVANCED_COMMON_GROUP)
        common_layout = QVBoxLayout(common_group)

        eb_row = QHBoxLayout()
        eb_label = QLabel(AppStrings.EXCLUDE_BINARY_LABEL)
        self.exclude_binary_check = QCheckBox()
        self.exclude_binary_check.setChecked(self.config_manager.get_exclude_binary())
        self.exclude_binary_check.stateChanged.connect(self._on_exclude_binary_changed)
        self.exclude_binary_check.setFixedWidth(INPUT_WIDTH)
        eb_row.addWidget(eb_label)
        eb_row.addStretch()
        eb_row.addWidget(self.exclude_binary_check)
        common_layout.addLayout(eb_row)

        adv_settings = self.config_manager.get_advanced_settings()

        def setting_bounds(key_name):
            spec = Constants.ADVANCED_SETTING_SPECS[key_name]
            return int(spec["minimum"]), int(spec["maximum"])
        
        def create_spinbox_row(
            label_text,
            key_name,
            min_val,
            max_val,
            unit_text,
            *,
            target_layout=None,
            description_text=None,
        ):
            target_layout = target_layout or common_layout
            row = QHBoxLayout()
            label = QLabel(label_text)
            
            spinbox = QSpinBox()
            spinbox.setRange(min_val, max_val)
            spinbox.setSuffix(f" {unit_text}")
            spinbox.setValue(adv_settings.get(key_name, 0))
            spinbox.setFixedWidth(150)
            
            spinbox.valueChanged.connect(lambda v, k=key_name: self._on_advanced_setting_changed(k, v))
            
            row.addWidget(label)
            row.addStretch()
            row.addWidget(spinbox)
            target_layout.addLayout(row)
            if description_text:
                description = QLabel(description_text)
                description.setObjectName("advancedSettingDescription")
                description.setWordWrap(True)
                description.setStyleSheet("color: #888888; font-size: 11px; padding: 0 2px 4px 2px;")
                target_layout.addWidget(description)
            return spinbox
            
        self.adv_spinboxes = {}
        self.adv_spinboxes[Constants.CONFIG_KEY_MAX_TOTAL_MATCHES] = create_spinbox_row(
            AppStrings.ADVANCED_MAX_TOTAL_MATCHES,
            Constants.CONFIG_KEY_MAX_TOTAL_MATCHES, *setting_bounds(Constants.CONFIG_KEY_MAX_TOTAL_MATCHES), AppStrings.UNIT_COUNT
        )
        self.adv_spinboxes[Constants.CONFIG_KEY_MAX_PER_FILE_MATCHES] = create_spinbox_row(
            AppStrings.ADVANCED_MAX_PER_FILE_MATCHES,
            Constants.CONFIG_KEY_MAX_PER_FILE_MATCHES, *setting_bounds(Constants.CONFIG_KEY_MAX_PER_FILE_MATCHES), AppStrings.UNIT_COUNT
        )
        self.adv_spinboxes[Constants.CONFIG_KEY_MAX_JSON_DOM_SIZE] = create_spinbox_row(
            AppStrings.ADVANCED_MAX_JSON_DOM_SIZE,
            Constants.CONFIG_KEY_MAX_JSON_DOM_SIZE, *setting_bounds(Constants.CONFIG_KEY_MAX_JSON_DOM_SIZE), AppStrings.UNIT_MB,
            description_text=AppStrings.ADVANCED_MAX_JSON_DOM_SIZE_DESCRIPTION,
        )
        self.adv_spinboxes[Constants.CONFIG_KEY_MAX_JSON_DEPTH] = create_spinbox_row(
            AppStrings.ADVANCED_MAX_JSON_DEPTH,
            Constants.CONFIG_KEY_MAX_JSON_DEPTH, *setting_bounds(Constants.CONFIG_KEY_MAX_JSON_DEPTH), AppStrings.UNIT_DEPTH
        )
        tab_advanced_layout.addWidget(common_group)

        precise_group = QGroupBox(AppStrings.ADVANCED_PRECISE_SEARCH_GROUP)
        precise_layout = QVBoxLayout(precise_group)
        precise_description = QLabel(AppStrings.ADVANCED_PRECISE_SEARCH_DESCRIPTION)
        precise_description.setObjectName("preciseSearchSettingsDescription")
        precise_description.setWordWrap(True)
        precise_description.setStyleSheet("color: #888888; font-size: 11px; padding-bottom: 4px;")
        precise_layout.addWidget(precise_description)
        self.adv_spinboxes[Constants.CONFIG_KEY_MAX_SMALL_FILE_SIZE] = create_spinbox_row(
            AppStrings.ADVANCED_MAX_SMALL_FILE_SIZE,
            Constants.CONFIG_KEY_MAX_SMALL_FILE_SIZE, *setting_bounds(Constants.CONFIG_KEY_MAX_SMALL_FILE_SIZE), AppStrings.UNIT_MB,
            target_layout=precise_layout,
            description_text=AppStrings.ADVANCED_MAX_SMALL_FILE_SIZE_DESCRIPTION,
        )
        self.adv_spinboxes[Constants.CONFIG_KEY_JSON_MMAP_THRESHOLD] = create_spinbox_row(
            AppStrings.ADVANCED_JSON_MMAP_THRESHOLD,
            Constants.CONFIG_KEY_JSON_MMAP_THRESHOLD, *setting_bounds(Constants.CONFIG_KEY_JSON_MMAP_THRESHOLD), AppStrings.UNIT_MB,
            target_layout=precise_layout,
            description_text=AppStrings.ADVANCED_JSON_MMAP_THRESHOLD_DESCRIPTION,
        )
        self.adv_spinboxes[Constants.CONFIG_KEY_TIMEOUT_WORKER_HANG] = create_spinbox_row(
            AppStrings.ADVANCED_TIMEOUT_WORKER_HANG,
            Constants.CONFIG_KEY_TIMEOUT_WORKER_HANG, *setting_bounds(Constants.CONFIG_KEY_TIMEOUT_WORKER_HANG), AppStrings.UNIT_SEC,
            target_layout=precise_layout,
            description_text=AppStrings.ADVANCED_TIMEOUT_WORKER_HANG_DESCRIPTION,
        )
        tab_advanced_layout.addWidget(precise_group)

        existence_only_group = QGroupBox(AppStrings.ADVANCED_EXISTENCE_ONLY_GROUP)
        existence_only_layout = QVBoxLayout(existence_only_group)
        self.adv_spinboxes[Constants.CONFIG_KEY_MAX_CHECK_CELLS] = create_spinbox_row(
            AppStrings.ADVANCED_MAX_CHECK_CELLS,
            Constants.CONFIG_KEY_MAX_CHECK_CELLS, *setting_bounds(Constants.CONFIG_KEY_MAX_CHECK_CELLS), AppStrings.UNIT_CELL,
            target_layout=existence_only_layout,
            description_text=AppStrings.ADVANCED_MAX_CHECK_CELLS_DESCRIPTION,
        )
        tab_advanced_layout.addWidget(existence_only_group)

        tab_advanced_layout.addSpacing(10)
        reset_adv_btn = QPushButton(AppStrings.BTN_RESET_ADVANCED)
        reset_adv_btn.clicked.connect(self._reset_advanced_settings)
        tab_advanced_layout.addWidget(reset_adv_btn)

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

    def _on_language_changed(self, index):
        language = self.language_combo.itemData(index)
        if not language or language == self.config_manager.get_language():
            return
        self.config_manager.set_language(language)
        QMessageBox.information(
            self,
            AppStrings.INFO_TITLE,
            AppStrings.LANGUAGE_RESTART_REQUIRED,
        )

    def _get_external_editor_settings(self):
        settings = self.config_manager.get(Constants.CONFIG_KEY_EXTERNAL_EDITOR, {})
        return dict(settings) if isinstance(settings, dict) else {}

    def _update_external_editor_path_state(self):
        is_custom = self.external_editor_combo.currentData() == "custom"
        self.external_editor_path_edit.setEnabled(is_custom)
        self.external_editor_args_edit.setEnabled(is_custom)

    def _on_external_editor_changed(self, index):
        settings = self._get_external_editor_settings()
        settings[Constants.CONFIG_KEY_EDITOR_TYPE] = self.external_editor_combo.itemData(index)
        self.config_manager.set(Constants.CONFIG_KEY_EXTERNAL_EDITOR, settings)
        self._update_external_editor_path_state()

    def _on_external_editor_path_changed(self):
        settings = self._get_external_editor_settings()
        settings[Constants.CONFIG_KEY_EDITOR_CUSTOM_PATH] = self.external_editor_path_edit.text().strip()
        self.config_manager.set(Constants.CONFIG_KEY_EXTERNAL_EDITOR, settings)

    def _on_external_editor_args_changed(self):
        settings = self._get_external_editor_settings()
        custom_args = self.external_editor_args_edit.text().strip() or "{file}:{line}"
        settings[Constants.CONFIG_KEY_EDITOR_CUSTOM_ARGS] = custom_args
        self.config_manager.set(Constants.CONFIG_KEY_EXTERNAL_EDITOR, settings)

    def _browse_external_editor(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            AppStrings.EXTERNAL_EDITOR_BROWSE,
            "",
            AppStrings.EXTERNAL_EDITOR_FILE_FILTER,
        )
        if path:
            self.external_editor_path_edit.setText(path)
            self._on_external_editor_path_changed()


    def _on_log_retention_changed(self, index):
        enabled = self.log_retention_combo.itemData(index)
        retention = self.config_manager.get_log_retention()
        retention["enabled"] = enabled
        self.config_manager.set("log_retention", retention)
        self.max_files_spinbox.setEnabled(enabled)
        self.max_days_spinbox.setEnabled(enabled)

    def _on_max_files_changed(self, value):
        retention = self.config_manager.get_log_retention()
        retention["max_files"] = value
        self.config_manager.set("log_retention", retention)

    def _on_max_days_changed(self, value):
        retention = self.config_manager.get_log_retention()
        retention["max_days"] = value
        self.config_manager.set("log_retention", retention)

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

