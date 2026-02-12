from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex
import os
import re
from html import escape


class SearchResultModel(QAbstractTableModel):
    """
    파일 검색 결과(파일 경로, 매칭 횟수)를 관리하는 고성능 모델입니다.
    수백만 개의 행이 있어도 화면에 보이는 부분만 처리하여 성능을 유지합니다.
    """

    def __init__(self, icon_provider=None):
        """모델을 초기화하고 검색 엔진의 결과 항목 헤더를 설정합니다."""
        super().__init__()
        from utils.app_strings import AppStrings

        # 컬럼 구성: 일치 한 수, 파일, 폴더 경로
        self.headers = [AppStrings.HEADER_COUNT, AppStrings.HEADER_FILE, AppStrings.HEADER_FOLDER]
        self._data = []  # [count, filename_with_ext, folder, full_path, matches] 형태의 리스트
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

        if role in [Qt.DisplayRole, Qt.EditRole]:
            # 0: 일치, 1: 파일(파일명.확장자), 2: 폴더
            return row_data[col]

        elif role == Qt.TextAlignmentRole:
            if col == 0:
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        elif role == Qt.ToolTipRole:
            # 모든 컬럼에서 전체 파일 경로를 툴팁으로 제공
            return row_data[3]

        elif role == Qt.UserRole:
            # 전체 데이터 반환 (full_path, matches)
            return row_data[3], row_data[4]

        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.headers[section]
        return None

    def sort(self, column, order=Qt.AscendingOrder):
        """특정 열을 기준으로 데이터를 오름차순 또는 내림차순으로 정렬합니다."""
        self.layoutAboutToBeChanged.emit()
        reverse = order == Qt.DescendingOrder
        # 데이터 구조 [count, file, folder, full_path, matches] 에 맞춰 정렬 (숫자는 숫자 그대로 비교)
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
            # 경로 분해: 폴더, 파일명+확장자
            folder = os.path.dirname(file_path)
            file_name_with_ext = os.path.basename(file_path)

            # _data 구조: [count, filename_with_ext, folder, file_path, matches]
            self._data.append([count, file_name_with_ext, folder, file_path, matches])
        self.endInsertRows()

    def get_full_data(self, row):
        """특정 행의 전체 데이터(경로 및 매칭 리스트)를 반환합니다."""
        if 0 <= row < len(self._data):
            return self._data[row][3], self._data[row][4]
        return None, None


class MatchDetailModel(QAbstractTableModel):
    """
    특정 파일 내의 상세 매칭 정보(라인 번호, 내용)를 관리하는 모델입니다.
    """

    def __init__(self):
        """모델을 초기화하고 매칭 상세 정보(라인, 내용)의 헤더를 설정합니다."""
        super().__init__()
        from utils.app_strings import AppStrings

        self._headers = [AppStrings.HEADER_POSITION, AppStrings.HEADER_CONTENT]
        self._data = []  # (line_no, name/content, [value]) 튜플/리스트
        self.current_file_path = ""
        self.search_text = ""
        self.search_mode = "Normal"
        self.highlight_pattern = None

    def rowCount(self, parent=QModelIndex()):
        return len(self._data)

    def columnCount(self, parent=QModelIndex()):
        return len(self._headers)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._data)):
            return None

        row_data = self._data[index.row()]
        col = index.column()

        if role == Qt.DisplayRole:
            if col >= len(row_data):
                return None
            val = str(row_data[col])

            # 검색 강조 처리 (HTML) - 위치 컬럼 이외의 텍스트 컬럼에만 적용
            # 바이너리 플레이스홀더 등 특수 메시지는 강조 제외
            if self.search_text and col > 0 and not val.startswith("[바이너리 파일"):
                # "전체 일치" 모드인지 확인
                if "전체 일치" in self.search_mode:
                    if val.lower() == self.search_text.lower():
                        return f"<span style='color: #ff9900; font-weight: bold;'>{escape(val)}</span>"
                else:
                    # 부분 일치 모드: 미리 컴파일된 정규식 재사용
                    if self.highlight_pattern:
                        parts = []
                        last_end = 0
                        for match in self.highlight_pattern.finditer(val):
                            parts.append(escape(val[last_end : match.start()]))
                            parts.append(
                                f"<span style='color: #ff9900; font-weight: bold;'>{escape(match.group())}</span>"
                            )
                            last_end = match.end()
                        parts.append(escape(val[last_end:]))
                        return "".join(parts)
                return escape(val)
            # 위치 컬럼 또는 검색어 없는 경우에도 델리게이트 호환성을 위해 이스케이프
            return escape(val)

        elif role == Qt.EditRole:
            if col >= len(row_data):
                return ""
            return str(row_data[col])

        elif role == Qt.TextAlignmentRole:
            if col == 0:
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        elif role == Qt.UserRole:
            return self.current_file_path

        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            if section < len(self._headers):
                return self._headers[section]
        return None

    def set_matches(self, file_path, matches, search_text="", search_mode="Normal"):
        """검색 모드에 따라 컬럼 수와 헤더를 조정하고 데이터를 설정합니다."""
        from utils.app_strings import AppStrings

        self.beginResetModel()

        self.current_file_path = file_path
        self.search_text = search_text
        self.search_mode = search_mode

        # 정규식 미리 컴파일 (부분 일치 모드일 경우)
        if search_text and "전체 일치" not in search_mode:
            try:
                self.highlight_pattern = re.compile(re.escape(search_text), re.IGNORECASE)
            except re.error:
                self.highlight_pattern = None
        else:
            self.highlight_pattern = None

        # 실제 데이터의 열 개수 확인 (Fallback 대응)
        col_count = len(matches[0]) if matches else 2

        # 모드에 따른 헤더 구성 (문자열 포함 여부 및 실제 데이터 열 개수로 판단)
        if col_count >= 3:
            if "JSON" in search_mode:
                self._headers = [AppStrings.HEADER_POSITION, AppStrings.HEADER_JSON_KEY, AppStrings.HEADER_JSON_VALUE]
            elif "XML" in search_mode:
                self._headers = [AppStrings.HEADER_POSITION, AppStrings.HEADER_XML_NAME, AppStrings.HEADER_XML_VALUE]
            else:
                self._headers = [AppStrings.HEADER_POSITION, AppStrings.HEADER_CONTENT]
        else:
            # 데이터가 2열인 경우(텍스트 검색 또는 Fallback)
            self._headers = [AppStrings.HEADER_POSITION, AppStrings.HEADER_CONTENT]

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
            try:
                return int(self._data[row][0])
            except (ValueError, TypeError):
                return 1
        return 1
