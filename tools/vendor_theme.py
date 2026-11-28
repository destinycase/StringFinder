"""pyqtdarktheme 2.1.0이 만들던 테마를 앱 안의 고정 파일로 옮겨 적는 도구(개발용, 한 번만 실행).

8.1.2에서 외부 테마 라이브러리를 없애면서 화면을 그대로 유지하려고, 라이브러리가 만들던 스타일시트(QSS)와
아이콘(SVG)을 `src/assets/theme/`에 저장합니다. 앱은 실행할 때 이 파일만 읽습니다.
다시 만들 일이 있을 때만 `pip install pyqtdarktheme==2.1.0 --ignore-requires-python --no-deps` 후 실행합니다.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src" / "assets" / "theme"
ICON_TOKEN = "{THEME_ICON_DIR}"


def main() -> int:
    import os
    from importlib import metadata

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import qdarktheme  # 개발 PC에서만 필요합니다.

    # 앱이 쓰던 결과와 같게: QApplication이 있어야 제목줄 아이콘 규칙(standard icons)까지 붙습니다.
    _app = QApplication.instance() or QApplication([])

    version = metadata.version("pyqtdarktheme")
    if version != "2.1.0":
        print(f"pyqtdarktheme 2.1.0이 필요합니다(현재 {version}).")
        return 1
    icons = OUT / "icons"
    if OUT.exists():
        shutil.rmtree(OUT)
    icons.mkdir(parents=True)
    used: set[str] = set()
    for theme in ("dark", "light"):
        qss = qdarktheme.load_stylesheet(theme)

        def repl(match: re.Match[str]) -> str:
            source = Path(match.group(1).strip("'\""))
            used.add(source.name)
            shutil.copyfile(source, icons / source.name)
            return f'url("{ICON_TOKEN}/{source.name}")'

        qss = re.sub(r"url\(([^)]*)\)", repl, qss)
        (OUT / f"{theme}.qss").write_text(qss, encoding="utf-8", newline="\n")
    license_file = next(Path(p.locate()) for p in metadata.files("pyqtdarktheme") or [] if p.name == "LICENSE.txt")
    # 저장소와 같은 LF 줄바꿈으로 씁니다(원본을 그대로 복사하면 PC에 따라 CRLF가 됨).
    license_text = license_file.read_text(encoding="utf-8").replace("\r\n", "\n")
    (OUT / "LICENSE-pyqtdarktheme.txt").write_text(license_text, encoding="utf-8", newline="\n")
    print(f"QSS 2개, 아이콘 {len(used)}개를 {OUT.relative_to(ROOT)}에 저장했습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
