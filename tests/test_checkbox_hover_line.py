"""체크박스에 마우스를 올려도 밑줄(아래 테두리)이 생기지 않습니다(7.1.19)."""
import qdarktheme

from ui.styles import UIStyles


def test_theme_hover_line_is_overridden_after_theme_stylesheet():
    theme = qdarktheme.load_stylesheet("dark")
    assert "QCheckBox:hover" in theme  # 테마가 밑줄을 그리는 규칙이 있어 덮어써야 합니다.
    assert "QCheckBox:hover" in UIStyles.STYLE_CHECKBOX_NO_HOVER_LINE
    assert "transparent" in UIStyles.STYLE_CHECKBOX_NO_HOVER_LINE


def test_apply_theme_appends_override(monkeypatch, qtbot, mock_config_manager):
    from PySide6.QtWidgets import QApplication
    from ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    app = QApplication.instance()
    original = app.styleSheet()
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    try:
        window._apply_theme()
        assert app.styleSheet().endswith(UIStyles.STYLE_CHECKBOX_NO_HOVER_LINE)
    finally:
        app.setStyleSheet(original)
