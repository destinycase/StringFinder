from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QPushButton,
    QMessageBox,
    QSpinBox,
    QGroupBox,
)
import os
import subprocess
import sys  # sys 모듈 추가 (macOS/Linux 분기 처리를 위해 필요)
from utils.app_strings import AppStrings
from ui.styles import UIStyles
from utils.logger import logger

from ui.widgets import HotkeyLineEdit


class SettingsDialog(QDialog):
    """애플리케이션 설정을 변경하는 다이얼로그 클래스"""

    def __init__(self, config_manager, parent=None):
        """
        SettingsDialog 초기화.

        Args:
            config_manager: 설정 관리를 위한 ConfigManager 인스턴스.
            parent: 부모 위젯 (기본값: None).
        """
        super().__init__(parent)
        self.config_manager = config_manager
        self.setWindowTitle(AppStrings.SETTINGS_TITLE)  # 다이얼로그 제목 설정
        self.setMinimumWidth(300)  # 최소 너비 설정
        self._init_ui()  # UI 초기화 메서드 호출

    def _init_ui(self):
        """사용자 인터페이스를 초기화하고 레이아웃을 설정합니다."""
        main_layout = QVBoxLayout(self)

        # 1. 외형 (Appearance)
        appearance_group = QGroupBox(AppStrings.SETTINGS_GROUP_APPEARANCE)
        appearance_layout = QVBoxLayout(appearance_group)

        # 테마 설정
        theme_row = QHBoxLayout()
        theme_label = QLabel(AppStrings.THEME_LABEL)
        self.theme_combo = QComboBox()
        self.theme_combo.addItems([AppStrings.THEME_DARK, AppStrings.THEME_LIGHT])
        self.theme_combo.setCurrentText(self.config_manager.get_theme())
        self.theme_combo.currentTextChanged.connect(self._on_theme_changed)
        theme_row.addWidget(theme_label)
        theme_row.addWidget(self.theme_combo, 1)
        appearance_layout.addLayout(theme_row)

        # 레이아웃 고정
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

        # 레이아웃 초기화 버튼
        reset_layout_btn = QPushButton(AppStrings.MENU_RESET_LAYOUT)
        reset_layout_btn.clicked.connect(self._reset_layout)
        appearance_layout.addWidget(reset_layout_btn)

        main_layout.addWidget(appearance_group)

        # 2. 동작 설정 (Operation)
        operation_group = QGroupBox(AppStrings.SETTINGS_GROUP_OPERATION)
        operation_layout = QVBoxLayout(operation_group)

        # 자동 실행
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

        # 닫기 방식
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

        # 호출 단축키
        hotkey_row = QHBoxLayout()
        hotkey_label = QLabel(AppStrings.HOTKEY_LABEL)
        self.hotkey_edit = HotkeyLineEdit()
        self.hotkey_edit.setText(self.config_manager.get_global_hotkey())
        self.hotkey_edit.hotkey_changed.connect(self._on_hotkey_changed)
        hotkey_row.addWidget(hotkey_label)
        hotkey_row.addWidget(self.hotkey_edit, 1)
        operation_layout.addLayout(hotkey_row)

        main_layout.addWidget(operation_group)

        # 3. 로그 설정 (Log) - [Renamed from Data Settings]
        log_group = QGroupBox(AppStrings.SETTINGS_GROUP_LOG)
        log_layout = QVBoxLayout(log_group)

        # 로그 설정 서브레이아웃
        log_retention_row = QHBoxLayout()
        log_retention_label = QLabel(AppStrings.LOG_RETENTION_LABEL)
        self.log_retention_combo = QComboBox()
        self.log_retention_combo.addItem(AppStrings.COMBO_DISABLE, False)
        self.log_retention_combo.addItem(AppStrings.COMBO_ENABLE, True)
        retention_config = self.config_manager.get("log_retention", {})
        enabled = retention_config.get("enabled", True)  # Default True
        self.log_retention_combo.setCurrentIndex(self.log_retention_combo.findData(enabled))
        self.log_retention_combo.currentIndexChanged.connect(self._on_log_retention_changed)
        log_retention_row.addWidget(log_retention_label)
        log_retention_row.addWidget(self.log_retention_combo, 1)
        log_layout.addLayout(log_retention_row)

        # 최대 파일 수
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

        # 최대 보관 일수
        max_days_row = QHBoxLayout()
        max_days_label = QLabel(AppStrings.MAX_DAYS_LABEL)
        self.max_days_spinbox = QSpinBox()
        self.max_days_spinbox.setRange(1, 365)
        self.max_days_spinbox.setValue(retention_config.get("max_days", 3))  # Default 3
        self.max_days_spinbox.setEnabled(enabled)
        self.max_days_spinbox.valueChanged.connect(self._on_max_days_changed)
        max_days_row.addWidget(max_days_label)
        max_days_row.addWidget(self.max_days_spinbox)
        max_days_row.addStretch()
        log_layout.addLayout(max_days_row)

        log_layout.addSpacing(5)

        # [NEW] 전체 로그 삭제 버튼
        delete_logs_btn = QPushButton(AppStrings.BTN_DELETE_ALL_LOGS)
        delete_logs_btn.clicked.connect(self._clear_all_logs)
        log_layout.addWidget(delete_logs_btn)

        main_layout.addWidget(log_group)

        # 4. 성능(실험실) (Performance)
        performance_group = QGroupBox(AppStrings.SETTINGS_GROUP_PERFORMANCE)
        performance_layout = QVBoxLayout(performance_group)

        # 캐시 활성화
        cache_enable_row = QHBoxLayout()
        cache_enable_label = QLabel(AppStrings.CACHE_ENABLE_LABEL)
        self.cache_enable_combo = QComboBox()
        self.cache_enable_combo.addItem(AppStrings.COMBO_DISABLE, False)
        self.cache_enable_combo.addItem(AppStrings.COMBO_ENABLE, True)
        cache_enabled = self.config_manager.get_cache_enabled()
        self.cache_enable_combo.setCurrentIndex(self.cache_enable_combo.findData(cache_enabled))
        self.cache_enable_combo.currentIndexChanged.connect(self._on_cache_enable_changed)
        cache_enable_row.addWidget(cache_enable_label)
        cache_enable_row.addWidget(self.cache_enable_combo, 1)
        performance_layout.addLayout(cache_enable_row)

        # 캐시 설명
        cache_desc_label = QLabel(AppStrings.CACHE_ENABLE_DESC)
        cache_desc_label.setStyleSheet("color: gray; font-size: 10px;")
        cache_desc_label.setWordWrap(True)
        performance_layout.addWidget(cache_desc_label)

        performance_layout.addSpacing(5)

        # [Mod] 캐시 통계 제거 (User Request)
        # self.cache_stats_label ... Removed
        # self.cache_stats_value ... Removed
        # self._update_cache_stats() ... Removed

        # 캐시 삭제 버튼
        clear_cache_btn = QPushButton(AppStrings.CACHE_CLEAR_BTN)
        clear_cache_btn.clicked.connect(self._clear_cache)
        performance_layout.addWidget(clear_cache_btn)

        main_layout.addWidget(performance_group)

        main_layout.addSpacing(10)

        # 5. 데이터 폴더 열기 (Button)
        open_dir_btn = QPushButton(AppStrings.OPEN_DATA_DIR_BTN)
        open_dir_btn.clicked.connect(self._open_data_dir)
        main_layout.addWidget(open_dir_btn)

        # 6. 모든 데이터 초기화 (Button) - Logs, Layout, Cache
        clear_data_btn = QPushButton(AppStrings.CLEAR_ALL_DATA_BTN)
        clear_data_btn.setStyleSheet(f"QPushButton {{ {UIStyles.STYLE_DANGER_TEXT} }}")
        clear_data_btn.clicked.connect(self._clear_all_data)
        main_layout.addWidget(clear_data_btn)

        main_layout.addStretch()

        main_layout.addStretch()

        # 5. 닫기 버튼 (Accept)
        close_btn = QPushButton(AppStrings.BTN_CLOSE)
        close_btn.clicked.connect(self.accept)
        main_layout.addWidget(close_btn)

    def _on_theme_changed(self, theme):
        self.config_manager.set_theme(theme)
        if self.parent():
            self.parent()._apply_theme()

    def _on_hotkey_changed(self, hotkey):
        self.config_manager.set_global_hotkey(hotkey)
        if self.parent() and hasattr(self.parent(), "_setup_system_configs"):
            self.parent()._setup_system_configs()

    def _on_startup_changed(self, index):
        """시작 프로그램 설정 변경 시 호출"""
        enabled = self.startup_combo.itemData(index)
        self.config_manager.set_run_at_startup(enabled)
        if self.parent() and hasattr(self.parent(), "_setup_system_configs"):
            self.parent()._setup_system_configs()

    def _on_close_behavior_changed(self, index):
        """닫기 버튼 동작 설정 변경 시 호출"""
        to_tray = self.close_behavior_combo.itemData(index)
        self.config_manager.set_close_to_tray(to_tray)

    def _on_log_retention_changed(self, index):
        """로그 보존 설정 변경 시 호출"""
        enabled = self.log_retention_combo.itemData(index)
        retention = self.config_manager.get("log_retention", {})
        retention["enabled"] = enabled
        self.config_manager.config["log_retention"] = retention
        self.config_manager.save()

        # 스핀박스 활성화/비활성화
        self.max_files_spinbox.setEnabled(enabled)
        self.max_days_spinbox.setEnabled(enabled)

    def _on_max_files_changed(self, value):
        """최대 파일 수 변경 시 호출"""
        retention = self.config_manager.get("log_retention", {})
        retention["max_files"] = value
        self.config_manager.config["log_retention"] = retention
        self.config_manager.save()

    def _on_max_days_changed(self, value):
        """최대 보관 일수 변경 시 호출"""
        retention = self.config_manager.get("log_retention", {})
        retention["max_days"] = value
        self.config_manager.config["log_retention"] = retention
        self.config_manager.save()

    def _on_lock_layout_changed(self, index):
        """레이아웃 잠금 설정 변경 시 호출"""
        locked = self.lock_layout_combo.itemData(index)
        self.config_manager.set_lock_dock_layout(locked)
        # 모든 열려 있는 검색 탭에 즉시 반영
        if self.parent() and hasattr(self.parent(), "tab_widget"):
            for i in range(self.parent().tab_widget.count()):
                tab = self.parent().tab_widget.widget(i)
                if hasattr(tab, "_apply_lock_layout"):
                    tab._apply_lock_layout()

    def _reset_layout(self):
        """현재 활성화된 탭의 레이아웃을 초기화합니다."""
        if self.parent() and hasattr(self.parent(), "tab_widget"):
            current_tab = self.parent().tab_widget.currentWidget()
            if hasattr(current_tab, "reset_layout"):
                current_tab.reset_layout()
                QMessageBox.information(self, AppStrings.INFO_TITLE, AppStrings.INFO_RESET_LAYOUT_DONE)

    def _open_data_dir(self):
        """설정 파일이 저장된 데이터 폴더를 탐색기에서 엽니다."""
        data_dir = self.config_manager.config_dir
        if os.path.exists(data_dir):
            try:
                if os.name == "nt":  # Windows
                    os.startfile(data_dir)
                else:  # macOS/Linux
                    subprocess.run(["open" if sys.platform == "darwin" else "xdg-open", data_dir])
            except Exception as e:
                logger.error(AppStrings.LOG_RES_DATA_DIR_FAIL.format(e))
                QMessageBox.warning(
                    self,
                    AppStrings.ERROR_TITLE,
                    AppStrings.ERROR_OPEN_DIR_FAILED.format(e),
                )

    def _clear_all_data(self):
        """저장된 모든 설정, 히스토리, 위치 정보를 삭제하고 초기화합니다."""
        reply = QMessageBox.question(
            self, AppStrings.CLEAR_ALL_DATA_BTN, AppStrings.CLEAR_ALL_DATA_CONFIRM, QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.config_manager.clear_all_data()
            QMessageBox.information(self, AppStrings.SUCCESS_TITLE, AppStrings.INFO_CLEAR_SUCCESS)
            self.accept()

    def _on_cache_enable_changed(self, index):
        """캐시 활성화 설정 변경 시 호출"""
        enabled = self.cache_enable_combo.itemData(index)
        self.config_manager.set_cache_enabled(enabled)
        logger.info(f"[설정] 검색 캐시: {'활성화' if enabled else '비활성화'}")

    def _clear_all_logs(self):
        """저장된 로그 파일을 모두 삭제합니다."""
        reply = QMessageBox.question(
            self,
            AppStrings.BTN_DELETE_ALL_LOGS,
            AppStrings.MSG_DELETE_LOGS_CONFIRM,
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.config_manager.clear_all_logs()
            QMessageBox.information(self, AppStrings.SUCCESS_TITLE, AppStrings.INFO_LOGS_DELETED)

    # _update_cache_stats 제거됨 (사용자 요청)

    def _clear_cache(self):
        """캐시 삭제"""
        reply = QMessageBox.question(
            self,
            AppStrings.CACHE_CLEAR_BTN,
            AppStrings.MSG_CACHE_CLEAR_CONFIRM,
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            try:
                from core.search_cache import HybridSearchCache

                cache_dir = self.config_manager.get_cache_dir()
                cache = HybridSearchCache(cache_dir, persist=True)
                cache.clear()

                QMessageBox.information(self, AppStrings.SUCCESS_TITLE, AppStrings.INFO_CACHE_CLEARED)

                # 통계 업데이트 제거
                # self._update_cache_stats()
                logger.info("[설정] 검색 캐시 삭제 완료")
            except Exception as e:
                QMessageBox.warning(self, AppStrings.ERROR_TITLE, f"캐시 삭제 실패: {e}")
                logger.error(f"[설정] 캐시 삭제 실패: {e}")
