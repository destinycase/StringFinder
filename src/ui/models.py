from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex
import os
import re
from html import escape
from sf_utils.constants import Constants


class SearchResultModel(QAbstractTableModel):
    """
    파일 검색 결과(파일 경로, 매칭 횟수)를 관리하는 고성능 모델입니다.
    수백만 개의 행이 있어도 화면에 보이는 부분만 처리하여 성능을 유지합니다.
    """

    def __init__(self, icon_provider=None):
        """모델을 초기화하고 검색 엔진의 결과 항목 헤더를 설정합니다."""
        super().__init__()
        from sf_utils.app_strings import AppStrings

        self.headers = [AppStrings.HEADER_COUNT, AppStrings.HEADER_FILE, AppStrings.HEADER_FOLDER]
        self._data = []
        self.icon_provider = icon_provider
        self.filename_filters = []
        self.highlight_pattern = None

        self._result_buffer = []
        self._page_size = 1000
        self._loaded_count = 0
        self._pagination_enabled = True

    def rowCount(self, parent=QModelIndex()):
        return len(self._data)

    def columnCount(self, parent=QModelIndex()):
        return len(self.headers)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._data)):
            return None

        row_data = self._data[index.row()]
        col = index.column()

        if role in [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole]:
            val = row_data[col]

            if role == Qt.ItemDataRole.DisplayRole and col == 1 and self.highlight_pattern:
                try:
                    parts = []
                    last_end = 0
                    val_str = str(val)
                    for match in self.highlight_pattern.finditer(val_str):
                        parts.append(escape(val_str[last_end : match.start()]))
                        parts.append(f"<span style='color: #ff9900; font-weight: bold;'>{escape(match.group())}</span>")
                        last_end = match.end()
                    parts.append(escape(val_str[last_end:]))
                    return "".join(parts)
                except Exception:
                    return escape(str(val))

            if role == Qt.ItemDataRole.DisplayRole and col == 1:
                return escape(str(val))

            return val

        elif role == Qt.ItemDataRole.TextAlignmentRole:
            if col == 0:
                return Qt.AlignmentFlag.AlignCenter
            return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter

        elif role == Qt.ItemDataRole.ToolTipRole:
            return row_data[3]

        elif role == Qt.ItemDataRole.UserRole:
            return row_data[3], row_data[4]

        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.headers[section]
        return None

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        """특정 열을 기준으로 데이터를 오름차순 또는 내림차순으로 정렬합니다."""
        self.layoutAboutToBeChanged.emit()
        reverse = order == Qt.SortOrder.DescendingOrder
        self._data.sort(key=lambda x: x[column], reverse=reverse)
        self.layoutChanged.emit()

    def clear(self):
        self.beginResetModel()
        self._data = []
        self._result_buffer = []
        self._loaded_count = 0
        self.endResetModel()

    def load_next_page(self):
        """다음 페이지를 로드합니다."""
        if not self._pagination_enabled or self._loaded_count >= len(self._result_buffer):
            return

        start_idx = self._loaded_count
        end_idx = min(start_idx + self._page_size, len(self._result_buffer))
        next_page_data = self._result_buffer[start_idx:end_idx]

        if next_page_data:
            self.beginInsertRows(QModelIndex(), len(self._data), len(self._data) + len(next_page_data) - 1)
            self._data.extend(next_page_data)
            self._loaded_count = len(self._data)
            self.endInsertRows()

    def get_total_pages(self) -> int:
        """전체 페이지 수를 반환합니다 (1-based)"""
        if not self._pagination_enabled or not self._result_buffer:
            return 1
        return (len(self._result_buffer) + self._page_size - 1) // self._page_size

    def get_current_page(self) -> int:
        """현재 페이지 번호를 반환합니다 (1-based)"""
        if not self._pagination_enabled or self._loaded_count == 0:
            return 1
        return (self._loaded_count - 1) // self._page_size + 1

    def has_more_results(self) -> bool:
        """로드할 결과가 더 있는지 확인합니다"""
        return self._loaded_count < len(self._result_buffer)

    def go_to_page(self, page_number: int):
        """
        특정 페이지로 이동 (1-based)

        속도 최적화 전략:
        - 앞으로 이동 (page_number < current_page):
          모델을 리셋하고 처음부터 요청한 페이지까지 로드
          이유: Qt 모델에서 중간 데이터 제거가 복잡하므로 리셋이 더 효율적

        - 뒤로 이동 (page_number > current_page):
          현재 로드된 데이터를 유지하고 필요한 페이지(만) 추가 로드
          이유: 이미 로드된 데이터를 재사용하여 성능 향상

        메모리 효율성:
        - 페이지네이션을 통해 대량 결과(10,000개 이상)를 효율적으로 처리
        - UI 끊김 발생 감소 (한 번에 최대 1000~5000개만 표시)
        """
        if page_number < 1:
            page_number = 1

        total_pages = self.get_total_pages()
        if page_number > total_pages:
            page_number = total_pages

        current_page = self.get_current_page()

        if page_number == current_page:
            return

        if page_number < current_page:
            self.beginResetModel()
            self._data.clear()
            self._loaded_count = 0
            self.endResetModel()

            for _ in range(page_number):
                self.load_next_page()
        else:
            pages_to_load = page_number - current_page
            for _ in range(pages_to_load):
                self.load_next_page()

    def set_page_size(self, size: int):
        """
        페이지 크기 변경
        설정 시 모델을 초기화하고 첫 페이지를 다시 로드합니다.
        """
        if size < 100:
            size = 100

        self._page_size = size

        if self._loaded_count > 0:
            self.beginResetModel()
            self._data.clear()
            self._loaded_count = 0
            self.endResetModel()

            self.load_next_page()

    def get_total_result_count(self) -> int:
        """전체 결과 수를 반환합니다 (버퍼에 있는 모든 결과)"""
        return len(self._result_buffer)

    def get_loaded_result_count(self) -> int:
        """현재 로드된 결과 수를 반환합니다"""
        return self._loaded_count

    def get_pagination_info(self):
        """페이지네이션 정보를 반환합니다 (로드된 수, 전체 수, 더 있는지 여부)"""
        return self._loaded_count, len(self._result_buffer), self.has_more_results()

    def set_pagination_enabled(self, enabled):
        """페이지네이션 활성화/비활성화를 설정합니다"""
        self._pagination_enabled = enabled

    def add_results(self, results):
        """여러 개의 검색 결과를 모델에 추가합니다. 페이지네이션이 활성화된 경우 버퍼에 저장합니다."""
        if not results:
            return

        for file_path, count, matches in results:
            folder = os.path.dirname(file_path)
            file_name_with_ext = os.path.basename(file_path)
            self._result_buffer.append([count, file_name_with_ext, folder, file_path, matches])

        if not self._pagination_enabled or len(self._result_buffer) <= self._page_size:
            self._load_all_from_buffer()
        else:
            if self._loaded_count == 0:
                self.load_next_page()

    def _load_all_from_buffer(self):
        """버퍼의 모든 결과를 즉시 로드합니다 (페이지네이션 비활성화 시)."""
        if not self._result_buffer:
            return

        self.beginInsertRows(QModelIndex(), len(self._data), len(self._data) + len(self._result_buffer) - 1)
        self._data.extend(self._result_buffer)
        self._loaded_count = len(self._data)
        self.endInsertRows()
        self._result_buffer = []

    def get_full_data(self, row):
        """특정 행의 전체 데이터(경로 및 매칭 리스트)를 반환합니다."""
        if 0 <= row < len(self._data):
            return self._data[row][3], self._data[row][4]
        return None, None

    def set_filename_filters(self, filters):
        """하이라이팅을 위한 파일명 필터 목록을 설정합니다."""
        self.filename_filters = filters if filters else []
        self.highlight_pattern = None

        if self.filename_filters:
            # ["npc", "id"] -> "(npc|id)" 형태의 정규식 생성
            try:
                patterns = [re.escape(f).strip() for f in self.filename_filters if f.strip()]
                if patterns:
                    combined_pattern = "|".join(patterns)
                    self.highlight_pattern = re.compile(combined_pattern, re.IGNORECASE)
            except re.error:
                pass

        if self._data:
            top_left = self.index(0, 1)
            bottom_right = self.index(len(self._data) - 1, 1)
            self.dataChanged.emit(top_left, bottom_right, [Qt.ItemDataRole.DisplayRole])


class MatchDetailModel(QAbstractTableModel):
    """
    특정 파일 내의 상세 매칭 정보(라인 번호, 내용)를 관리하는 모델입니다.
    """

    def __init__(self):
        """모델을 초기화하고 매칭 상세 정보(라인, 내용)의 헤더를 설정합니다."""
        super().__init__()
        from sf_utils.app_strings import AppStrings

        self._headers = [AppStrings.HEADER_POSITION, AppStrings.HEADER_CONTENT]
        self._data = []
        self.current_file_path = ""
        self.search_text = ""
        self.search_mode = "Normal"
        self.highlight_pattern = None

    def rowCount(self, parent=QModelIndex()):
        return len(self._data)

    def columnCount(self, parent=QModelIndex()):
        return len(self._headers)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._data)):
            return None

        row_data = self._data[index.row()]
        col = index.column()

        if role in [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole]:
            if col >= len(row_data):
                return ""

            # Excel이나 특수 모드(JSON/XML)는 row_data[0]이 더미이거나 라인 번호이므로
            # 고정된 컬럼 매핑 대신 헤더 인덱스를 존중하거나 특수 처리합니다.
            is_special = len(self._headers) > 2 or (
                self.current_file_path.lower().endswith((".xlsx", ".xlsm", ".xls", ".xlsb", ".ods"))
            )

            if is_special:
                # 엑셀의 경우 col 0: 위치(row_data[1]), col 1: 값(row_data[2])
                if self.current_file_path.lower().endswith((".xlsx", ".xlsm", ".xls", ".xlsb", ".ods")):
                    val = str(row_data[col + 1]) if col + 1 < len(row_data) else ""
                else:
                    # JSON/XML 등 (line, path, value...) -> col 0: line, col 1: path, col 2: value
                    val = str(row_data[col])
            else:
                val = str(row_data[col])

            if role == Qt.ItemDataRole.EditRole:
                return val

            # DisplayRole (Highlighting)
            if self.search_text and col > 0 and not val.startswith("[바이너리 파일"):
                if Constants.MODE_EXACT in self.search_mode:
                    if val.lower() == self.search_text.lower():
                        return f"<span style='color: #ff9900; font-weight: bold;'>{escape(val)}</span>"
                else:
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
            return escape(val)

        elif role == Qt.ItemDataRole.EditRole:
            if col >= len(row_data):
                return ""
            return str(row_data[col])

        elif role == Qt.ItemDataRole.TextAlignmentRole:
            if col == 0:
                return Qt.AlignmentFlag.AlignCenter
            return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter

        elif role == Qt.ItemDataRole.UserRole:
            return self.current_file_path

        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            if section < len(self._headers):
                return self._headers[section]
        return None

    def set_matches(self, file_path, matches, search_text="", search_mode="Normal"):
        """검색 모드에 따라 컬럼 수와 헤더를 조정하고 데이터를 설정합니다."""
        from sf_utils.app_strings import AppStrings

        self.beginResetModel()

        self.current_file_path = file_path
        self.search_text = search_text
        self.search_mode = search_mode

        if search_text and Constants.MODE_EXACT not in search_mode:
            try:
                self.highlight_pattern = re.compile(re.escape(search_text), re.IGNORECASE)
            except re.error:
                self.highlight_pattern = None
        else:
            self.highlight_pattern = None

        col_count = len(matches[0]) if matches else 2

        is_excel_file = file_path.lower().endswith((".xlsx", ".xlsm", ".xls", ".xlsb", ".ods"))

        if is_excel_file or col_count >= 3:
            if Constants.MODE_JSON in search_mode:
                self._headers = [AppStrings.HEADER_POSITION, AppStrings.HEADER_JSON_KEY, AppStrings.HEADER_JSON_VALUE]
            elif Constants.MODE_XML in search_mode:
                self._headers = [AppStrings.HEADER_POSITION, AppStrings.HEADER_XML_NAME, AppStrings.HEADER_XML_VALUE]
            elif Constants.MODE_ARCHIVE in search_mode:
                self._headers = [
                    AppStrings.HEADER_POSITION,
                    AppStrings.HEADER_ARCHIVE_NAMESPACE,
                    AppStrings.HEADER_ARCHIVE_KEY,
                    AppStrings.HEADER_ARCHIVE_SOURCE,
                    AppStrings.HEADER_ARCHIVE_TRANSLATION,
                ]
            elif is_excel_file or Constants.MODE_EXCEL in search_mode:
                self._headers = [AppStrings.HEADER_EXCEL_POSITION, AppStrings.HEADER_EXCEL_VALUE]
                if matches and len(matches[0]) >= 3 and not (len(matches[0]) == 4 and matches[0][2] is not None):
                    # Excel 결과는 (0, location, None, None) 형식이므로 필터링 필요
                    matches = [(0, m[1], m[2] if len(m) > 2 else "", None, None) for m in matches]
            else:
                self._headers = [AppStrings.HEADER_POSITION, AppStrings.HEADER_CONTENT]
        else:
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

    def get_match_info(self, row):
        """특정 행의 (offset, length) 정보를 반환합니다."""
        if 0 <= row < len(self._data):
            d = self._data[row]
            if len(d) >= 4:
                return d[-2], d[-1]
        return None, None
