import os
import subprocess


def open_file(file_path):
    """시스템 기본 프로그램으로 지정된 파일을 실행합니다."""
    try:
        if os.name == "nt":
            os.startfile(file_path)
        elif os.name == "posix":
            subprocess.call(("open", file_path))
    except Exception:
        return False
    return True
