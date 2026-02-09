from PySide6.QtWidgets import (
    QMainWindow,
    QTabWidget,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QStatusBar,
    QApplication,
    QSystemTrayIcon,
    QMenu,
    QStyle,
    QProgressBar,  # UX 개선: 상태 표시줄 프로그레스 바
)
from PySide6.QtCore import Qt, QByteArray
from PySide6.QtGui import QIcon, QAction
from ui.search_tab import SearchTab
from ui.settings_dialog import SettingsDialog
from utils.config_manager import ConfigManager
from utils.app_strings import AppStrings
from utils.logger import logger
from core.system_manager import SystemManager
from utils.resource_helper import get_resource_path
import qdarktheme
import os
import ctypes


class MainWindow(QMainWindow):
    """
    애플리케이션의 메인 윈도우 클래스입니다.
    탭 관리, 시스템 트레이, 테마 전환 및 전역 단축키 설정을 담당합니다.
    """

    def __init__(self):
        """메인 윈도우를 초기화하고 필요한 시스템 설정을 수행합니다."""
        super().__init__()
        self.config_manager = ConfigManager()

        # Windows 작업표시줄에서 아이콘이 별도 그룹으로 분리되지 않도록 AppUserModelID를 설정합니다.
        if os.name == "nt":
            myappid = f"N2.StringFinder.v{AppStrings.APP_VERSION}"
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)

        self.setWindowTitle(AppStrings.APP_TITLE)

        # 이전 실행 시의 윈도우 크기 및 위치를 복원합니다.
        geom, state = self.config_manager.get_window_state()
        if geom and state:
            self.restoreGeometry(QByteArray.fromHex(geom.encode()))
            self.restoreState(QByteArray.fromHex(state.encode()))
        else:
            self.resize(1200, 800)

        self.setMinimumSize(600, 400)

        # 시스템 전역 이벤트(단축키 등)를 관리하는 SystemManager를 초기화합니다.
        self.system_manager = SystemManager()
        self.system_manager.hotkey_pressed.connect(self.toggle_visibility)

        self._init_ui()
        self._init_tray()

        # 메인 윈도우 아이콘 설정 (resource_helper 사용)
        icon_path = get_resource_path(os.path.join("resources", "icon.png"))
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        self._apply_theme()
        self._setup_system_configs()

    def closeEvent(self, event):
        """
        윈도우 UI의 닫기(X) 버튼 클릭 시 호출됩니다.
        설정에 따라 시스템 트레이로 숨기거나 프로그램을 종료합니다.
        """
        if self.config_manager.get_close_to_tray() and self.tray_icon.isVisible():
            self.hide()
            event.ignore()
        else:
            self._quit_application()

    def _quit_application(self):
        """애플리케이션을 안전하게 종료하고 현재 상태를 저장합니다."""
        # 종료 전 윈도우의 위치와 상태를 저장합니다.
        self.config_manager.set_window_state(self.saveGeometry(), self.saveState())

        # 각 탭의 스플리터(Splitter) 위치 등 UI 세부 상태를 저장합니다.
        for i in range(self.tab_widget.count()):
            current_tab = self.tab_widget.widget(i)
            if hasattr(current_tab, "save_splitter_states"):
                current_tab.save_splitter_states()

        self.system_manager.unregister_hotkey()
        QApplication.quit()

    def _init_ui(self):
        """메인 레이아웃 및 기본 UI 컴포넌트들을 생성하고 배치합니다."""
        central_widget = QWidget()
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        # 탭 기반의 인터페이스 구성을 위해 QTabWidget을 사용합니다.
        self.tab_widget = QTabWidget()
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.setMovable(True)
        self.tab_widget.tabCloseRequested.connect(self._close_tab)

        # 탭 바 좌측 상단에 '새 탭 추가' 버튼을 배치합니다.
        add_button = QPushButton(AppStrings.ADD_TAB_BTN)
        add_button.setFixedWidth(30)
        add_button.clicked.connect(lambda: self.add_new_tab())
        self.tab_widget.setCornerWidget(add_button, Qt.TopLeftCorner)

        # 탭 바 우측 상단에 '설정' 버튼을 배치합니다.
        settings_container = QWidget()
        settings_layout = QHBoxLayout(settings_container)
        settings_layout.setContentsMargins(0, 0, 10, 0)
        settings_layout.setSpacing(0)

        settings_btn = QPushButton(AppStrings.SETTINGS_TITLE)
        settings_btn.setFixedWidth(80)
        settings_btn.setObjectName("settingsBtn")
        settings_btn.clicked.connect(self._show_settings)

        settings_layout.addWidget(settings_btn)
        self.tab_widget.setCornerWidget(settings_container, Qt.TopRightCorner)

        layout.addWidget(self.tab_widget)
        self.setCentralWidget(central_widget)

        self.setStatusBar(QStatusBar())

        # 검색 진행 상황을 전역적으로 보여주기 위해 상태 표시줄에 프로그레스 바를 추가합니다.
        self.status_progress_bar = QProgressBar()
        self.status_progress_bar.setMaximumWidth(200)
        self.status_progress_bar.setFormat(AppStrings.PROGRESS_BAR_FORMAT)
        self.status_progress_bar.setTextVisible(True)
        self.status_progress_bar.setVisible(False)
        self.statusBar().addPermanentWidget(self.status_progress_bar)

        # 실행 시 첫 번째 검색 탭을 자동으로 생성합니다.
        self.add_new_tab()

    def add_new_tab(self):
        """새로운 검색 세션(탭)을 추가하고 필요한 시그널을 연결합니다."""
        new_tab = SearchTab(self.config_manager)
        # 탭 내부의 이벤트를 메인 윈도우 UI(상태바 등)와 동기화합니다.
        new_tab.status_message_requested.connect(self.statusBar().showMessage)
        new_tab.progress_update_requested.connect(self._update_progress_bar)

        tab_count = self.tab_widget.count() + 1
        tab_title = AppStrings.SEARCH_TAB_TITLE_TEMPLATE.format(AppStrings.SEARCH_TAB_DEFAULT_TITLE, tab_count)
        self.tab_widget.addTab(new_tab, tab_title)
        self.tab_widget.setCurrentWidget(new_tab)

    def _init_tray(self):
        """시스템 트레이 아이콘 및 우클릭 메뉴를 초기화합니다."""
        self.tray_icon = QSystemTrayIcon(self)

        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.error(AppStrings.ERROR_TRAY_UNAVAILABLE)

        # 리소스 폴더에서 앱 아이콘을 불러와 설정합니다.
        icon_path = get_resource_path(os.path.join("resources", "icon.png"))
        if os.path.exists(icon_path):
            self.tray_icon.setIcon(QIcon(icon_path))
        else:
            # 기본 아이콘이 없는 경우 OS 표준 정보 아이콘으로 대체합니다.
            self.tray_icon.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation))
            logger.warning(AppStrings.ERROR_RESOURCE_NOT_FOUND.format(icon_path))

        self.tray_icon.setToolTip(AppStrings.APP_TITLE)

        tray_menu = QMenu(self)
        show_action = QAction(AppStrings.TRAY_OPEN, self)
        show_action.triggered.connect(self.show_normal_and_activate)

        quit_action = QAction(AppStrings.TRAY_QUIT, self)
        quit_action.triggered.connect(self._quit_application)

        tray_menu.addAction(show_action)
        tray_menu.addSeparator()
        tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.toggle_visibility()

    def show_normal_and_activate(self):
        self.showNormal()
        self.activateWindow()

    def toggle_visibility(self):
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self.show_normal_and_activate()

    def _setup_system_configs(self):
        """사용자 정의 설정에 따라 전역 단축키 및 시작프로그램 등록을 수행합니다."""
        hotkey = self.config_manager.get_global_hotkey()
        self.system_manager.register_hotkey(hotkey)

        should_run = self.config_manager.get_run_at_startup()
        self.system_manager.set_run_at_startup(should_run)

    def _show_settings(self):
        """설정 다이얼로그를 모달 형식으로 띄우고 변경 사항을 반영합니다."""
        dialog = SettingsDialog(self.config_manager, self)
        if dialog.exec():
            # 설정창 종료 후 즉시 테마를 다시 적용합니다.
            self._apply_theme()

    def _apply_theme(self):
        """설정된 테마(Dark/Light)를 바탕으로 애플리케이션의 스타일시트를 업데이트합니다."""
        theme = self.config_manager.get_theme().lower()
        app = QApplication.instance()
        if app:
            stylesheet = qdarktheme.load_stylesheet(theme)
            # qdarktheme 기본 스타일에 사용자 정의 스크롤바 스타일을 병합합니다.
            app.setStyleSheet(stylesheet + AppStrings.STYLE_SCROLLBAR)

    # UX 개선: 상태 표시줄 프로그레스 바 업데이트 함수
    def _update_progress_bar(self, current, total, visible):
        """상태 표시줄의 프로그레스 바를 업데이트합니다."""
        if visible:
            self.status_progress_bar.setMaximum(total)
            self.status_progress_bar.setValue(current)
            self.status_progress_bar.setVisible(True)
        else:
            self.status_progress_bar.setVisible(False)

    def _close_tab(self, index):
        if self.tab_widget.count() > 1:
            self.tab_widget.removeTab(index)
        else:
            # 마지막 탭은 닫지 않고 초기화하거나 그대로 둠
            pass
