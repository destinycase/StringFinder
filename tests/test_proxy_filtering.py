from PySide6.QtCore import Qt
from ui.proxies import MatchProxyModel, ResultProxyModel
from ui.models import SearchResultModel, MatchDetailModel


def test_search_result_model_edit_role():
    """SearchResultModel의 EditRole이 올바른 원본 데이터를 반환하는지 테스트"""
    model = SearchResultModel()
    results: list = [("path/file.txt", 5, [])]
    model.add_results(results)

    index = model.index(0, 1)
    assert model.data(index, Qt.ItemDataRole.DisplayRole) == "file.txt"
    assert model.data(index, Qt.ItemDataRole.EditRole) == "file.txt"


def test_match_detail_model_edit_role():
    """MatchDetailModel의 DisplayRole(HTML)과 EditRole(Plain)을 구분하여 반환하는지 테스트"""
    model = MatchDetailModel()
    matches = [(10, "This is a search_target line")]
    model.set_matches("test.txt", matches, search_text="search_target")

    index = model.index(0, 1)
    display_data = model.data(index, Qt.ItemDataRole.DisplayRole)
    edit_data = model.data(index, Qt.ItemDataRole.EditRole)

    assert "<span" in display_data
    assert "<span" not in edit_data
    assert "search_target" in edit_data


def test_match_proxy_model_filtering_with_html():
    """상세 필터가 HTML 태그 간섭 없이 동작하는지 테스트"""
    source_model = MatchDetailModel()
    matches = [(1, "prefix target suffix")]
    source_model.set_matches("test.txt", matches, search_text="target")

    proxy_model = MatchProxyModel()
    proxy_model.setSourceModel(source_model)

    proxy_model.setColumnFilter(1, "prefix")
    assert proxy_model.rowCount() == 1

    proxy_model.setColumnFilter(1, "target")
    assert proxy_model.rowCount() == 1

    proxy_model.setColumnFilter(1, "nonexistent")
    assert proxy_model.rowCount() == 0


def test_result_proxy_model_filtering():
    """결과 모델 필터링 동작 테스트"""
    source_model = SearchResultModel()
    results: list = [("C:/folder1/file1.txt", 10, []), ("C:/folder2/file2.log", 5, [])]
    source_model.add_results(results)

    proxy_model = ResultProxyModel()
    proxy_model.setSourceModel(source_model)

    proxy_model.setFileFilter("file1")
    assert proxy_model.rowCount() == 1

    proxy_model.setFileFilter("")
    proxy_model.setFolderFilter("folder2")
    assert proxy_model.rowCount() == 1
