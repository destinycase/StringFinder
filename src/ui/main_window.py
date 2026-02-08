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
    어플리케이션의 메인 윈도우 클래스입니다.
    탭 관리, 시스템 트레이, 테마 설정 및 전역 설정을 총괄합니다.
    """
    def __init__(self):
        super().__init__()
        self.config_manager = ConfigManager()
        
        # 윈도우 작업표시줄 아이콘이 올바르게 표시되도록 AppUserModelID 설정 (Windows 전용)
        if os.name == "nt":
            myappid = f"N2.StringFinder.v{AppStrings.APP_VERSION}"
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)

        # 윈도우 타이틀 설정
        self.setWindowTitle(AppStrings.APP_TITLE)

        # 윈도우 상태 복원 (크기 및 위치)
        geom, state = self.config_manager.get_window_state()
        if geom and state:
            self.restoreGeometry(QByteArray.fromHex(geom.encode()))
            self.restoreState(QByteArray.fromHex(state.encode()))
        else:
            # 초기 실행 시 기본 크기 설정
            self.resize(1200, 800)

        # 윈도우 최소 크기 설정 (UI 요소 깨짐 방지)
        self.setMinimumSize(600, 400)

        # 시스템 관리자 초기화
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
        X 버튼 클릭 시 설정에 따라 처리 (트레이 숨김 또는 즉시 종료)
        """
        if self.config_manager.get_close_to_tray() and self.tray_icon.isVisible():
            # 백그라운드 동작 설정이 켜져 있고 트레이 아이콘이 보인다면 숨김
            self.hide()
            event.ignore()
        else:
            # 기본 동작: 프로그램 종료
            self._quit_application()

    def _quit_application(self):
        """실제 애플리케이션 종료 처리"""
        # 1. 윈도우 상태 저장
        self.config_manager.set_window_state(self.saveGeometry(), self.saveState())

        # 2. 모든 탭 상태 저장
        for i in range(self.tab_widget.count()):
            current_tab = self.tab_widget.widget(i)
            if hasattr(current_tab, "save_splitter_states"):
                current_tab.save_splitter_states()

        self.system_manager.unregister_hotkey()
        QApplication.quit()

    def _init_ui(self):
        """
        사용자 인터페이스를 초기화합니다.
        """
        central_widget = QWidget()
        layout = QVBoxLayout(central_widget)
        # 전체적으로 5px 정도의 여백을 주어 요소가 윈도우 경계에 붙는 것을 방지
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        # 탭 위젯 초기화
        self.tab_widget = QTabWidget()
        self.tab_widget.setTabsClosable(True)  # 탭 닫기 버튼 활성화
        self.tab_widget.setMovable(True)  # 탭 이동 가능하게 설정
        self.tab_widget.tabCloseRequested.connect(self._close_tab)  # 탭 닫기 요청 시그널 연결

        # 새 탭 추가 버튼 (좌측 상단 코너)
        add_button = QPushButton(AppStrings.ADD_TAB_BTN)
        add_button.setFixedWidth(30)
        add_button.clicked.connect(lambda: self.add_new_tab())
        self.tab_widget.setCornerWidget(add_button, Qt.TopLeftCorner)

        # 설정 버튼 (우측 상단 코너)
        # 버튼이 윈도우 경계 밖으로 나가는 것을 방지하기 위해 컨테이너 위젯과 레이아웃 사용
        settings_container = QWidget()
        settings_layout = QHBoxLayout(settings_container)
        settings_layout.setContentsMargins(0, 0, 10, 0)  # 우측에 10px 여백 확보
        settings_layout.setSpacing(0)

        settings_btn = QPushButton(AppStrings.SETTINGS_TITLE)
        settings_btn.setFixedWidth(80)
        settings_btn.setObjectName("settingsBtn")
        settings_btn.clicked.connect(self._show_settings)

        settings_layout.addWidget(settings_btn)
        self.tab_widget.setCornerWidget(settings_container, Qt.TopRightCorner)

        layout.addWidget(self.tab_widget)

        self.setCentralWidget(central_widget)

        # 상태 표시줄 초기화
        self.setStatusBar(QStatusBar())

        # 애플리케이션 시작 시 기본 탭 추가
        self.add_new_tab()

    def add_new_tab(self):
        """
        새로운 검색 탭을 추가합니다.
        """
        new_tab = SearchTab(self.config_manager)
        # 탭 내부에서 상태 메시지 요청 시 메인 윈도우의 상태바에 표시하도록 시그널 연결
        new_tab.status_message_requested.connect(self.statusBar().showMessage)

        tab_count = self.tab_widget.count() + 1
        tab_title = f"{AppStrings.SEARCH_TAB_DEFAULT_TITLE} {tab_count}"
        self.tab_widget.addTab(new_tab, tab_title)
        self.tab_widget.setCurrentWidget(new_tab)  # 새로 추가된 탭을 현재 탭으로 설정

    def _init_tray(self):
        """시스템 트레이 아이콘 초기화"""
        self.tray_icon = QSystemTrayIcon(self)

        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.error("시스템 트레이를 사용할 수 없습니다.")

        # 아이콘 설정 (resource_helper 사용)
        icon_path = get_resource_path(os.path.join("resources", "icon.png"))
        if os.path.exists(icon_path):
            self.tray_icon.setIcon(QIcon(icon_path))
        else:
            # 아이콘 파일이 없을 경우 다른 표준 아이콘 사용 시도
            self.tray_icon.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation))
            logger.warning(f"앱 아이콘 파일을 찾을 수 없습니다: {icon_path}")

        self.tray_icon.setToolTip(AppStrings.APP_TITLE)

        # 트레이 메뉴 구성
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
        """설저어에 따른 시스템 등록 초기화 (단축키, 시작프로그램)"""
        # 단축키 등록
        hotkey = self.config_manager.get_global_hotkey()
        self.system_manager.register_hotkey(hotkey)

        # 시작 프로그램 등록 동기화
        should_run = self.config_manager.get_run_at_startup()
        self.system_manager.set_run_at_startup(should_run)

    def _show_settings(self):
        """
        설정 다이얼로그를 표시합니다.
        """
        dialog = SettingsDialog(self.config_manager, self)
        if dialog.exec():
            # 설정 변경 후 테마 즉시 적용
            self._apply_theme()

    def _apply_theme(self):
        """
        설정 관리자로부터 테마 정보를 읽어와 qdarktheme을 적용하고, 
        추가적인 전역 스타일(스크롤바 등)을 설정합니다.
        """
        theme = self.config_manager.get_theme().lower()
        app = QApplication.instance()
        if app:
            stylesheet = qdarktheme.load_stylesheet(theme)
            # AppStrings에 정의된 공통 스크롤바 스타일 추가 적용
            app.setStyleSheet(stylesheet + AppStrings.STYLE_SCROLLBAR)

    def _close_tab(self, index):
        if self.tab_widget.count() > 1:
            self.tab_widget.removeTab(index)
        else:
            # 마지막 탭은 닫지 않고 초기화하거나 그대로 둠
            pass
