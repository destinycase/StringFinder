import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from utils.file_helper import sanitize_filename


class TestSanitizeFilename:
    """sanitize_filename 함수 테스트"""

    def test_windows_forbidden_chars(self):
        """Windows 금지 문자 제거 테스트"""
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
        """여러 금지 문자 동시 제거"""
        assert sanitize_filename("file?<>|*name") == "file_____name"
        assert sanitize_filename('test:"file"') == "test__file_"

    def test_control_chars(self):
        """제어 문자 제거 테스트"""
        assert sanitize_filename("file\x00name") == "filename"
        assert sanitize_filename("file\x1fname") == "filename"
        assert sanitize_filename("file\x01\x02\x03name") == "filename"

    def test_reserved_names(self):
        """Windows 예약어 처리 테스트"""
        assert sanitize_filename("CON") == "_CON"
        assert sanitize_filename("PRN") == "_PRN"
        assert sanitize_filename("AUX") == "_AUX"
        assert sanitize_filename("NUL") == "_NUL"
        assert sanitize_filename("COM1") == "_COM1"
        assert sanitize_filename("COM2") == "_COM2"
        assert sanitize_filename("LPT1") == "_LPT1"
        assert sanitize_filename("LPT9") == "_LPT9"

    def test_reserved_names_case_insensitive(self):
        """예약어 대소문자 구분 없이 처리"""
        assert sanitize_filename("con") == "_con"
        assert sanitize_filename("Con") == "_Con"
        assert sanitize_filename("prn") == "_prn"

    def test_empty_string(self):
        """빈 문자열 처리 테스트"""
        assert sanitize_filename("") == "untitled"
        assert sanitize_filename("   ") == "untitled"
        assert sanitize_filename("...") == "untitled"
        assert sanitize_filename("   .  ") == "untitled"

    def test_whitespace_trimming(self):
        """앞뒤 공백 및 점 제거"""
        assert sanitize_filename("  filename  ") == "filename"
        assert sanitize_filename("..filename..") == "filename"
        assert sanitize_filename(" . filename . ") == "filename"

    def test_path_traversal(self):
        """경로 탐색 공격 방지 테스트"""
        result = sanitize_filename("../../../etc/passwd")
        # 실제 구현은 .. 을 _ 로 변환
        assert "/" not in result
        assert "\\" not in result

    def test_normal_filenames(self):
        """정상적인 파일명은 그대로 유지"""
        assert sanitize_filename("normal_file.txt") == "normal_file.txt"
        assert sanitize_filename("file-name_123") == "file-name_123"
        assert sanitize_filename("한글파일명") == "한글파일명"

    def test_custom_replacement(self):
        """사용자 정의 대체 문자"""
        assert sanitize_filename("file?name", replacement="-") == "file-name"
        assert sanitize_filename("file|name", replacement="") == "filename"

    def test_edge_cases(self):
        """엣지 케이스"""
        # 모든 문자가 금지 문자인 경우 - 실제로는 _ 로 변환됨
        result = sanitize_filename("?<>|*")
        assert result != "", "Should not return empty string"
        # 예약어 + 확장자는 정상 처리
        assert "CON" in sanitize_filename("CON.txt") or "_CON" in sanitize_filename("CON.txt")
