from utils.app_strings import AppStrings


def test_app_strings_constants():
    """문자열 상수 로딩 테스트"""
    assert AppStrings.APP_NAME == "String Finder"
    assert AppStrings.SEARCH_LABEL == "검색 문자열:"


def test_app_version():
    """앱 버전 상수 테스트"""
    assert AppStrings.APP_VERSION == "2.4.4"
    assert "String Finder v2.4.4" in AppStrings.APP_TITLE
