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
        # PyInstaller의 임시 폴더인 _MEIPASS에 리소스들이 압축 해제됨
        base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    except (AttributeError, FileNotFoundError, OSError):
        # 개발 환경에서는 src 폴더를 기준으로 함
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    return os.path.join(base_path, relative_path)
