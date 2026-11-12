import ctypes
import os
import time
import warnings

import qdarktheme
from PySide6.QtCore import QByteArray, QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QStatusBar,
    QStyle,
    QSizePolicy,
    QTextBrowser,
    QToolButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

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
    검색 탭, 창 상태, 테마 및 애플리케이션 종료 처리를 담당합니다.
    """
    _startup_paint_observer: QObject  # Retain the Python wrapper until window teardown.
    # 백그라운드 세션 저장 실패를 화면 스레드로 넘깁니다(다른 스레드에서 emit해도 화면 스레드에서 처리).
    _session_save_failed = Signal(str)

    def __init__(self):
        """메인 윈도우를 초기화하고 필요한 시스템 설정을 수행합니다."""
        super().__init__()
        self._session_save_failed.connect(self._on_background_session_save_failed)
        self.config_manager = ConfigManager()
        if os.name == "nt":
            # Constants.APP_VERSION은 raw 버전을 반환하므로 명시적으로 v를 붙여 AppID 생성
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
        self._search_lock_owner = None
        self._init_ui()
        from sf_utils.logger import qt_log_handler

        qt_log_handler.signaler.level_message_logged.connect(self._on_global_error_logged)
        self._last_error_time = 0.0
        self._last_error_msg = ""

        icon_path = get_resource_path(os.path.join("assets", "icon.svg"))
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        # 사용자에게는 프로그램명과 앱 버전만 표시합니다. 엔진 버전은 진단 정보로만 사용합니다.
        self.setWindowTitle(f"{Constants.APP_NAME} v{Constants.APP_VERSION}")
        self._apply_theme()
        self.new_tab_shortcut = QShortcut(QKeySequence("Ctrl+T"), self)
        self.new_tab_shortcut.activated.connect(lambda: self.add_new_tab())
        self._temporary_storage_warning_shown = False
        if self.config_manager.uses_temporary_storage is True:
            QTimer.singleShot(0, self, self._warn_temporary_config_storage)

    def _warn_temporary_config_storage(self):
        """Explain a real temporary-folder fallback once, after startup."""
        if self.config_manager.uses_temporary_storage is not True or self._temporary_storage_warning_shown:
            return
        self._temporary_storage_warning_shown = True
        QMessageBox.warning(
            self, AppStrings.ERROR_TITLE,
            AppStrings.CONFIG_TEMP_STORAGE_WARNING.format(self.config_manager.config_dir),
        )

    def closeEvent(self, event):
        """창을 닫을 때 호출되어 애플리케이션 종료 절차를 수행합니다."""
        active = any(
            isinstance(tab := self.tab_widget.widget(i), SearchTab)
            and tab.search_state
            not in (Constants.SearchState.IDLE, Constants.SearchState.STOPPING)
            for i in range(self.tab_widget.count())
        )
        if active:
            answer = QMessageBox.question(self, AppStrings.TAB_CLOSE_MENU, AppStrings.CONFIRM_EXIT_DURING_SEARCH)
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self._quit_application()
        event.accept()

    def _quit_application(self):
        """애플리케이션을 안전하게 종료하고 현재 상태를 저장합니다."""
        logger.info(AppStrings.LOG_SYS_SHUTDOWN)
        for i in range(self.tab_widget.count()):
            tab = self.tab_widget.widget(i)
            if isinstance(tab, SearchTab):
                try:
                    tab.stop()
                except Exception as e:
                    logger.warning(AppStrings.LOG_WKR_BATCH_ERROR.format(e))
            elif hasattr(tab, "stop_search"):
                try:
                    getattr(tab, "stop_search")()
                except Exception as e:
                    logger.warning(AppStrings.LOG_WKR_BATCH_ERROR.format(e))
        # 백그라운드 저장이 끝난 뒤 최종 상태를 저장해, 늦게 끝난 저장이 덮어쓰지 않게 합니다.
        wait_saves = getattr(self.config_manager, "wait_for_session_saves", None)
        if callable(wait_saves):
            wait_saves()
        # "모든 데이터 삭제" 뒤 새로 저장한 세션이 없으면, 지운 탭·창 상태를 종료하면서 되살리지 않습니다(검수 U-9).
        if not getattr(self.config_manager, "data_cleared", False):
            self.config_manager.set_window_state(self.saveGeometry(), self.saveState())
            for i in range(self.tab_widget.count()):
                current_tab = self.tab_widget.widget(i)
                if isinstance(current_tab, SearchTab):
                    current_tab.save_splitter_states()
                self._save_tab(i)
            self._save_tab_order()

        # [무결성 강화] 종료 직전 모든 설정이 디스크에 물리적으로 기록되는지 확인
        if not self.config_manager.stop():
            logger.error(AppStrings.LOG_CFG_SAVE_SHUTDOWN_FAIL)

        # [Fix] 명시적으로 리소스 정리 호출 (탭 워커, 전역 매니저 등)
        self.cleanup()
        
        QApplication.quit()

    def _init_ui(self):
        """메인 인터페이스 구성 요소(탭 위젯, 상태 표시줄 등)를 초기화합니다."""
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
        layout.addWidget(self.tab_widget)
        self.setCentralWidget(central_widget)
        sb = QStatusBar()
        # Qt reserves a small top inset; balance it below the controls as well.
        sb.setContentsMargins(0, 0, 0, 3)
        self.setStatusBar(sb)
        self.filter_status_warning = QLabel()
        self.filter_status_warning.setPixmap(self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning).pixmap(16, 16))
        self.filter_status_warning.setAccessibleName(AppStrings.FOLDER_FILTER_EMPTY)
        sb.addWidget(self.filter_status_warning)
        self.filter_status_label = QLabel()
        self.filter_status_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        sb.addWidget(self.filter_status_label, 1)
        self.help_button = QToolButton()
        self.help_button.setObjectName("statusHelpButton")
        self.help_button.setText(AppStrings.SEARCH_HELP_BUTTON)
        self.help_button.clicked.connect(self._show_search_help)
        self.log_button = QToolButton()
        self.log_button.setObjectName("statusLogButton")
        self.log_button.setText(AppStrings.TAB_LOGS)
        self.log_button.setStyleSheet(
            "QToolButton { background-color: #3a3d41; color: #f0f0f0; "
            "border: 1px solid #60656b; border-radius: 3px; padding: 2px 8px; }"
            "QToolButton:hover { background-color: #4a4f55; }"
            "QToolButton:pressed { background-color: #2f3337; }"
        )
        self.log_button.clicked.connect(self._show_active_tab_logs)
        self.help_button.setStyleSheet(self.log_button.styleSheet())
        self.settings_button = QToolButton()
        self.settings_button.setObjectName("statusSettingsButton")
        self.settings_button.setText(AppStrings.SETTINGS_TITLE)
        self.settings_button.setAccessibleName(AppStrings.SETTINGS_TITLE)
        self.settings_button.setStyleSheet(
            "QToolButton { background-color: #3a3d41; color: #f0f0f0; "
            "border: 1px solid #60656b; border-radius: 3px; padding: 2px 7px; }"
            "QToolButton:hover { background-color: #4a4f55; }"
            "QToolButton:pressed { background-color: #2f3337; }"
        )
        self.settings_button.clicked.connect(self._show_settings)
        self.status_actions = QWidget()
        actions_layout = QHBoxLayout(self.status_actions)
        actions_layout.setContentsMargins(0, 2, 0, 2)
        actions_layout.setSpacing(6)
        for button in (self.help_button, self.log_button, self.settings_button):
            actions_layout.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
        sb.addPermanentWidget(self.status_actions)

        self.tab_widget.currentChanged.connect(self._sync_status_bar_with_active_tab)
        self._load_all_tabs()

    def _show_search_help(self):
        """Keep local search guidance available without blocking a running search."""
        dialog = getattr(self, "_search_help_dialog", None)
        if dialog is None:
            dialog = QDialog(self)
            dialog.setWindowTitle(AppStrings.SEARCH_HELP_TITLE)
            dialog.resize(680, 560)
            layout = QVBoxLayout(dialog)
            text = QTextBrowser()
            text.setObjectName("searchHelpText")
            text.setHtml(AppStrings.SEARCH_HELP_TEXT)
            layout.addWidget(text)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            buttons.button(QDialogButtonBox.StandardButton.Close).setText(AppStrings.BTN_CLOSE)
            buttons.rejected.connect(dialog.close)
            layout.addWidget(buttons)
            self._search_help_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def add_new_tab(self, name=None, state=None):
        """새로운 검색 세션(탭)을 추가하고 필요한 시그널을 연결합니다."""
        new_tab = SearchTab(self.config_manager)
        new_tab.filter_status_changed.connect(lambda: self._sync_status_bar_with_active_tab(self.tab_widget.currentIndex()))
        new_tab.status_message_requested.connect(self._show_search_notice)
        new_tab.search_finished_with_data.connect(lambda: self._on_search_finished_in_tab(new_tab))
        new_tab.search_status_changed.connect(
            lambda locked, tab=new_tab: self._on_tab_search_status_changed(tab, locked)
        )
        if self._search_lock_owner is not None:
            new_tab.set_search_allowed(False)
        if state:
            try:
                new_tab.load_state(state)
            except Exception:
                # A partially restored tab must not remain connected to the UI.
                new_tab.cleanup()
                new_tab.deleteLater()
                raise
        if name:
            tab_title = name
        else:
            # 세션 파일은 탭 이름으로 구분하므로 열린 탭과 겹치지 않는 번호를 고릅니다(검수 U-2).
            existing = {self.tab_widget.tabText(index) for index in range(self.tab_widget.count())}
            tab_count = self.tab_widget.count() + 1
            tab_title = AppStrings.SEARCH_TAB_TITLE_TEMPLATE.format(AppStrings.SEARCH_TAB_DEFAULT_TITLE, tab_count)
            while tab_title in existing:
                tab_count += 1
                tab_title = AppStrings.SEARCH_TAB_TITLE_TEMPLATE.format(AppStrings.SEARCH_TAB_DEFAULT_TITLE, tab_count)
        self.tab_widget.addTab(new_tab, tab_title)
        self.tab_widget.setCurrentWidget(new_tab)
        return new_tab

    def _on_search_finished_in_tab(self, tab):
        """탭에서 검색이 완료되었을 때 호출되어 상태와 순서를 저장합니다.

        결과가 많으면 파일 쓰기가 1초 넘게 걸리므로 백그라운드에서 저장합니다(검수 S-3).
        """
        index = self.tab_widget.indexOf(tab)
        if index >= 0:
            self._save_tab(index, background=True)
            self._save_tab_order()

    def _on_background_session_save_failed(self, name):
        self._show_search_notice(AppStrings.ERROR_SESSION_SAVE.format(name), 3000)

    def _set_ui_locked(self, locked):
        """검색 중 UI 상호작용을 제한하거나 해제합니다."""
        self.tab_widget.tabBar().setEnabled(not locked)
        self.tab_widget.setTabsClosable(not locked)
        if hasattr(self, "new_tab_shortcut"):
            self.new_tab_shortcut.setEnabled(not locked)
        self.log_button.setEnabled(not locked)
        self.settings_button.setEnabled(not locked)

    def _on_tab_search_status_changed(self, tab, locked):
        """검색 중에는 다른 탭에서 검색을 시작하지 못하도록 전역 잠금을 관리합니다."""
        if locked:
            if self._search_lock_owner is None:
                self._search_lock_owner = tab
            if self._search_lock_owner is not tab:
                tab.set_search_allowed(False)
                return
            for index in range(self.tab_widget.count()):
                current_tab = self.tab_widget.widget(index)
                if isinstance(current_tab, SearchTab):
                    current_tab.set_search_allowed(current_tab is tab)
            self._set_ui_locked(True)
            return

        if self._search_lock_owner is not None and self._search_lock_owner is not tab:
            return
        self._search_lock_owner = None
        for index in range(self.tab_widget.count()):
            current_tab = self.tab_widget.widget(index)
            if isinstance(current_tab, SearchTab):
                current_tab.set_search_allowed(True)
        self._set_ui_locked(False)

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
            existing = [self.tab_widget.tabText(i) for i in range(self.tab_widget.count()) if i != index]
            if not new_name or new_name in existing:
                QMessageBox.warning(self, AppStrings.TAB_RENAME_TITLE, AppStrings.ERROR_TAB_NAME_DUPLICATE)
                return
            # Write the new session first.  Removing the old session before a
            # failed save could permanently lose the user's session.
            self.tab_widget.setTabText(index, new_name)
            if not self._save_tab(index):
                self.tab_widget.setTabText(index, old_name)
                return
            self.config_manager.delete_session(old_name)
            self._save_tab_order()

    def _show_tab_context_menu(self, pos):
        """탭 바에서 우클릭 시 호출되는 컨텍스트 메뉴입니다."""
        menu = QMenu(self)
        add_action = QAction(AppStrings.ADD_TAB_MENU, self)
        add_action.setEnabled(self._search_lock_owner is None)
        add_action.triggered.connect(self.add_new_tab)
        menu.addAction(add_action)
        index = self.tab_widget.tabBar().tabAt(pos)
        if index < 0:
            menu.exec(self.tab_widget.tabBar().mapToGlobal(pos))
            return
        menu.addSeparator()
        rename_action = QAction(AppStrings.TAB_RENAME_TITLE, self)
        rename_action.triggered.connect(lambda: self._rename_tab(index))
        close_action = QAction(AppStrings.TAB_CLOSE_MENU, self)
        close_action.triggered.connect(lambda: self._close_tab(index))
        menu.addAction(rename_action)
        menu.addAction(close_action)
        menu.exec(self.tab_widget.tabBar().mapToGlobal(pos))

    def _save_tab(self, index, background=False):
        """특정 탭의 데이터를 파일로 저장합니다. ``background``이면 저장을 맡기고 바로 돌아옵니다."""
        if index < 0:
            return
        tab = self.tab_widget.widget(index)
        name = self.tab_widget.tabText(index)
        if isinstance(tab, SearchTab):
            state = tab.get_state()
            state["title"] = name
            if background and hasattr(self.config_manager, "save_session_async"):
                # 다음 검색이 같은 목록에 결과를 덧붙이므로, 저장할 결과 목록은 지금 사본을 떠 둡니다.
                results = state.get(Constants.PAYLOAD_RESULTS)
                if isinstance(results, list):
                    state[Constants.PAYLOAD_RESULTS] = list(results)
                self.config_manager.save_session_async(name, state, self._session_save_failed.emit)
                return True
            if not self.config_manager.save_session(name, state):
                self._show_search_notice(AppStrings.ERROR_SESSION_SAVE.format(name), 3000)
                return False
            return True
        return False

    def _save_tab_order(self):
        """열려 있는 모든 탭의 이름과 순서를 저장합니다."""
        tabs = []
        for i in range(self.tab_widget.count()):
            tabs.append(self.tab_widget.tabText(i))
        self.config_manager.set_tab_order(tabs)

    def _sync_status_bar_with_active_tab(self, index):
        """Only the active tab's file-selection conditions belong in the status bar."""
        tab = self.tab_widget.widget(index)
        self.statusBar().clearMessage()
        self.filter_status_label.setText(tab.get_filter_status_text() if isinstance(tab, SearchTab) else "")
        self.filter_status_warning.setVisible(isinstance(tab, SearchTab) and not tab.folder_panel.get_selected_folders())

    def _show_search_notice(self, text, _duration):
        dialog = getattr(self, "_notice_dialog", None)
        if dialog is None:
            dialog = QMessageBox(QMessageBox.Icon.Warning, AppStrings.ERROR_TITLE, "",
                                 QMessageBox.StandardButton.Close, self)
            dialog.setWindowModality(Qt.WindowModality.NonModal)
            dialog.setTextFormat(Qt.TextFormat.PlainText)
            dialog.button(QMessageBox.StandardButton.Close).setText(AppStrings.BTN_CLOSE)
            self._notice_dialog = dialog
        dialog.setText(text)
        dialog.show()

    def _show_active_tab_logs(self):
        """Open the active tab's logs in a separate window."""
        tab = self.tab_widget.currentWidget()
        if isinstance(tab, SearchTab):
            tab.show_log_window()

    def _load_all_tabs(self):
        """프로그램 시작 시 저장된 모든 탭을 정해진 순서로 불러옵니다."""
        ordered_tabs = self.config_manager.get_tab_order()
        all_sessions = self.config_manager.get_all_session_names()
        loaded_tabs = set()
        failed_tabs = set()

        def restore_session(name):
            try:
                state = self.config_manager.load_session(name)
                if state:
                    self.add_new_tab(name=name, state=state)
                    loaded_tabs.add(name)
            except Exception as error:
                # Isolate corrupt sessions at the startup boundary; keep their files for recovery.
                failed_tabs.add(name)
                logger.error(AppStrings.LOG_SES_LOAD_FAIL.format(name, error), exc_info=True)

        for name in ordered_tabs:
            if name in all_sessions and name not in loaded_tabs and name not in failed_tabs:
                restore_session(name)
        remaining_tabs = sorted(list(set(all_sessions) - loaded_tabs - failed_tabs))
        for name in remaining_tabs:
            restore_session(name)
        if remaining_tabs and not failed_tabs:
            self._save_tab_order()
        if self.tab_widget.count() == 0:
            # Do not let a new empty tab overwrite an unreadable session on shutdown.
            number = 1
            while True:
                title = AppStrings.SEARCH_TAB_TITLE_TEMPLATE.format(AppStrings.SEARCH_TAB_DEFAULT_TITLE, number)
                if title not in all_sessions:
                    break
                number += 1
            self.add_new_tab(name=title)


    def _show_settings(self):
        """설정 다이얼로그를 모달 형식으로 띄우고 변경 사항을 반영합니다."""
        dialog = SettingsDialog(self.config_manager, self)
        dialog.display_density_changed.connect(self._apply_display_density)
        if dialog.exec():
            self._apply_theme()

    def _apply_display_density(self):
        for index in range(self.tab_widget.count()):
            tab = self.tab_widget.widget(index)
            if isinstance(tab, SearchTab):
                tab.apply_display_density()

    def _apply_theme(self):
        """설정된 테마(Dark/Light)를 애플리케이션에 적용합니다."""
        if "PYTEST_CURRENT_TEST" in os.environ:
            return
        theme_raw = self.config_manager.get_theme()
        # 테마 명칭의 대소문자 및 한국어 호환성을 보장하기 위한 매핑을 수행합니다.
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

                # 열려있는 탭들에게 테마 변경 사항 반영
                if hasattr(self, "tab_widget"):
                    for i in range(self.tab_widget.count()):
                        tab = self.tab_widget.widget(i)
                        if hasattr(tab, "result_view_panel") and hasattr(tab.result_view_panel, "_apply_theme_style"):
                            tab.result_view_panel._apply_theme_style()
            except Exception as e:
                logger.error(AppStrings.LOG_THEME_APPLY_FAIL.format(theme_raw, e))

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
                if self._search_lock_owner is tab:
                    self._search_lock_owner = None
                    self._set_ui_locked(False)
                self.tab_widget.removeTab(index)
                tab.deleteLater()
            self._save_tab_order()
        else:
            logger.debug(AppStrings.LOG_SYS_NO_TABS_TO_REMOVE)

    def cleanup(self):
        """테스트 또는 종료 시 리소스를 정리합니다."""
        from sf_utils.logger import qt_log_handler

        try:
            # 이미 해제된 연결에서 발생하는 PySide 경고까지 종료 로그를 오염시키지 않도록 방어합니다.
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="Failed to disconnect.*", category=RuntimeWarning)
                try:
                    qt_log_handler.signaler.level_message_logged.disconnect(self._on_global_error_logged)
                except (RuntimeError, TypeError):
                    pass
        except Exception as e:
            logger.debug(AppStrings.LOG_SYS_CLEANUP_SIGNALER_FAIL.format(e))
        # 탭들 정리
        for i in range(self.tab_widget.count()):
            tab = self.tab_widget.widget(i)
            if tab and hasattr(tab, "cleanup"):
                tab.cleanup()

    def _on_global_error_logged(self, level, message):
        """전역 로그에서 CRITICAL 레벨 이상 감지 시 모든 작업을 중단하고 사용자에게 팝업으로 알립니다."""
        if level not in {"CRITICAL", "FATAL"}:
            return

        # [Safety] 테스트 중에는 팝업을 띄우지 않음 (크래시 및 중단 방지)
        if "PYTEST_CURRENT_TEST" in os.environ:
            return

        # [Safety] 메인 스레드에서 실행되도록 보장
        app_instance = QApplication.instance()
        if app_instance and QThread.currentThread() != app_instance.thread():
            QTimer.singleShot(0, lambda: self._on_global_error_logged(level, message))
            return

        # 팝업이 떠 있는 동안 반복되는 처리(타이머·그리기)에서 오류가 또 나도 팝업을 겹쳐 띄우지 않습니다(검수 D-1).
        if getattr(self, "_error_popup_open", False):
            return
        # 중복 팝업 방지 (1초 이내 동일 메시지)
        current_time = time.time()
        if message == self._last_error_msg and (current_time - self._last_error_time) < 1.0:
            return

        self._last_error_time = current_time
        self._last_error_msg = message

        # 1. 모든 탭의 검색을 강제로 중단
        logger.info(AppStrings.LOG_SYS_CRITICAL_STOP_ALL)
        for i in range(self.tab_widget.count()):
            tab = self.tab_widget.widget(i)
            if isinstance(tab, SearchTab):
                try:
                    tab.stop()
                except Exception as e:
                    logger.debug(AppStrings.LOG_SYS_TAB_STOP_FAIL.format(e))

        # 2. 팝업 표시
        from PySide6.QtWidgets import QMessageBox

        self._error_popup_open = True
        try:
            QMessageBox.critical(self, AppStrings.ERROR_TITLE, f"{AppStrings.ERROR_SEARCH_CRITICAL_MSG.format(message)}")
        finally:
            self._error_popup_open = False
