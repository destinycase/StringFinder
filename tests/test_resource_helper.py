"""
[test_resource_helper.py]

이 테스트는 애플리케이션 리소스 경로 확인 유틸리티(`resource_helper`)의 정확성을 검증합니다.

- 테스트 목적:
  1. 일반 개발 환경과 PyInstaller 등으로 패키징된 빌드 환경(`#_MEIPASS`) 모두에서 리소스 파일을 올바르게 찾을 수 있는지 확인.

- 주요 검증 사항:
  1. 개발 환경에서의 프로젝트 루트 기준 상대 경로 계산.
  2. 빌드 환경에서의 임시 디렉토리(`_MEIPASS`) 경로 결합 무결성.
"""

import os
import sys

from sf_utils.resource_helper import get_resource_path


def test_get_resource_path_dev(patch_sys_meipass_missing):
    """test_get_resource_path_dev 함수."""
    # 개발 환경에서는 프로젝트 루트를 기준으로 상대 리소스 경로를 계산한다.
    path = get_resource_path("assets/icon.svg")

    assert "assets" in path
    assert "icon.svg" in path
    assert os.path.isabs(path)


def test_get_resource_path_build(patch_sys_meipass_present):
    """test_get_resource_path_build 함수."""
    meipass_path = "C:/Temp/_MEI1234"
    setattr(sys, "_MEIPASS", meipass_path)

    path = get_resource_path("assets/icon.svg")

    expected = os.path.normpath(os.path.join(meipass_path, "assets/icon.svg"))
    assert os.path.normpath(path).lower() == expected.lower()
