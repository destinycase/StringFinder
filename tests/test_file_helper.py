"""
[test_file_helper.py]

이 테스트는 파일 및 경로 관리를 위한 유틸리티 함수들의 정확성을 검증합니다.

- 테스트 목적:
  1. 운영체제별(특히 Windows) 제한 사항을 고려한 파일명 샌니타이징 로직의 무결성 보장.
  2. 시스템 예약어 또는 특수 제어 문자로 인한 파일 시스템 크래시 예방.

- 주요 검증 사항:
  1. 윈도우 금지 문자(`?`, `<`, `>`, `:`, `|`, `*`, `/`, r"\")의 교체 처리.
  2. `CON`, `PRN`, `AUX`, `NUL` 등 윈도우 예약 디바이스 파일명 탐지 및 우회.
  3. 공백 문자 트리밍 및 비정상적인 빈 파일명에 대한 기본값 할당.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
from sf_utils.file_helper import sanitize_filename


class TestSanitizeFilename:
    def test_windows_forbidden_chars(self):
        assert sanitize_filename("file?name") == "file_name"
        assert sanitize_filename("file<name") == "file_name"
        assert sanitize_filename("file>name") == "file_name"
        assert sanitize_filename("file:name") == "file_name"
        assert sanitize_filename("file|name") == "file_name"
        assert sanitize_filename('file"name') == "file_name"
        assert sanitize_filename("file*name") == "file_name"
        assert sanitize_filename("file/name") == "file_name"
        assert sanitize_filename("file\\name") == "file_name"

    def test_multiple_forbidden_chars(self):
        assert sanitize_filename("file?<>|*name") == "file_____name"
        assert sanitize_filename('test:"file"') == "test__file_"

    def test_control_chars(self):
        assert sanitize_filename("file\x00name") == "filename"
        assert sanitize_filename("file\x1fname") == "filename"

    def test_reserved_names(self):
        assert sanitize_filename("CON") == "_CON"
        assert sanitize_filename("PRN") == "_PRN"

    def test_reserved_names_case_insensitive(self):
        assert sanitize_filename("con") == "_con"

    def test_empty_string(self):
        assert sanitize_filename("") == "untitled"
        assert sanitize_filename("   ") == "untitled"

    def test_whitespace_trimming(self):
        assert sanitize_filename("  filename  ") == "filename"

    def test_path_traversal(self):
        result = sanitize_filename("../../../etc/passwd")
        assert "/" not in result
        assert "\\" not in result

    def test_normal_filenames(self):
        assert sanitize_filename("normal_file.txt") == "normal_file.txt"
        assert sanitize_filename("korean_file.txt") == "korean_file.txt"

    def test_custom_replacement(self):
        assert sanitize_filename("file?name", replacement="-") == "file-name"

    def test_edge_cases(self):
        result = sanitize_filename("?<>|*")
        assert result != ""
