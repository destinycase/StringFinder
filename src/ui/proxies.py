from PySide6.QtCore import QSortFilterProxyModel, Qt


class ResultProxyModel(QSortFilterProxyModel):
    """검색 결과 목록의 정렬을 담당합니다. 필터링과 페이지 구성은 원본 모델에서 처리합니다."""



    def filterAcceptsRow(self, _source_row, _source_parent):
        """필터링은 소스 모델(SearchResultModel)에서 직접 처리합니다."""
        return True

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        """머리글 정렬을 현재 쪽이 아니라 전체 결과에 적용합니다(검수 U-6).

        화면 표에는 현재 쪽(최대 1,000행)만 있으므로 여기서 정렬하면 쪽 안에서만 순서가 바뀝니다.
        원본 모델이 전체 결과를 정렬하고 첫 쪽부터 다시 보여 줍니다.
        """
        source = self.sourceModel()
        if source is not None and hasattr(source, "sort_all"):
            source.sort_all(column, order)
            return
        super().sort(column, order)

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
    상세 목록을 뷰에 연결합니다. 컬럼별 필터링은 원본 모델에서 처리합니다.
    """


    def filterAcceptsRow(self, _source_row, _source_parent):
        """필터링은 소스 모델에서 직접 처리하므로 항상 True를 반환합니다."""
        return True
