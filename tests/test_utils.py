"""
[test_utils.py]

이 테스트는 파일 열기와 실행 가능 파일 분류 유틸리티를 검증합니다.

- 테스트 목적:
  1. 운영체제별 파일 열기와 실행 가능 파일 분류의 안정성 보장.

- 주요 검증 사항:
  1. 현재 검색 입력 기록 동작은 test_search_history.py에서 검증.
  2. 운영체제별 파일 열기(`os.startfile` 등) 명령의 성공적 호출 및 예외 처리.
"""

import os
import subprocess
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from sf_utils.file_helper import is_potentially_executable_file, open_file






def test_open_file_windows(tmp_path):
    """test_open_file_windows 함수."""
    file_path = tmp_path / "test.txt"
    file_path.touch()

    with patch("os.startfile") as mock_startfile:
        with patch("os.name", "nt"):
            result = open_file(str(file_path))
            assert result is True
            mock_startfile.assert_called_once_with(str(file_path))


def test_open_file_exception():
    """test_open_file_exception 함수."""
    with patch("os.startfile", side_effect=OSError("Error")):
        with patch("os.name", "nt"):
            result = open_file("invalid/path")
            assert result is False


def test_open_file_linux_uses_xdg_open(monkeypatch, tmp_path):
    file_path = tmp_path / "test.txt"
    file_path.touch()
    monkeypatch.setattr("sf_utils.file_helper.os.name", "posix")
    monkeypatch.setattr("sf_utils.file_helper.sys.platform", "linux")
    monkeypatch.setattr("sf_utils.file_helper.shutil.which", lambda name: "/usr/bin/xdg-open" if name == "xdg-open" else None)

    with patch("sf_utils.file_helper.subprocess.run") as run:
        run.return_value.returncode = 0
        assert open_file(str(file_path)) is True

    run.assert_called_once_with(
        ["/usr/bin/xdg-open", str(file_path)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def test_potentially_executable_file_classification(monkeypatch):
    monkeypatch.setenv("PATHEXT", ".EXE;.COM;.CUSTOM")

    assert is_potentially_executable_file("C:/downloads/tool.EXE")
    assert is_potentially_executable_file("C:/downloads/script.ps1")
    assert is_potentially_executable_file("C:/downloads/link.lnk")
    assert is_potentially_executable_file("/tmp/install.command")
    assert is_potentially_executable_file("/tmp/install.sh")
    assert is_potentially_executable_file("C:/downloads/tool.custom")
    assert not is_potentially_executable_file("C:/documents/report.txt")
