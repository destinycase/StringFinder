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
        # preserve quotes handling if implemented, usually replaced
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
