import os
import sys


def get_resource_path(relative_path):
    """
    개발 환경과 PyInstaller 빌드 환경 모두에서 리소스 파일의 절대 경로를 반환합니다.

    Args:
        relative_path (str): 'resources/icon.png' 형태의 상대 경로

    Returns:
        str: 리소스의 절대 경로
    """
    try:
        # PyInstaller는 임시 폴더인 _MEIPASS에 리소스를 압축 해제함
        base_path = sys._MEIPASS
    except (AttributeError, FileNotFoundError, OSError):
        # PyInstaller 환경에서 _MEIPASS 속성 없음 또는 파일 시스템 오류
        # 개발 환경에서는 src 폴더를 기준으로 함
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    return os.path.join(base_path, relative_path)
