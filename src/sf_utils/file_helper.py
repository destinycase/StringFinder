import os
import shutil
import subprocess


def open_file(file_path):
    """시스템 기본 프로그램으로 지정된 파일을 실행합니다."""
    try:
        if os.name == "nt":
            os.startfile(file_path)
        elif os.name == "posix":
            subprocess.call(("open", file_path))
    except (OSError, PermissionError, FileNotFoundError, subprocess.SubprocessError):
        # 권한 부족, 파일 부재 등 다양한 시스템 오류로 인해 파일을 열 수 없는 경우 false를 반환합니다.
        return False
    return True


def open_in_external_editor(file_path: str, line: int = 1, editor_settings=None) -> bool:
    """설정된 편집기로 파일을 열고, 가능한 경우 지정한 줄로 이동합니다."""
    if not file_path:
        return False

    settings = editor_settings if isinstance(editor_settings, dict) else {}
    editor_type = settings.get("editor_type", "system")
    if editor_type == "system":
        return open_file(file_path)

    executables = {
        "vscode": "code",
        "cursor": "cursor",
        "notepadpp": "notepad++",
        "sublime": "subl",
    }
    executable = settings.get("custom_path", "") if editor_type == "custom" else executables.get(editor_type)
    if not executable:
        return open_file(file_path)
    if editor_type == "custom":
        executable = os.path.abspath(os.path.expandvars(os.path.expanduser(str(executable))))
        if not os.path.isfile(executable):
            return open_file(file_path)
    else:
        executable = shutil.which(executable)
        if not executable:
            return open_file(file_path)

    try:
        try:
            line_number = max(1, int(line))
        except (TypeError, ValueError):
            line_number = 1

        if editor_type in {"vscode", "cursor"}:
            command = [executable, "--goto", f"{file_path}:{line_number}"]
        elif editor_type == "notepadpp":
            command = [executable, file_path, f"-n{line_number}"]
        else:
            command = [executable, f"{file_path}:{line_number}"]
        subprocess.Popen(command, shell=False)
        return True
    except (OSError, PermissionError, FileNotFoundError, subprocess.SubprocessError):
        return open_file(file_path)


def sanitize_filename(name: str, replacement: str = "_") -> str:
    """파일명으로 사용할 수 없는 특수 문자나 예약어를 안전한 문자로 변환합니다."""
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
