"""결과·상세 표 모델의 편집 역할과 거르기(HTML 강조 포함)."""

from PySide6.QtCore import Qt

from ui.models import MatchDetailModel, SearchResultModel
from ui.proxies import MatchProxyModel, ResultProxyModel


def test_search_result_model_edit_role():
    """test_search_result_model_edit_role 함수."""
    model = SearchResultModel()
    results: list = [("path/file.txt", 5, [])]
    model.add_results(results)

    index = model.index(0, 1)
    assert model.data(index, Qt.ItemDataRole.DisplayRole) == "file.txt"
    assert model.data(index, Qt.ItemDataRole.EditRole) == "file.txt"


def test_match_detail_model_edit_role():
    """test_match_detail_model_edit_role 함수."""
    model = MatchDetailModel()
    matches = [(10, "This is a search_target line")]
    model.set_matches("test.txt", matches, search_text="search_target")

    index = model.index(0, 0)  # Normal 모드에서는 0번 컬럼이 통합 목록
    display_data = model.data(index, Qt.ItemDataRole.DisplayRole)
    edit_data = model.data(index, Qt.ItemDataRole.EditRole)

    assert "<span" in display_data
    assert "<span" not in edit_data
    assert "search_target" in edit_data


def test_match_proxy_model_filtering_with_html():
    """test_match_proxy_model_filtering_with_html 함수."""
    source_model = MatchDetailModel()
    matches = [(1, "prefix target suffix")]
    source_model.set_matches("test.txt", matches, search_text="target")

    proxy_model = MatchProxyModel()
    proxy_model.setSourceModel(source_model)

    # Normal 모드에서는 모든 필터링이 0번 컬럼으로 수행됨
    source_model.set_column_filter(0, "prefix")
    assert proxy_model.rowCount() == 1

    source_model.set_column_filter(0, "target")
    assert proxy_model.rowCount() == 1

    source_model.set_column_filter(0, "nonexistent")
    assert proxy_model.rowCount() == 0


def test_result_proxy_model_filtering():
    """결과 모델 필터링 동작 테스트"""
    source_model = SearchResultModel()
    results: list = [("C:/folder1/file1.txt", 10, []), ("C:/folder2/file2.log", 5, [])]
    source_model.add_results(results)

    proxy_model = ResultProxyModel()
    proxy_model.setSourceModel(source_model)

    source_model.set_filters("file1", "")
    assert proxy_model.rowCount() == 1

    source_model.set_filters("", "folder2")
    assert proxy_model.rowCount() == 1
