from PySide6.QtCore import QSortFilterProxyModel, Qt


class ResultProxyModel(QSortFilterProxyModel):
    """
    파일(컬럼 1)과 폴더(컬럼 2)를 각각의 필터 문자열로 동시에 필터링하는 프록시 모델입니다.
    데이터 표시(DisplayRole)가 아닌 실제 값(EditRole)을 기준으로 검색하여 HTML 강조 태그의 간섭을 피합니다.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.file_filter = ""  # 파일명 필터 문자열
        self.folder_filter = ""  # 폴더 경로 필터 문자열

    def setFileFilter(self, text):
        self.file_filter = text.lower()
        self.invalidate()

    def setFolderFilter(self, text):
        self.folder_filter = text.lower()
        self.invalidate()

    def filterAcceptsRow(self, source_row, source_parent):
        # 컬럼 1: 파일, 컬럼 2: 폴더
        model = self.sourceModel()

        file_idx = model.index(source_row, 1, source_parent)
        folder_idx = model.index(source_row, 2, source_parent)

        # DisplayRole(표시용) 대신 EditRole(원본 데이터)을 사용하여 필터링
        file_data = str(model.data(file_idx, Qt.ItemDataRole.EditRole) or "").lower()
        folder_data = str(model.data(folder_idx, Qt.ItemDataRole.EditRole) or "").lower()

        return (self.file_filter in file_data) and (self.folder_filter in folder_data)

    def lessThan(self, left, right):
        """
        정렬 속도 최적화:
        DisplayRole(HTML/Regex 처리 포함) 대신 EditRole(Raw Data)을 사용하여 비교합니다.
        기본 lessThan은 DisplayRole을 사용하여 렌더링 오버헤드로 인해 정렬이 매우 느려질 수 있습니다.
        """
        left_data = self.sourceModel().data(left, Qt.ItemDataRole.EditRole)
        right_data = self.sourceModel().data(right, Qt.ItemDataRole.EditRole)

        # None 처리
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
        self.invalidate()

    def clearFilters(self):
        self.filters = {}
        self.invalidate()

    def filterAcceptsRow(self, source_row, source_parent):
        if not self.filters:
            return True

        model = self.sourceModel()
        for col, filter_text in self.filters.items():
            if not filter_text:
                continue

            idx = model.index(source_row, col, source_parent)
            # DisplayRole은 HTML 태그를 포함할 수 있으므로 EditRole(순수 텍스트) 사용
            data = model.data(idx, Qt.ItemDataRole.EditRole)
            if data is None:
                return False

            if filter_text not in str(data).lower():
                return False

        return True
