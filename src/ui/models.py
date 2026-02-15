from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex
import os
import re
from html import escape
from utils.constants import Constants


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
        self.filename_filters = []  # 하이라이팅할 파일명 필터 목록
        self.highlight_pattern = None

        # [Feature] 페이지네이션 지원
        self._result_buffer = []  # 전체 결과를 저장하는 내부 버퍼
        self._page_size = 1000  # 한 번에 로드할 결과 수
        self._loaded_count = 0  # 현재 로드된 결과 수
        self._pagination_enabled = True  # 페이지네이션 활성화 여부

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
            val = row_data[col]

            # [Highlighting] 파일명 컬럼(1)이고 DisplayRole이며, 필터 패턴이 있는 경우
            if role == Qt.DisplayRole and col == 1 and self.highlight_pattern:
                # 텍스트 이스케이프 후 하이라이팅 적용
                try:
                    # 정규식으로 매칭된 부분 찾아서 강조 태그로 감싸기
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

            # HTML 델리게이트를 사용하므로 기본 텍스트도 이스케이프 처리 권장 (특수문자깨짐 방지)
            if role == Qt.DisplayRole and col == 1:
                return escape(str(val))

            return val

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
        self._result_buffer = []  # 버퍼도 초기화
        self._loaded_count = 0
        self.endResetModel()

    def load_next_page(self):
        """다음 페이지의 결과를 로드합니다. 더 이상 로드할 결과가 없으면 False를 반환합니다."""
        if self._loaded_count >= len(self._result_buffer):
            return False  # 더 이상 로드할 데이터 없음

        # 다음 페이지 범위 계산
        start_idx = self._loaded_count
        end_idx = min(start_idx + self._page_size, len(self._result_buffer))
        page_data = self._result_buffer[start_idx:end_idx]

        if not page_data:
            return False

        # 모델에 추가
        self.beginInsertRows(QModelIndex(), len(self._data), len(self._data) + len(page_data) - 1)
        self._data.extend(page_data)
        self._loaded_count = end_idx
        self.endInsertRows()

        return True  # 성공적으로 로드됨

    def has_more_results(self):
        """로드되지 않은 결과가 더 있는지 확인합니다."""
        return self._loaded_count < len(self._result_buffer)

    def get_pagination_info(self):
        """페이지네이션 정보를 반환합니다: (로드된 수, 전체 수, 더 있는지 여부)"""
        return self._loaded_count, len(self._result_buffer), self.has_more_results()

    def set_pagination_enabled(self, enabled):
        """페이지네이션 활성화/비활성화를 설정합니다."""
        self._pagination_enabled = enabled

    def add_results(self, results):
        """여러 개의 검색 결과를 모델에 추가합니다. 페이지네이션이 활성화된 경우 버퍼에 저장합니다."""
        if not results:
            return

        # 결과를 내부 버퍼에 추가
        for file_path, count, matches in results:
            folder = os.path.dirname(file_path)
            file_name_with_ext = os.path.basename(file_path)
            self._result_buffer.append([count, file_name_with_ext, folder, file_path, matches])

        # 페이지네이션이 비활성화되었거나 결과가 적으면 즉시 로드
        if not self._pagination_enabled or len(self._result_buffer) <= self._page_size:
            self._load_all_from_buffer()
        else:
            # 페이지네이션 활성화: 첫 페이지만 로드
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
            # 필터들을 OR 조건으로 묶은 정규식 생성 (특수문자 이스케이프 처리)
            # 예: ["npc", "id"] -> "(npc|id)"
            try:
                patterns = [re.escape(f).strip() for f in self.filename_filters if f.strip()]
                if patterns:
                    combined_pattern = "|".join(patterns)
                    self.highlight_pattern = re.compile(combined_pattern, re.IGNORECASE)
            except re.error:
                pass

        # 데이터 갱신 알림 (화면 다시 그리기)
        if self._data:
            # 전체 영역이 변경되었음을 알림 (컬럼 1만 해도 되지만 단순함을 위해 전체)
            top_left = self.index(0, 1)
            bottom_right = self.index(len(self._data) - 1, 1)
            self.dataChanged.emit(top_left, bottom_right, [Qt.DisplayRole])


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
                # "전체 일치" 또는 "Archive" 모드인지 확인
                # Archive는 내부적으로 부분 일치 검색을 수행하므로 강조를 적용할 수 있음
                if Constants.MODE_EXACT in self.search_mode:
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
        if search_text and Constants.MODE_EXACT not in search_mode:
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
