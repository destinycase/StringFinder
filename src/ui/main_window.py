import ctypes
import os

import qdarktheme
from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QAction, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QInputDialog,
    QMainWindow,
    QMenu,
    QPushButton,
    QStatusBar,
    QStyle,
    QSystemTrayIcon,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.system_manager import SystemManager
from sf_utils.app_strings import AppStrings
from sf_utils.config_manager import ConfigManager
from sf_utils.constants import Constants
from sf_utils.logger import logger
from sf_utils.resource_helper import get_resource_path
from ui.search_tab import SearchTab
from ui.settings_dialog import SettingsDialog
from ui.styles import UIStyles


class MainWindow(QMainWindow):
    """
    애플리케이션의 메인 윈도우 클래스입니다.
    창 관리, 시스템 트레이, 테마 전환 및 전역 단축키 설정을 담당합니다.
    """

    def __init__(self):
        """메인 윈도우를 초기화하고 필요한 시스템 설정을 수행합니다."""
        super().__init__()
        self.config_manager = ConfigManager()
        if os.name == "nt":
            myappid = f"N2.StringFinder.v{Constants.APP_VERSION}"
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
        self.setWindowTitle(AppStrings.APP_TITLE)
        geom, state = self.config_manager.get_window_state()
        if geom and state:
            self.restoreGeometry(QByteArray.fromHex(geom.encode()))
            self.restoreState(QByteArray.fromHex(state.encode()))
        else:
            self.resize(1200, 800)
        self.setMinimumSize(600, 400)
        self.system_manager = SystemManager()
        self.system_manager.hotkey_pressed.connect(self.toggle_visibility)
        self._init_ui()
        if "PYTEST_CURRENT_TEST" not in os.environ:
            self._init_tray()
        else:
            self.tray_icon = None
            logger.debug(AppStrings.LOG_SYS_TRAY_DISABLED_TEST)
        icon_path = get_resource_path(os.path.join("assets", "icon.svg"))
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        from core.search_engine import HAS_RUST_ENGINE

        engine_status = AppStrings.ENGINE_STATUS_RUST if HAS_RUST_ENGINE else AppStrings.ENGINE_STATUS_PYTHON
        self.setWindowTitle(f"{Constants.APP_NAME} {Constants.APP_VERSION} - {engine_status}")
        self._apply_theme()
        self._setup_system_configs()
        self.new_tab_shortcut = QShortcut(QKeySequence("Ctrl+T"), self)
        self.new_tab_shortcut.activated.connect(lambda: self.add_new_tab())
        from core.search_engine import HAS_RUST_ENGINE

        if not HAS_RUST_ENGINE:
            from PySide6.QtCore import QTimer

            def show_rust_fail_msg():
                try:
                    # 윈도우 객체가 아직 유효한 경우에만 메시지 표시
                    if not self.parent() and self.isVisible():
                        self.statusBar().showMessage(AppStrings.MSG_RUST_LOAD_FAIL, 10000)
                        # [개선] 경고 창을 통해 상세 해결 방법 안내
                        from PySide6.QtWidgets import QMessageBox

                        msg = QMessageBox(self)
                        msg.setIcon(QMessageBox.Icon.Warning)
                        msg.setWindowTitle(AppStrings.MSG_RUST_LOAD_FAIL)
                        msg.setText(AppStrings.MSG_RUST_LOAD_FAIL_GUIDE)
                        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
                        msg.show()
                except (RuntimeError, AttributeError):
                    pass

            QTimer.singleShot(1000, show_rust_fail_msg)

    def closeEvent(self, event):
        """closeEvent 함수."""
        if self.config_manager.get_close_to_tray() and self.tray_icon and self.tray_icon.isVisible():
            self.hide()
            event.ignore()
        else:
            self._quit_application()

    def _quit_application(self):
        """애플리케이션을 안전하게 종료하고 현재 상태를 저장합니다."""
        logger.info(AppStrings.LOG_SYS_SHUTDOWN)
        for i in range(self.tab_widget.count()):
            tab = self.tab_widget.widget(i)
            if isinstance(tab, SearchTab):
                try:
                    tab.stop_search()
                except Exception as e:
                    logger.warning(AppStrings.LOG_WKR_BATCH_ERROR.format(e))
            elif hasattr(tab, "stop_search"):
                try:
                    getattr(tab, "stop_search")()
                except Exception as e:
                    logger.warning(AppStrings.LOG_WKR_BATCH_ERROR.format(e))
        self.config_manager.set_window_state(self.saveGeometry(), self.saveState())
        for i in range(self.tab_widget.count()):
            current_tab = self.tab_widget.widget(i)
            if isinstance(current_tab, SearchTab):
                current_tab.save_splitter_states()
            self._save_tab(i)
        self._save_tab_order()
        self.system_manager.unregister_hotkey()

        # [무결성 강화] 종료 직전 모든 설정이 디스크에 물리적으로 기록되는지 확인
        if not self.config_manager.stop():
            logger.error("Final configuration save failed during shutdown.")

        QApplication.quit()

    def _init_ui(self):
        """_init_ui 함수."""
        central_widget = QWidget()
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)
        self.tab_widget = QTabWidget()
        self.tab_widget.setTabsClosable(True)
        self.tab_widget.setMovable(True)
        self.tab_widget.tabCloseRequested.connect(self._close_tab)
        self.tab_widget.tabBar().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tab_widget.tabBar().customContextMenuRequested.connect(self._show_tab_context_menu)
        self.tab_widget.tabBarDoubleClicked.connect(self._rename_tab)
        self.tab_widget.tabBar().tabMoved.connect(self._save_tab_order)
        self.add_button = QPushButton(AppStrings.ADD_TAB_BTN)
        self.add_button.setFixedWidth(30)
        self.add_button.clicked.connect(lambda: self.add_new_tab())
        self.tab_widget.setCornerWidget(self.add_button, Qt.Corner.TopLeftCorner)
        settings_container = QWidget()
        settings_layout = QHBoxLayout(settings_container)
        settings_layout.setContentsMargins(0, 0, 10, 0)
        settings_layout.setSpacing(0)
        settings_btn = QPushButton(AppStrings.SETTINGS_TITLE)
        settings_btn.setFixedWidth(80)
        settings_btn.setObjectName("settingsBtn")
        settings_btn.clicked.connect(self._show_settings)
        settings_layout.addWidget(settings_btn)
        self.tab_widget.setCornerWidget(settings_container, Qt.Corner.TopRightCorner)
        layout.addWidget(self.tab_widget)
        self.setCentralWidget(central_widget)
        self.setStatusBar(QStatusBar())
        self._load_all_tabs()

    def add_new_tab(self, name=None, state=None):
        """새로운 검색 세션(탭)을 추가하고 필요한 시그널을 연결합니다."""
        new_tab = SearchTab(self.config_manager)
        new_tab.status_message_requested.connect(self.statusBar().showMessage)
        new_tab.search_finished_with_data.connect(lambda: self._on_search_finished_in_tab(new_tab))
        new_tab.search_status_changed.connect(self._set_ui_locked)
        if state:
            new_tab.load_state(state)
        if name:
            tab_title = name
        else:
            tab_count = self.tab_widget.count() + 1
            tab_title = AppStrings.SEARCH_TAB_TITLE_TEMPLATE.format(AppStrings.SEARCH_TAB_DEFAULT_TITLE, tab_count)
        self.tab_widget.addTab(new_tab, tab_title)
        self.tab_widget.setCurrentWidget(new_tab)
        return new_tab

    def _on_search_finished_in_tab(self, tab):
        """탭에서 검색이 완료되었을 때 호출되어 상태와 순서를 저장합니다."""
        index = self.tab_widget.indexOf(tab)
        if index >= 0:
            self._save_tab(index)
            self._save_tab_order()

    def _set_ui_locked(self, locked):
        """_set_ui_locked 함수."""
        self.tab_widget.tabBar().setEnabled(not locked)
        self.tab_widget.setTabsClosable(not locked)
        if hasattr(self, "add_button"):
            self.add_button.setEnabled(not locked)
        if hasattr(self, "new_tab_shortcut"):
            self.new_tab_shortcut.setEnabled(not locked)
        settings_btn = self.tab_widget.findChild(QPushButton, "settingsBtn")
        if settings_btn:
            settings_btn.setEnabled(not locked)

    def _rename_tab(self, index):
        """탭 이름을 변경하고 세션 파일을 업데이트합니다."""
        if index < 0:
            return
        old_name = self.tab_widget.tabText(index)
        new_name, ok = QInputDialog.getText(
            self, AppStrings.TAB_RENAME_TITLE, AppStrings.TAB_RENAME_PROMPT, text=old_name
        )
        if ok and new_name and new_name != old_name:
            from sf_utils.file_helper import sanitize_filename

            new_name = sanitize_filename(new_name)
            self.config_manager.delete_session(old_name)
            self.tab_widget.setTabText(index, new_name)
            self._save_tab(index)
            self._save_tab_order()

    def _show_tab_context_menu(self, pos):
        """탭 바에서 우클릭 시 호출되는 컨텍스트 메뉴입니다."""
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
        if isinstance(tab, SearchTab):
            state = tab.get_state()
            state["title"] = name
            if not self.config_manager.save_session(name, state):
                self.statusBar().showMessage(AppStrings.ERROR_SESSION_SAVE.format(name), 3000)

    def _save_tab_order(self):
        """열려 있는 모든 탭의 이름과 순서를 저장합니다."""
        tabs = []
        for i in range(self.tab_widget.count()):
            tabs.append(self.tab_widget.tabText(i))
        self.config_manager.set_tab_order(tabs)

    def _load_all_tabs(self):
        """프로그램 시작 시 저장된 모든 탭을 정해진 순서로 불러옵니다."""
        ordered_tabs = self.config_manager.get_tab_order()
        all_sessions = self.config_manager.get_all_session_names()
        loaded_tabs = set()
        for name in ordered_tabs:
            if name in all_sessions:
                state = self.config_manager.load_session(name)
                if state:
                    self.add_new_tab(name=name, state=state)
                    loaded_tabs.add(name)
        remaining_tabs = sorted(list(set(all_sessions) - loaded_tabs))
        for name in remaining_tabs:
            state = self.config_manager.load_session(name)
            if state:
                self.add_new_tab(name=name, state=state)
                loaded_tabs.add(name)
        if remaining_tabs:
            self._save_tab_order()
        if self.tab_widget.count() == 0:
            self.add_new_tab()

    def _init_tray(self):
        """시스템 트레이 아이콘 및 우클릭 메뉴를 초기화합니다."""
        self.tray_icon = QSystemTrayIcon(self)
        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.error(AppStrings.LOG_SYS_TRAY_UNAVAILABLE)
        icon_path = get_resource_path(os.path.join("assets", "icon.svg"))
        if os.path.exists(icon_path):
            self.tray_icon.setIcon(QIcon(icon_path))
        else:
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
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
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
            self._apply_theme()

    def _apply_theme(self):
        """_apply_theme 함수."""
        if "PYTEST_CURRENT_TEST" in os.environ:
            return
        theme_raw = self.config_manager.get_theme()
        # [고도화] 한국어 테마 명칭 및 대소문자 매핑 (qdarktheme 호환성 확보)
        theme_map = {
            AppStrings.THEME_DARK.lower(): "dark",
            AppStrings.THEME_LIGHT.lower(): "light",
            "dark": "dark",
            "light": "light",
            "auto": "auto",
        }
        theme = theme_map.get(theme_raw.lower(), "dark")  # 기본값 dark

        app = QApplication.instance()
        if isinstance(app, QApplication):
            try:
                stylesheet = qdarktheme.load_stylesheet(theme)
                app.setStyleSheet(stylesheet + UIStyles.STYLE_SCROLLBAR)
            except Exception as e:
                logger.error(f"Theme application failed (theme={theme_raw}): {e}")

    def _close_tab(self, index):
        """탭을 닫을 때 워커를 정리하고 시그널 연결을 해제합니다."""
        if self.tab_widget.count() > 1:
            tab = self.tab_widget.widget(index)
            name = self.tab_widget.tabText(index)
            self.config_manager.delete_session(name)
            if isinstance(tab, SearchTab):
                try:
                    # pylint: disable=protected-access
                    tab._stop_existing_search()
                except Exception as e:
                    logger.warning(AppStrings.ERROR_STOP_SEARCH_TAB.format(f"tab={index}, error={e}"))
                tab.cleanup()
                self.tab_widget.removeTab(index)
                tab.deleteLater()
            self._save_tab_order()
        else:
            pass

    def cleanup(self):
        """테스트 또는 종료 시 리소스를 정리합니다."""
        tray = getattr(self, "tray_icon", None)
        if tray:
            tray.hide()
            tray.deleteLater()
        if hasattr(self, "system_manager"):
            try:
                self.system_manager.unregister_hotkey()
            except Exception:
                pass
        # 탭들 정리
        for i in range(self.tab_widget.count()):
            tab = self.tab_widget.widget(i)
            if tab and hasattr(tab, "cleanup"):
                tab.cleanup()
