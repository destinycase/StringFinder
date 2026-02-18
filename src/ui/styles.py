class UIStyles:
    STYLE_SCROLLBAR = """
        QScrollBar:vertical { width: 16px; }
        QScrollBar:horizontal { height: 16px; }
        QScrollBar::handle:vertical { min-height: 30px; }
        QScrollBar::handle:horizontal { min-width: 30px; }
    """

    STYLE_DANGER_TEXT = "color: #ff5555;"
    STYLE_SELECTION_INFO = "color: #888; font-size: 14px; margin: 20px;"
    STYLE_EMPTY_LABEL = "color: {}; font-size: 16px; font-weight: bold;"

    STYLE_STOP_BTN_ACTIVE = """
        QPushButton {
            background-color: #ff4d4d;
            color: white;
            font-weight: bold;
            padding: 8px 16px;
            border-radius: 4px;
            border: 1px solid transparent;
        }
        QPushButton:hover {
            background-color: #ff1a1a;
            border: 1px solid #ff9999;
        }
        QPushButton:pressed {
            background-color: #cc0000;
        }
    """
    STYLE_STOP_BTN_WAIT = """
        QPushButton {
            background-color: #888888;
            color: #CCCCCC;
            font-weight: bold;
            padding: 8px 16px;
            border-radius: 4px;
        }
        QPushButton:hover {
            background-color: #999999;
            color: white;
        }
    """
    STYLE_SEARCH_BTN_PRIMARY = """
        QPushButton {
            background-color: #0078D7;
            color: white;
            font-weight: bold;
            padding: 8px 16px;
            border-radius: 4px;
            border: 1px solid transparent;
        }
        QPushButton:hover {
            background-color: #0086F0;
            border: 1px solid #99CCFF;
        }
        QPushButton:pressed {
            background-color: #004E8C;
        }
    """
    STYLE_SETTINGS_RECORDING = "QLineEdit { border: 2px solid #3498db; background-color: #2c3e50; }"

    STYLE_PREVIEW_HIGHLIGHT_LINE = (
        "background-color: #404040; color: #ffffff; border-left: 3px solid #ff9900; font-weight: bold;"
    )
    STYLE_PREVIEW_EMPTY = "QTextEdit { background-color: transparent; border: none; }"

    FONT_PREVIEW_WIN = "Consolas, Courier New, monospace"
    FONT_PREVIEW_MAC = "Menlo, Monaco, Courier, monospace"
