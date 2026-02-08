from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QPushButton,
    QMessageBox,
    QCheckBox,
)
import os
import subprocess
import sys  # sys 모듈 추가 (macOS/Linux 분기 처리를 위해 필요)
from utils.app_strings import AppStrings
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
        layout = QVBoxLayout(self)

        # 1. 테마 설정
        theme_layout = QHBoxLayout()
        theme_label = QLabel(AppStrings.THEME_LABEL)
        self.theme_combo = QComboBox()
        self.theme_combo.addItems([AppStrings.THEME_DARK, AppStrings.THEME_LIGHT])

        current_theme = self.config_manager.get_theme()
        self.theme_combo.setCurrentText(current_theme)
        self.theme_combo.currentTextChanged.connect(self._on_theme_changed)

        theme_layout.addWidget(theme_label)
        theme_layout.addWidget(self.theme_combo, 1)
        layout.addLayout(theme_layout)

        layout.addSpacing(10)

        # 2. 전역 단축키 설정
        hotkey_layout = QHBoxLayout()
        hotkey_label = QLabel(AppStrings.HOTKEY_LABEL)
        self.hotkey_edit = HotkeyLineEdit()
        self.hotkey_edit.setText(self.config_manager.get_global_hotkey())
        self.hotkey_edit.hotkey_changed.connect(self._on_hotkey_changed)

        hotkey_layout.addWidget(hotkey_label)
        hotkey_layout.addWidget(self.hotkey_edit, 1)
        layout.addLayout(hotkey_layout)

        layout.addSpacing(10)

        # 3. 시작 프로그램 설정
        self.startup_check = QCheckBox(AppStrings.STARTUP_LABEL)
        self.startup_check.setChecked(self.config_manager.get_run_at_startup())
        self.startup_check.toggled.connect(self._on_startup_toggled)
        layout.addWidget(self.startup_check)

        layout.addSpacing(20)

        # 4. 데이터 관리
        open_dir_btn = QPushButton(AppStrings.OPEN_DATA_DIR_BTN)
        open_dir_btn.clicked.connect(self._open_data_dir)
        layout.addWidget(open_dir_btn)

        clear_data_btn = QPushButton(AppStrings.CLEAR_ALL_DATA_BTN)
        clear_data_btn.setStyleSheet("QPushButton { color: #ff5555; }")
        clear_data_btn.clicked.connect(self._clear_all_data)
        layout.addWidget(clear_data_btn)

        layout.addStretch()

        # 3. 닫기 버튼
        close_btn = QPushButton("닫기")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

    def _on_theme_changed(self, theme):
        self.config_manager.set_theme(theme)
        if self.parent():
            self.parent()._apply_theme()

    def _on_hotkey_changed(self, hotkey):
        self.config_manager.set_global_hotkey(hotkey)
        if self.parent() and hasattr(self.parent(), "_setup_system_configs"):
            self.parent()._setup_system_configs()

    def _on_startup_toggled(self, checked):
        self.config_manager.set_run_at_startup(checked)
        if self.parent() and hasattr(self.parent(), "_setup_system_configs"):
            self.parent()._setup_system_configs()

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
                logger.error(f"Failed to open data dir: {e}")
                QMessageBox.warning(self, AppStrings.ERROR_TITLE if hasattr(AppStrings, 'ERROR_TITLE') else "오류", 
                                    f"폴더를 열 수 없습니다: {e}")

    def _clear_all_data(self):
        """저장된 모든 설정, 히스토리, 위치 정보를 삭제하고 초기화합니다."""
        reply = QMessageBox.question(
            self, AppStrings.CLEAR_ALL_DATA_BTN, AppStrings.CLEAR_ALL_DATA_CONFIRM, QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.config_manager.clear_all_data()
            QMessageBox.information(self, AppStrings.SUCCESS_TITLE, AppStrings.INFO_CLEAR_SUCCESS)
            self.accept()
