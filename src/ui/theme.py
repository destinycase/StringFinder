"""앱의 다크/라이트 테마.

8.1.1까지 쓰던 pyqtdarktheme 2.1.0의 스타일시트와 아이콘을 `assets/theme/`에 고정 파일로 옮겨(tools/vendor_theme.py)
외부 라이브러리 없이 같은 화면을 그립니다. 원본의 MIT 라이선스는 `assets/theme/LICENSE-pyqtdarktheme.txt`에 있습니다.
"""

from __future__ import annotations

import os
from functools import lru_cache

from sf_utils.resource_helper import get_resource_path

THEMES = ("dark", "light")
ICON_TOKEN = "{THEME_ICON_DIR}"


def theme_dir() -> str:
    return str(get_resource_path(os.path.join("assets", "theme")))


def resolve_theme(theme: str) -> str:
    """'dark'·'light'는 그대로, 예전 설정의 'auto'는 Windows 앱 모드(밝게/어둡게)를 따릅니다. 그 밖은 'dark'."""
    theme = (theme or "").lower()
    if theme in THEMES:
        return theme
    if theme == "auto":
        try:
            from PySide6.QtCore import Qt
            from PySide6.QtGui import QGuiApplication

            if QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Light:
                return "light"
        except (ImportError, AttributeError, RuntimeError):
            pass
    return "dark"


@lru_cache(maxsize=None)
def _stylesheet(theme: str, directory: str) -> str:
    with open(os.path.join(directory, f"{theme}.qss"), encoding="utf-8") as file:
        qss = file.read()
    icon_dir = os.path.join(directory, "icons").replace("\\", "/")
    return qss.replace(ICON_TOKEN, icon_dir)


def load_stylesheet(theme: str) -> str:
    """테마('dark'·'light'·'auto')의 스타일시트. 아이콘 경로는 지금 실행 위치 기준으로 채웁니다."""
    return _stylesheet(resolve_theme(theme), theme_dir())
