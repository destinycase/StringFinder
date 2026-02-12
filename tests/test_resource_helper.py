import os
import sys
from utils.resource_helper import get_resource_path


def test_get_resource_path_dev(patch_sys_meipass_missing):
    """개발 환경(sys._MEIPASS 없음)에서 리소스 경로 계산 테스트"""
    # resource_helper.py의 위치를 기준으로 상위 폴더(src)의 상위(root)를 기준으로 함
    # 02-12 기준 구조: d:/Project/StringFinder/src/utils/resource_helper.py
    # base_path는 d:/Project/StringFinder 가 되어야 함
    path = get_resource_path("assets/icon.svg")

    assert "assets" in path
    assert "icon.svg" in path
    assert os.path.isabs(path)


def test_get_resource_path_build(patch_sys_meipass_present):
    """빌드 환경(sys._MEIPASS 존재)에서 리소스 경로 계산 테스트"""
    meipass_path = "C:/Temp/_MEI1234"
    sys._MEIPASS = meipass_path

    path = get_resource_path("assets/icon.svg")

    expected = os.path.normpath(os.path.join(meipass_path, "assets/icon.svg"))
    assert os.path.normpath(path).lower() == expected.lower()
