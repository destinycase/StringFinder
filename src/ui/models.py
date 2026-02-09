from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QFileInfo
import os


class SearchResultModel(QAbstractTableModel):
    """
    파일 검색 결과(파일 경로, 매칭 횟수)를 관리하는 고성능 모델입니다.
    수백만 개의 행이 있어도 화면에 보이는 부분만 처리하여 성능을 유지합니다.
    """

    def __init__(self, icon_provider):
        """모델을 초기화하고 검색 엔진의 결과 항목 헤더를 설정합니다."""
        super().__init__()
        from utils.app_strings import AppStrings

        self.headers = [AppStrings.HEADER_COUNT, AppStrings.HEADER_FILE_PATH]
        self._data = []  # [count, file_path, matches] 형태의 리스트
        self.icon_provider = icon_provider

    def rowCount(self, parent=QModelIndex()):
        return len(self._data)

    def columnCount(self, parent=QModelIndex()):
        return len(self.headers)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._data)):
            return None

        row_data = self._data[index.row()]
        col = index.column()

        if role == Qt.DisplayRole:
            if col == 0:
                return str(row_data[0])
            elif col == 1:
                return row_data[1]

        elif role == Qt.EditRole:
            if col == 0:
                return row_data[0]
            return row_data[1]

        elif role == Qt.DecorationRole and col == 1:
            # 파일 아이콘 표시 (QFileInfo 객체 전달 필요)
            file_path = row_data[1]
            return self.icon_provider.icon(QFileInfo(os.path.abspath(file_path)))

        elif role == Qt.ToolTipRole and col == 1:
            return row_data[1]

        elif role == Qt.TextAlignmentRole:
            if col == 0:
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        elif role == Qt.UserRole:
            # 전체 데이터 반환 (file_path, matches)
            return row_data[1], row_data[2]

        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.headers[section]
        return None

    def sort(self, column, order=Qt.AscendingOrder):
        """특정 열을 기준으로 데이터를 오름차순 또는 내림차순으로 정렬합니다."""
        self.layoutAboutToBeChanged.emit()
        reverse = order == Qt.DescendingOrder
        # 리스트의 해당 인덱스 값을 기준으로 정렬을 수행합니다.
        self._data.sort(key=lambda x: x[column], reverse=reverse)
        self.layoutChanged.emit()

    def clear(self):
        self.beginResetModel()
        self._data = []
        self.endResetModel()

    def add_results(self, results):
        """여러 개의 검색 결과를 모델에 한꺼번에 추가(Batch Insert)합니다. (file_path, count, matches)"""
        if not results:
            return

        self.beginInsertRows(QModelIndex(), len(self._data), len(self._data) + len(results) - 1)
        for file_path, count, matches in results:
            self._data.append([count, file_path, matches])
        self.endInsertRows()

    def get_full_data(self, row):
        """특정 행의 전체 데이터(경로 및 매칭 리스트)를 반환합니다."""
        if 0 <= row < len(self._data):
            return self._data[row][1], self._data[row][2]
        return None, None


class MatchDetailModel(QAbstractTableModel):
    """
    특정 파일 내의 상세 매칭 정보(라인 번호, 내용)를 관리하는 모델입니다.
    """

    def __init__(self):
        """모델을 초기화하고 매칭 상세 정보(라인, 내용)의 헤더를 설정합니다."""
        super().__init__()
        from utils.app_strings import AppStrings

        self.headers = [AppStrings.HEADER_POSITION, AppStrings.HEADER_CONTENT]
        self._data = []  # (line_no, content) 튜플 리스트
        self.current_file_path = ""

    def rowCount(self, parent=QModelIndex()):
        return len(self._data)

    def columnCount(self, parent=QModelIndex()):
        return len(self.headers)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._data)):
            return None

        line_no, content = self._data[index.row()]
        col = index.column()

        if role == Qt.DisplayRole or role == Qt.EditRole:
            if col == 0:
                return str(line_no)
            return content

        elif role == Qt.TextAlignmentRole:
            if col == 0:
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        elif role == Qt.UserRole:
            return self.current_file_path

        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.headers[section]
        return None

    def set_matches(self, file_path, matches):
        """특정 파일의 매칭 상세 목록을 모델에 설정하고 뷰를 갱신합니다."""
        self.beginResetModel()
        self.current_file_path = file_path
        self._data = matches
        self.endResetModel()

    def clear(self):
        self.beginResetModel()
        self._data = []
        self.current_file_path = ""
        self.endResetModel()

    def get_line_no(self, row):
        """특정 행의 실제 소스 코드 라인 번호를 반환합니다."""
        if 0 <= row < len(self._data):
            return self._data[row][0]
        return None
