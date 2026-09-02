class UIStyles:
    """애플리케이션 내에서 공통으로 사용되는 CSS 스타일시트와 동적 스타일 로직을 정의합니다."""
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

    @classmethod
    def get_summary_label_style(cls, is_dark_mode: bool) -> str:
        """테마(다크/라이트)에 대응하는 검색 결과 요약 레이블 스타일을 반환합니다."""
        if is_dark_mode:
            return (
                "font-weight: bold; color: #E0E0E0; padding: 6px 10px; "
                "background-color: #2D2D2D; border: 1px solid #3E3E3E; "
                "border-radius: 4px; margin-bottom: 4px;"
            )
        return (
            "font-weight: bold; color: #333333; padding: 6px 10px; "
            "background-color: #F0F4F8; border: 1px solid #D0D7DE; "
            "border-radius: 4px; margin-bottom: 4px;"
        )

    @classmethod
    def get_skipped_files_banner_style(cls, is_dark_mode: bool) -> str:
        """건너뛴 파일 안내를 눈에 띄게 표시하는 테마별 스타일을 반환합니다."""
        if is_dark_mode:
            return """
                QFrame#skippedFilesBanner {
                    background-color: #3B3020;
                    border: 1px solid #D69E2E;
                    border-radius: 4px;
                }
                QLabel#skippedFilesCount {
                    color: #FFD166;
                    font-weight: 700;
                }
                QPushButton#skippedFilesButton {
                    background-color: #D69E2E;
                    color: #1F1F1F;
                    font-weight: 700;
                    padding: 4px 10px;
                    border: 0;
                    border-radius: 3px;
                }
                QPushButton#skippedFilesButton:hover { background-color: #EDB83D; }
                QPushButton#skippedFilesButton:pressed { background-color: #B7791F; }
            """
        return """
            QFrame#skippedFilesBanner {
                background-color: #FFF7E0;
                border: 1px solid #C47B00;
                border-radius: 4px;
            }
            QLabel#skippedFilesCount {
                color: #8A4B00;
                font-weight: 700;
            }
            QPushButton#skippedFilesButton {
                background-color: #C47B00;
                color: white;
                font-weight: 700;
                padding: 4px 10px;
                border: 0;
                border-radius: 3px;
            }
            QPushButton#skippedFilesButton:hover { background-color: #D98A00; }
            QPushButton#skippedFilesButton:pressed { background-color: #9A6100; }
        """

    @classmethod
    def get_file_info_header_style(cls, is_dark_mode: bool) -> str:
        """테마에 대응하는 선택 파일 정보 헤더 스타일을 반환합니다."""
        if is_dark_mode:
            return (
                "font-weight: bold; color: #61AFEF; padding: 4px 8px; "
                "background-color: #282C34; border: 1px solid #3E4451; border-radius: 4px;"
            )
        return (
            "font-weight: bold; color: #145DA0; padding: 4px 8px; "
            "background-color: #F1F3F4; border: 1px solid #DADCE0; border-radius: 4px;"
        )

    @classmethod
    def get_context_preview_style(cls, is_dark_mode: bool) -> str:
        """테마에 대응하는 문맥 미리보기 편집기 스타일을 반환합니다."""
        if is_dark_mode:
            return (
                "QPlainTextEdit { background-color: #1E1E1E; color: #ABB2BF; "
                "border: 1px solid #333333; border-radius: 4px; padding: 4px; }"
            )
        return (
            "QPlainTextEdit { background-color: #FFFFFF; color: #202124; "
            "border: 1px solid #DADCE0; border-radius: 4px; padding: 4px; }"
        )

    @classmethod
    def get_table_style(cls, is_dark_mode: bool) -> str:
        """
        테마(다크/라이트)에 따라 테이블 스타일을 반환합니다.
        """
        if is_dark_mode:
            return """
                QTableView {
                    selection-background-color: #0078D7;
                    selection-color: white;
                    show-decoration-selected: 1;
                }
                QTableView:focus {
                    selection-background-color: #0086F0;
                }
                QTableView::item:selected {
                    background-color: #0078D7;
                    color: white;
                }
                QTableView::item:selected:!active {
                    background-color: #0078D7;
                    color: white;
                }
                QHeaderView::section {
                    background-color: #333333;
                    color: #ffffff;
                    padding: 4px;
                    border: 1px solid #444444;
                    font-weight: bold;
                }
            """
        else:
            return """
                QTableView {
                    selection-background-color: #0078D7;
                    selection-color: white;
                    show-decoration-selected: 1;
                }
                QTableView:focus {
                    selection-background-color: #0086F0;
                }
                QTableView::item:selected {
                    background-color: #0078D7;
                    color: white;
                }
                QTableView::item:selected:!active {
                    background-color: #0078D7;
                    color: white;
                }
                QHeaderView::section {
                    background-color: #f8f9fa;
                    color: #202124;
                    padding: 4px;
                    border: 1px solid #dadce0;
                    font-weight: bold;
                }
            """
