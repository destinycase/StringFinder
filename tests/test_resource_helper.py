"""개발 실행과 실행 파일 실행에서 자원 경로를 맞게 찾는지 확인합니다."""

import os
import sys

from sf_utils.resource_helper import get_resource_path


def test_get_resource_path_dev_mode(patch_sys_meipass_missing):
    """test_get_resource_path_dev 함수."""
    _ = patch_sys_meipass_missing
    # 개발 환경에서는 프로젝트 루트를 기준으로 상대 리소스 경로를 계산한다.
    path = get_resource_path("assets/icon.svg")

    assert "assets" in path
    assert "icon.svg" in path
    assert os.path.isabs(path)


def test_get_resource_path_frozen_mode(patch_sys_meipass_present):
    """test_get_resource_path_build 함수."""
    _ = patch_sys_meipass_present
    meipass_path = "C:/Temp/_MEI1234"
    setattr(sys, "_MEIPASS", meipass_path)

    path = get_resource_path("assets/icon.svg")

    expected = os.path.normpath(os.path.join(meipass_path, "assets/icon.svg"))
    assert os.path.normpath(path).lower() == expected.lower()
