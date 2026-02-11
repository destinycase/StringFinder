from PySide6.QtCore import Qt
from ui.proxies import MatchProxyModel, ResultProxyModel
from ui.models import SearchResultModel, MatchDetailModel


def test_search_result_model_edit_role():
    """SearchResultModel이 EditRole에 대해 올바른 원본 데이터를 반환하는지 테스트"""
    model = SearchResultModel()
    results = [("path/file.txt", 5, [])]
    model.add_results(results)

    index = model.index(0, 1)  # 파일명 컬럼
    assert model.data(index, Qt.DisplayRole) == "file.txt"
    assert model.data(index, Qt.EditRole) == "file.txt"


def test_match_detail_model_edit_role():
    """MatchDetailModel이 DisplayRole(HTML)과 EditRole(Plain)을 구분하여 반환하는지 테스트"""
    model = MatchDetailModel()
    matches = [(10, "This is a search_target line")]
    model.set_matches("test.txt", matches, search_text="search_target")

    index = model.index(0, 1)  # 내용 컬럼
    display_data = model.data(index, Qt.DisplayRole)
    edit_data = model.data(index, Qt.EditRole)

    # DisplayRole에는 HTML 강조 태그가 포함되어야 함
    assert "<span" in display_data
    # EditRole에는 강조 태그가 없어야 함
    assert "<span" not in edit_data
    assert "search_target" in edit_data


def test_match_proxy_model_filtering_with_html():
    """상세 필터가 HTML 태그 간섭 없이 동작하는지 테스트"""
    source_model = MatchDetailModel()
    # 'target' 이 강조되어 HTML 태그가 삽입된 상태를 가정
    matches = [(1, "prefix target suffix")]
    source_model.set_matches("test.txt", matches, search_text="target")

    proxy_model = MatchProxyModel()
    proxy_model.setSourceModel(source_model)

    # 'target' 단어 중간을 가로지르는 필터링 시도 (기존 버그 상황 재현)
    # 실제로는 'prefix' 나 'suffix' 처럼 강조되지 않은 부분으로 필터링해도
    # 강조된 부분 때문에 문자열이 끊기지 않는지 확인
    proxy_model.setColumnFilter(1, "prefix")
    assert proxy_model.rowCount() == 1

    proxy_model.setColumnFilter(1, "target")
    assert proxy_model.rowCount() == 1

    proxy_model.setColumnFilter(1, "nonexistent")
    assert proxy_model.rowCount() == 0


def test_result_proxy_model_filtering():
    """결과 모델 필터링 동작 테스트"""
    source_model = SearchResultModel()
    results = [("C:/folder1/file1.txt", 10, []), ("C:/folder2/file2.log", 5, [])]
    source_model.add_results(results)

    proxy_model = ResultProxyModel()
    proxy_model.setSourceModel(source_model)

    # 파일 필터 적용
    proxy_model.setFileFilter("file1")
    assert proxy_model.rowCount() == 1

    # 폴더 필터 적용
    proxy_model.setFileFilter("")
    proxy_model.setFolderFilter("folder2")
    assert proxy_model.rowCount() == 1
