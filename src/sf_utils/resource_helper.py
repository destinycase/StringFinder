import os
import sys


def get_resource_path(relative_path):
    """get_resource_path 함수."""
    try:
        base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    except (AttributeError, FileNotFoundError, OSError):
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)
