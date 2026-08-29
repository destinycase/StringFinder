"""문맥 미리보기용 경량 구문 강조기.

검색 코어와 무관하게 UI 문서에만 적용되며, 한 줄 단위의 정규식 규칙만 사용합니다.
따라서 검색 속도와 검색 결과에는 영향을 주지 않습니다.
"""

import os

from PySide6.QtCore import QRegularExpression
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat


class LightweightSyntaxHighlighter(QSyntaxHighlighter):
    """JSON/XML/Python/JavaScript/로그에 대한 비용이 낮은 문법 강조기."""

    _EXTENSION_LANGUAGE = {
        ".json": "json",
        ".xml": "xml",
        ".py": "python",
        ".js": "javascript",
        ".mjs": "javascript",
        ".cjs": "javascript",
        ".log": "log",
    }
    _LOG_EXTENSIONS = {".log", ".out", ".err"}

    def __init__(self, document, language="text", is_dark_mode=True):
        super().__init__(document)
        self.language = "text"
        self.is_dark_mode = bool(is_dark_mode)
        self._rules = []
        self.set_language(language)

    @classmethod
    def language_for_path(cls, file_path: str) -> str:
        extension = os.path.splitext(str(file_path or ""))[1].lower()
        if extension in cls._LOG_EXTENSIONS:
            return "log"
        return cls._EXTENSION_LANGUAGE.get(extension, "text")

    def set_file_path(self, file_path: str):
        self.set_language(self.language_for_path(file_path))

    def set_dark_mode(self, is_dark_mode: bool):
        is_dark_mode = bool(is_dark_mode)
        if self.is_dark_mode != is_dark_mode:
            self.is_dark_mode = is_dark_mode
            self._rebuild_rules()
            self.rehighlight()

    def set_language(self, language: str):
        normalized = str(language or "text").lower()
        if normalized not in {"json", "xml", "python", "javascript", "log"}:
            normalized = "text"
        if self.language == normalized and self._rules:
            return
        self.language = normalized
        self._rebuild_rules()
        self.rehighlight()

    def _format(self, color: str, bold: bool = False) -> QTextCharFormat:
        char_format = QTextCharFormat()
        char_format.setForeground(QColor(color))
        if bold:
            char_format.setFontWeight(QFont.Weight.Bold)
        return char_format

    def _rebuild_rules(self):
        if self.is_dark_mode:
            colors = {
                "comment": "#6A9955",
                "string": "#CE9178",
                "number": "#B5CEA8",
                "keyword": "#569CD6",
                "key": "#9CDCFE",
                "tag": "#4EC9B0",
                "severity": "#F44747",
            }
        else:
            colors = {
                "comment": "#008000",
                "string": "#A31515",
                "number": "#098658",
                "keyword": "#0000FF",
                "key": "#0451A5",
                "tag": "#800000",
                "severity": "#C00000",
            }

        string_format = self._format(colors["string"])
        number_format = self._format(colors["number"])
        comment_format = self._format(colors["comment"])
        keyword_format = self._format(colors["keyword"], bold=True)
        key_format = self._format(colors["key"])
        tag_format = self._format(colors["tag"], bold=True)
        severity_format = self._format(colors["severity"], bold=True)

        self._rules = [
            (QRegularExpression(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\''), string_format),
            (QRegularExpression(r"\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b"), number_format),
        ]

        if self.language == "json":
            self._rules.append((QRegularExpression(r'"(?:\\.|[^"\\])*"(?=\s*:)'), key_format))
        elif self.language == "xml":
            self._rules.extend(
                [
                    (QRegularExpression(r"<!--[\\s\\S]*?-->"), comment_format),
                    (QRegularExpression(r"</?[A-Za-z_][\\w:.-]*(?:\\s[^<>]*)?/?>"), tag_format),
                ]
            )
        elif self.language == "python":
            self._rules.extend(
                [
                    (QRegularExpression(r"#.*$"), comment_format),
                    (
                        QRegularExpression(
                            r"\b(?:and|as|assert|async|await|break|class|continue|def|del|elif|else|except|False|finally|for|from|if|import|in|is|lambda|None|not|or|pass|raise|return|True|try|while|with|yield)\b"
                        ),
                        keyword_format,
                    ),
                ]
            )
        elif self.language == "javascript":
            self._rules.extend(
                [
                    (QRegularExpression(r"//.*$"), comment_format),
                    (
                        QRegularExpression(
                            r"\b(?:break|case|catch|class|const|continue|debugger|default|delete|do|else|export|extends|false|finally|for|function|if|import|in|instanceof|let|new|null|return|switch|this|throw|true|try|typeof|undefined|var|void|while|with|yield)\b"
                        ),
                        keyword_format,
                    ),
                ]
            )
        elif self.language == "log":
            self._rules.extend(
                [
                    (QRegularExpression(r"\b(?:ERROR|CRITICAL|FATAL|EXCEPTION)\b"), severity_format),
                    (QRegularExpression(r"\b(?:WARN|WARNING)\b"), number_format),
                ]
            )

    def highlightBlock(self, text: str):
        for expression, char_format in self._rules:
            iterator = expression.globalMatch(text)
            while iterator.hasNext():
                match = iterator.next()
                start = match.capturedStart()
                length = match.capturedLength()
                if start >= 0 and length > 0:
                    self.setFormat(start, length, char_format)
