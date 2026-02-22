"""
[test_proxy_filtering.py]

이 테스트는 UI 모델과 뷰 사이의 데이터 변환 및 필터링 프록시 로직을 검증합니다.

- 테스트 목적:
  1. 하이라이팅 태그(HTML)가 포함된 데이터가 프록시 필터링 중에도 정확한 검색 결과를 필터링하는지 확인.
  2. 클립보드 복사 또는 편집 등을 위한 순수 텍스트 데이터(EditRole)와 표시용 데이터(DisplayRole)의 분리 무결성 보장.

- 주요 검증 사항:
  1. `MatchProxyModel` 및 `ResultProxyModel`의 컬럼 기반 동적 필터링 작동.
  2. HTML 태그가 제거된 원본 데이터를 기준으로 하는 검색 필터 정확도.
"""

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
    proxy_model.setColumnFilter(0, "prefix")
    assert proxy_model.rowCount() == 1

    proxy_model.setColumnFilter(0, "target")
    assert proxy_model.rowCount() == 1

    proxy_model.setColumnFilter(0, "nonexistent")
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
