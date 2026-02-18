import os
import sys
from sf_utils.resource_helper import get_resource_path


def test_get_resource_path_dev(patch_sys_meipass_missing):
    """媛쒕컻 ?섍꼍(sys._MEIPASS ?놁쓬)?먯꽌 由ъ냼??寃쎈줈 怨꾩궛 ?뚯뒪??"""
    # resource_helper.py???꾩튂瑜?湲곗??쇰줈 ?곸쐞 ?대뜑(src)???곸쐞(root)瑜?湲곗??쇰줈 ??
    # 02-12 湲곗? 援ъ“: d:/Project/StringFinder/src/utils/resource_helper.py
    # base_path??d:/Project/StringFinder 媛  ?섏뼱????
    path = get_resource_path("assets/icon.svg")

    assert "assets" in path
    assert "icon.svg" in path
    assert os.path.isabs(path)


def test_get_resource_path_build(patch_sys_meipass_present):
    """鍮뚮뱶 ?섍꼍(sys._MEIPASS 議댁옱)?먯꽌 由ъ냼??寃쎈줈 怨꾩궛 ?뚯뒪??"""
    meipass_path = "C:/Temp/_MEI1234"
    meipass_path = "C:/Temp/_MEI1234"
    setattr(sys, "_MEIPASS", meipass_path)

    path = get_resource_path("assets/icon.svg")

    expected = os.path.normpath(os.path.join(meipass_path, "assets/icon.svg"))
    assert os.path.normpath(path).lower() == expected.lower()
