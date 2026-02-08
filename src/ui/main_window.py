from PySide6.QtWidgets import (
    QMainWindow,
    QTabWidget,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QStatusBar,
    QApplication,
)
from PySide6.QtCore import Qt, QByteArray
from ui.search_tab import SearchTab
from ui.settings_dialog import SettingsDialog
from utils.config_manager import ConfigManager
from utils.app_strings import AppStrings
import qdarktheme


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config_manager = ConfigManager()

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

        self._init_ui()
        self._apply_theme()

    def closeEvent(self, event):
        """
        애플리케이션 종료 시 윈도우 상태 및 탭별 설정 저장
        """
        # 1. 윈도우 상태 (크기, 위치, 도킹 상태 등) 저장
        self.config_manager.set_window_state(self.saveGeometry(), self.saveState())

        # 2. 모든 탭의 상태 저장 (예: 스플리터 위치)
        for i in range(self.tab_widget.count()):
            current_tab = self.tab_widget.widget(i)
            if hasattr(current_tab, "save_splitter_states"):
                current_tab.save_splitter_states()

        super().closeEvent(event)

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
        설정된 테마를 애플리케이션에 적용합니다.
        """
        theme = self.config_manager.get_theme().lower()
        app = QApplication.instance()
        if app:
            stylesheet = qdarktheme.load_stylesheet(theme)
            # 스크롤바 폭 조절 스타일 추가
            scrollbar_style = """
                QScrollBar:vertical {
                    width: 16px;
                }
                QScrollBar:horizontal {
                    height: 16px;
                }
                QScrollBar::handle:vertical {
                    min-height: 30px;
                }
                QScrollBar::handle:horizontal {
                    min-width: 30px;
                }
            """
            app.setStyleSheet(stylesheet + scrollbar_style)

    def _close_tab(self, index):
        if self.tab_widget.count() > 1:
            self.tab_widget.removeTab(index)
        else:
            # 마지막 탭은 닫지 않고 초기화하거나 그대로 둠
            pass
