"""앱 내장 테마(8.1.2): 외부 테마 라이브러리 없이 다크/라이트 스타일시트를 그립니다."""

import ast
import os
import re
from pathlib import Path

import pytest

from ui import theme

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", ["dark", "light"])
def test_stylesheet_loads_and_every_icon_exists(name):
    qss = theme.load_stylesheet(name)
    assert len(qss) > 20000 and theme.ICON_TOKEN not in qss
    urls = re.findall(r'url\("([^"]+)"\)', qss)
    assert urls and not re.search(r"url\([^\"]", qss)
    for url in urls:
        assert os.path.isfile(url), url


def test_dark_and_light_differ_and_unknown_falls_back_to_dark():
    assert theme.load_stylesheet("dark") != theme.load_stylesheet("light")
    assert theme.resolve_theme("DARK") == "dark"
    assert theme.resolve_theme("Light") == "light"
    assert theme.resolve_theme("blue") == "dark"
    assert theme.resolve_theme("") == "dark"
    assert theme.resolve_theme("auto") in ("dark", "light")


def test_theme_license_ships_with_theme_files():
    license_text = (PROJECT_ROOT / "src" / "assets" / "theme" / "LICENSE-pyqtdarktheme.txt").read_text(encoding="utf-8")
    assert "MIT License" in license_text


def test_product_code_no_longer_imports_external_theme_package():
    for path in (PROJECT_ROOT / "src").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            assert not any(n.split(".")[0] == "qdarktheme" for n in names), path
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "pyqtdarktheme" not in pyproject
