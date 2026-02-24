import os
import subprocess


def open_file(file_path):
    """시스템 기본 프로그램으로 지정된 파일을 실행합니다."""
    try:
        if os.name == "nt":
            os.startfile(file_path)
        elif os.name == "posix":
            subprocess.call(("open", file_path))
    except (OSError, PermissionError, FileNotFoundError, subprocess.SubprocessError):
        # 파일 열기 실패: 권한 부족, 파일 없음, 시스템 오류
        return False
    return True


def sanitize_filename(name: str, replacement: str = "_") -> str:
    """sanitize_filename 함수."""
    # Windows 금지 문자: < > : " / \ | ? *
    invalid_chars = r'<>:"/\|?*'
    for char in invalid_chars:
        name = name.replace(char, replacement)
    name = "".join(c for c in name if ord(c) >= 32)
    # 앞뒤 공백/점 제거
    name = name.strip(". ")
    # 빈 문자열 방지
    if not name:
        name = "untitled"
    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "COM1",
        "COM2",
        "COM3",
        "COM4",
        "COM5",
        "COM6",
        "COM7",
        "COM8",
        "COM9",
        "LPT1",
        "LPT2",
        "LPT3",
        "LPT4",
        "LPT5",
        "LPT6",
        "LPT7",
        "LPT8",
        "LPT9",
    }
    if name.upper() in reserved:
        name = f"_{name}"
    return name
