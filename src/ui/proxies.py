from PySide6.QtCore import QSortFilterProxyModel, Qt


class ResultProxyModel(QSortFilterProxyModel):
    """검색 결과 목록의 정렬 및 필터링을 담당하는 프록시 모델입니다."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.file_filter = ""  # 파일명 필터 문자열
        self.folder_filter = ""  # 폴더 경로 필터 문자열

    def setFileFilter(self, text):
        self.file_filter = text.lower()
        sm = self.sourceModel()
        if sm and hasattr(sm, "set_filters"):
            sm.set_filters(self.file_filter, self.folder_filter)
        self.invalidate()

    def setFolderFilter(self, text):
        self.folder_filter = text.lower()
        sm = self.sourceModel()
        if sm and hasattr(sm, "set_filters"):
            sm.set_filters(self.file_filter, self.folder_filter)
        self.invalidate()

    def filterAcceptsRow(self, _source_row, _source_parent):
        """필터링은 소스 모델(SearchResultModel)에서 직접 처리합니다."""
        return True

    def lessThan(self, left, right):
        """두 항목의 크기를 비교하여 정렬 순서를 결정합니다."""
        left_data = self.sourceModel().data(left, Qt.ItemDataRole.EditRole)
        right_data = self.sourceModel().data(right, Qt.ItemDataRole.EditRole)
        if left_data is None:
            return False
        if right_data is None:
            return True
        # 숫자 컬럼(0: 매치 수) 처리
        if isinstance(left_data, (int, float)) and isinstance(right_data, (int, float)):
            return left_data < right_data
        # 문자열 컬럼(1: 파일명, 2: 폴더) 처리 - 대소문자 구분 없이 비교
        try:
            return str(left_data).lower() < str(right_data).lower()
        except Exception:
            # 타입 불일치 등 예외 발생 시 문자열로 변환하여 비교
            return str(left_data) < str(right_data)


class MatchProxyModel(QSortFilterProxyModel):
    """
    상세 목록의 각 컬럼을 독립적으로 필터링하는 프록시 모델입니다.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.filters = {}  # 컬럼 인덱스별 필터 텍스트 저장 (column_index: filter_text)

    def setColumnFilter(self, column, text):
        self.filters[column] = text.lower()
        sm = self.sourceModel()
        if sm and hasattr(sm, "set_column_filter"):
            sm.set_column_filter(column, text)
        self.invalidate()

    def setFilter0(self, text):
        self.setColumnFilter(0, text)

    def setFilter1(self, text):
        self.setColumnFilter(1, text)

    def setFilter2(self, text):
        self.setColumnFilter(2, text)

    def clearFilters(self):
        self.filters = {}
        sm = self.sourceModel()
        if sm and hasattr(sm, "set_column_filter"):
            # 소스 모델의 필터도 개별적으로 초기화해야 함
            for col in range(5):
                sm.set_column_filter(col, "")
        self.invalidate()

    def filterAcceptsRow(self, _source_row, _source_parent):
        """필터링은 소스 모델에서 직접 처리하므로 항상 True를 반환합니다."""
        return True
