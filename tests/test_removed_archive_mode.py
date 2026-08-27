from core.search_engine import search_in_file
from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants


def test_removed_format_is_not_a_special_search_option():
    assert all("archive" not in item.lower() for item in AppStrings.SPECIAL_SEARCH_ITEMS)
    assert not hasattr(Constants, "MODE_ARCHIVE")
    assert not hasattr(Constants, "EXT_ARCHIVE")


def test_removed_format_is_searched_as_plain_text(tmp_path):
    file_path = tmp_path / ("sample" + ".archive")
    file_path.write_text("plain needle content\n", encoding="utf-8")

    result = search_in_file(str(file_path), "needle")

    assert isinstance(result, tuple) and len(result) == 3
    assert result[1] == 1
    assert "needle" in result[2][0][1]
