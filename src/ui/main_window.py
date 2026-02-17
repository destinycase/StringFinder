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
    QInputDialog,
)
from PySide6.QtCore import Qt, QByteArray
from PySide6.QtGui import QIcon, QAction, QShortcut, QKeySequence
from ui.search_tab import SearchTab
from ui.settings_dialog import SettingsDialog

from utils.config_manager import ConfigManager
from utils.app_strings import AppStrings
from utils.constants import Constants
from ui.styles import UIStyles
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
            myappid = f"N2.StringFinder.v{Constants.APP_VERSION}"
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)

        self.setWindowTitle(AppStrings.APP_TITLE)

        # 이전 실행 시의 윈도우 크기 및 위치를 복원합니다.
        geom, state = self.config_manager.get_window_state()
        if geom and state:
            self.restoreGeometry(QByteArray.fromHex(geom.encode()))
            self.restoreState(QByteArray.fromHex(state.encode()))
        else:
            # 기본 창 크기 설정 (1200x800)
            self.resize(1200, 800)

        self.setMinimumSize(600, 400)

        # 시스템 전역 이벤트(단축키 등)를 관리하는 SystemManager를 초기화합니다.
        self.system_manager = SystemManager()
        self.system_manager.hotkey_pressed.connect(self.toggle_visibility)

        self._init_ui()
        self._init_tray()

        # 메인 윈도우 아이콘 설정 (assets 폴더의 SVG 사용)
        icon_path = get_resource_path(os.path.join("assets", "icon.svg"))
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        # [Fix] 검색 엔진 상태에 따른 윈도우 타이틀 설정
        from core.search_engine import HAS_RUST_ENGINE

        engine_status = AppStrings.ENGINE_STATUS_RUST if HAS_RUST_ENGINE else AppStrings.ENGINE_STATUS_PYTHON
        self.setWindowTitle(f"{Constants.APP_NAME} {Constants.APP_VERSION} - {engine_status}")

        self._apply_theme()
        # 전역 단축키 및 시작프로그램 설정
        self._setup_system_configs()

        # 새 탭 단축키 (Ctrl+T) 설정
        self.new_tab_shortcut = QShortcut(QKeySequence("Ctrl+T"), self)
        self.new_tab_shortcut.activated.connect(lambda: self.add_new_tab())

        # [Feat] Rust 엔진 가용 여부 확인 및 알림
        # 엔진 로드 실패 시(False) 상태 표시줄에 경고 메시지를 출력하여 사용자가 인지하도록 합니다.
        from core.search_engine import HAS_RUST_ENGINE

        if not HAS_RUST_ENGINE:
            # 윈도우가 완전히 로드된 후 메시지를 표시하기 위해 타이머 사용
            from PySide6.QtCore import QTimer

            QTimer.singleShot(
                1000,
                lambda: self.statusBar().showMessage(
                    AppStrings.MSG_RUST_LOAD_FAIL,
                    10000,  # 10초간 표시
                ),
            )

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
        # 종료 전 모든 탭의 실행 중인 워커 정리
        logger.info(AppStrings.LOG_SYS_SHUTDOWN)
        for i in range(self.tab_widget.count()):
            tab = self.tab_widget.widget(i)
            if hasattr(tab, "stop_search"):
                try:
                    # _stop_existing_search 대신 공개 메서드 stop_search 사용
                    tab.stop_search()
                except Exception as e:
                    logger.warning(AppStrings.LOG_WKR_BATCH_ERROR.format(e))
            elif hasattr(tab, "_stop_existing_search"):
                try:
                    tab._stop_existing_search()
                except Exception as e:
                    logger.warning(AppStrings.LOG_WKR_BATCH_ERROR.format(e))

        # 종료 전 윈도우의 위치와 상태를 저장합니다.
        self.config_manager.set_window_state(self.saveGeometry(), self.saveState())

        # 각 탭의 스플리터(Splitter) 위치 등 UI 세부 상태를 저장합니다.
        for i in range(self.tab_widget.count()):
            current_tab = self.tab_widget.widget(i)
            if hasattr(current_tab, "save_splitter_states"):
                current_tab.save_splitter_states()
            self._save_tab(i)

        self._save_tab_order()
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

        # 탭바 우클릭 및 더블클릭, 이동 이벤트 처리
        self.tab_widget.tabBar().setContextMenuPolicy(Qt.CustomContextMenu)
        self.tab_widget.tabBar().customContextMenuRequested.connect(self._show_tab_context_menu)
        self.tab_widget.tabBarDoubleClicked.connect(self._rename_tab)
        self.tab_widget.tabBar().tabMoved.connect(self._save_tab_order)

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

        # 실행 시 저장된 탭들을 로드하거나 첫 번째 검색 탭을 생성합니다.
        self._load_all_tabs()

    def add_new_tab(self, name=None, state=None):
        """새로운 검색 세션(탭)을 추가하고 필요한 시그널을 연결합니다."""
        new_tab = SearchTab(self.config_manager)
        # 탭 내부의 이벤트를 메인 윈도우 UI(상태바 등)와 동기화합니다.
        new_tab.status_message_requested.connect(self.statusBar().showMessage)

        # [Mod] 검색 완료 시 탭 상태와 탭 순서를 모두 저장하도록 변경
        # 기존: new_tab.search_finished_with_data.connect(lambda: self._save_tab(self.tab_widget.indexOf(new_tab)))
        new_tab.search_finished_with_data.connect(lambda: self._on_search_finished_in_tab(new_tab))

        if state:
            new_tab.load_state(state)

        if name:
            tab_title = name
        else:
            tab_count = self.tab_widget.count() + 1
            tab_title = AppStrings.SEARCH_TAB_TITLE_TEMPLATE.format(AppStrings.SEARCH_TAB_DEFAULT_TITLE, tab_count)

        self.tab_widget.addTab(new_tab, tab_title)
        self.tab_widget.setCurrentWidget(new_tab)
        # [Mod] 탭 추가 시에는 순서를 저장하지 않음 (사용자 요청)
        return new_tab

    def _on_search_finished_in_tab(self, tab):
        """탭에서 검색이 완료되었을 때 호출되어 상태와 순서를 저장합니다."""
        index = self.tab_widget.indexOf(tab)
        if index >= 0:
            self._save_tab(index)
            self._save_tab_order()

    def _rename_tab(self, index):
        """탭 이름을 변경하고 세션 파일을 업데이트합니다."""
        if index < 0:
            return

        old_name = self.tab_widget.tabText(index)
        new_name, ok = QInputDialog.getText(
            self, AppStrings.TAB_RENAME_TITLE, AppStrings.TAB_RENAME_PROMPT, text=old_name
        )

        if ok and new_name and new_name != old_name:
            # [Fix] 파일명 정규화: Windows 금지 문자 제거
            from utils.file_helper import sanitize_filename

            new_name = sanitize_filename(new_name)

            # 이전 세션 파일 삭제 (이름이 변경되었으므로)
            self.config_manager.delete_session(old_name)
            self.tab_widget.setTabText(index, new_name)
            self._save_tab(index)
            self._save_tab_order()

    def _show_tab_context_menu(self, pos):
        """탭바에서 우클릭 시 호출되는 컨텍스트 메뉴입니다."""
        index = self.tab_widget.tabBar().tabAt(pos)
        if index < 0:
            return

        menu = QMenu(self)
        rename_action = QAction(AppStrings.TAB_RENAME_TITLE, self)
        rename_action.triggered.connect(lambda: self._rename_tab(index))

        close_action = QAction(AppStrings.TAB_CLOSE_MENU, self)
        close_action.triggered.connect(lambda: self._close_tab(index))

        menu.addAction(rename_action)
        menu.addAction(close_action)
        menu.exec(self.tab_widget.tabBar().mapToGlobal(pos))

    def _save_tab(self, index):
        """특정 탭의 데이터를 파일로 저장합니다."""
        if index < 0:
            return

        tab = self.tab_widget.widget(index)
        name = self.tab_widget.tabText(index)

        # 탭의 제목(Title)과 현재 입력된 필터 상태 등을 세션 파일로 영구 저장합니다.
        if hasattr(tab, "get_state"):
            state = tab.get_state()
            state["title"] = name
            if not self.config_manager.save_session(name, state):
                self.statusBar().showMessage(AppStrings.ERROR_SESSION_SAVE.format(name), 3000)

    def _save_tab_order(self):
        """열려 있는 모든 탭의 이름을 순서대로 저장합니다."""
        tabs = []
        for i in range(self.tab_widget.count()):
            tabs.append(self.tab_widget.tabText(i))
        self.config_manager.set_tab_order(tabs)

    def _load_all_tabs(self):
        """프로그램 시작 시 저장된 모든 탭을 정해진 순서대로 불러옵니다."""
        # 1. 저장된 탭 순서(History)를 가져옵니다.
        ordered_tabs = self.config_manager.get_tab_order()

        # 2. 실제로 존재하는 모든 세션 파일 목록을 가져옵니다.
        all_sessions = self.config_manager.get_all_session_names()

        # 3. 중복 로드 방지를 위한 집합(Set) 구성
        loaded_tabs = set()

        # 순서가 지정된 탭 먼저 로드
        for name in ordered_tabs:
            # 실제 파일이 존재할 경우에만 로드
            if name in all_sessions:
                state = self.config_manager.load_session(name)
                if state:
                    self.add_new_tab(name=name, state=state)
                    loaded_tabs.add(name)

        # 4. 순서에 없던 나머지 세션들을 파일명 알파벳순으로 로드하여 뒤에 추가
        remaining_tabs = sorted(list(set(all_sessions) - loaded_tabs))
        for name in remaining_tabs:
            state = self.config_manager.load_session(name)
            if state:
                self.add_new_tab(name=name, state=state)
                loaded_tabs.add(name)

        # 5. 최종 로드된 순서대로 탭 순서를 다시 저장하여 동기화
        if remaining_tabs:
            self._save_tab_order()

        # [Fix] 복원된 탭이 하나도 없을 때만 새로운 빈 탭을 생성합니다.
        if self.tab_widget.count() == 0:
            self.add_new_tab()

    def _init_tray(self):
        """시스템 트레이 아이콘 및 우클릭 메뉴를 초기화합니다."""
        self.tray_icon = QSystemTrayIcon(self)

        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.error(AppStrings.LOG_SYS_TRAY_UNAVAILABLE)

        # assets 폴더에서 앱 아이콘을 불러와 설정합니다.
        icon_path = get_resource_path(os.path.join("assets", "icon.svg"))
        if os.path.exists(icon_path):
            self.tray_icon.setIcon(QIcon(icon_path))
        else:
            # 기본 아이콘이 없는 경우 OS 표준 정보 아이콘으로 대체합니다.
            self.tray_icon.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation))
            logger.warning(AppStrings.LOG_RES_NOT_FOUND.format(icon_path))

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
            app.setStyleSheet(stylesheet + UIStyles.STYLE_SCROLLBAR)

    # UX 개선: 상태 표시줄 프로그레스 바 업데이트 함수
    # UX 개선: 상태 표시줄 프로그레스 바 제거로 인해 빈 함수로 유지하거나 삭제
    def _close_tab(self, index):
        """탭을 닫을 때 워커를 정리하고 시그널 연결을 해제합니다."""
        if self.tab_widget.count() > 1:
            # 탭 제거 전 워커 정리
            tab = self.tab_widget.widget(index)
            name = self.tab_widget.tabText(index)

            # 세션 파일 삭제
            self.config_manager.delete_session(name)

            # 실행 중인 검색 워커 중지
            if hasattr(tab, "_stop_existing_search"):
                try:
                    tab._stop_existing_search()
                except Exception as e:
                    logger.warning(AppStrings.ERROR_STOP_SEARCH_TAB.format(index, e))

            # 로그 시그널 연결 해제 (메모리 누수 방지)
            if hasattr(tab, "logs_output"):
                from utils.logger import qt_log_handler

                try:
                    qt_log_handler.message_logged.disconnect(tab.logs_output.append)
                except Exception:
                    # 이미 연결 해제되었거나 연결되지 않은 경우 무시
                    pass

            # 탭 제거 및 메모리 정리
            if hasattr(tab, "cleanup"):
                tab.cleanup()

            self.tab_widget.removeTab(index)
            tab.deleteLater()

            # [Mod] 탭이 닫힐 때마다 변경된 순서를 저장
            self._save_tab_order()
        else:
            # 마지막 탭은 닫지 않고 초기화하거나 그대로 둠
            pass
