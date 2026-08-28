import logging
import os
import re
from dataclasses import dataclass
from html import escape
from typing import Dict, List, Optional, Union

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, QRunnable, Qt, QThreadPool, Signal

from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants

logger = logging.getLogger(__name__)


@dataclass
class SearchMatchSchema:
    """검색 결과의 단일 매치 데이터를 정의하는 스키마입니다."""

    position: str
    content: str  # 일반 텍스트 내용 또는 XML/JSON 경로 등
    extra_1: Optional[str] = None  # XML/JSON 값 등
    extra_2: Optional[str] = None  # 추가 구조화 데이터 등
    extra_3: Optional[str] = None  # 추가 구조화 데이터
    extra_4: Optional[str] = None  # 추가 구조화 데이터
    offset: Optional[Union[int, str]] = None
    length: Optional[int] = None

    def __getitem__(self, item):
        """기존 인덱스 기반 접근(컬럼 매핑) 호환성을 위한 메서드입니다."""
        # 모든 필드를 포함하며, 리스트의 인덱싱 기능을 활용해 음수 인덱스 지원
        fields = [
            self.position,
            self.content,
            self.extra_1,
            self.extra_2,
            self.extra_3,
            self.extra_4,
            self.offset,
            self.length,
        ]
        return fields[item]

    def __len__(self):
        return 8


class SearchResultModel(QAbstractTableModel):
    """
    파일 검색 결과(파일 경로, 매칭 횟수)를 관리하는 고성능 모델입니다.
    수백만 개의 행이 있어도 화면에 보이는 부분만 처리하여 성능을 유지합니다.
    """

    sort_completed = Signal()
    limit_reached = Signal(int)

    def __init__(self, icon_provider=None):
        """모델을 초기화하고 검색 엔진의 결과 항목 헤더를 설정합니다."""
        super().__init__()
        self.headers = [AppStrings.HEADER_COUNT, AppStrings.HEADER_FILE, AppStrings.HEADER_FOLDER]
        self._data = []
        self.icon_provider = icon_provider
        self.filename_filters = []
        self.highlight_pattern = None
        self._result_buffer = []
        self._filtered_buffer = []  # 필터링된 데이터
        self._limit_signal_sent = False
        self._current_page = 1
        self.has_truncated_results = False  # 매치 상한에 도달한 파일이 있는지 여부를 저장합니다.
        self._page_size = 1000  # 기본 페이지 크기를 1000건으로 설정합니다.
        self._pagination_enabled = True
        self._is_sorting = False
        self._file_filter = ""
        self._folder_filter = ""
        self.search_mode = Constants.MODE_NORMAL

    class SortWorkerSignals(QObject):
        finished = Signal(list)

    class SortWorker(QRunnable):
        def __init__(self, data, column, reverse, callback):
            super().__init__()
            self.setAutoDelete(False)  # signals 소멸 방지 (autoDelete 시 소멸 레이스 차단)
            self.data = data
            self.column = column
            self.reverse = reverse
            self.callback = callback
            self.signals = SearchResultModel.SortWorkerSignals()
            self.signals.finished.connect(self.callback)

        def run(self):
            # 대규모 데이터 정렬 수행 (메모리 중복 방지를 위해 in-place 정렬 시도)
            try:
                self.data.sort(key=lambda x: x[self.column], reverse=self.reverse)
                self.signals.finished.emit(self.data)
            except Exception as e:
                logger.error(AppStrings.ERROR_SORT_WORKER.format(e))
                self.signals.finished.emit([])

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        """비동기 방식으로 데이터를 정렬합니다."""
        if not self._result_buffer or self._is_sorting:
            return
        self._is_sorting = True
        self.layoutAboutToBeChanged.emit()
        reverse = order == Qt.SortOrder.DescendingOrder
        # 정렬 워커 생성 및 실행
        worker = self.SortWorker(list(self._result_buffer), column, reverse, self._on_sort_finished)
        QThreadPool.globalInstance().start(worker)

    def _on_sort_finished(self, sorted_data):
        self._result_buffer = sorted_data
        self._is_sorting = False
        self.go_to_page(self._current_page)
        self.layoutChanged.emit()
        self.sort_completed.emit()

    def add_results(self, results: list):
        """배치 단위로 결과를 일괄 추가하여 UI 갱신 빈도를 줄입니다."""
        if not results:
            return

        new_items = []
        for item in results:
            item_data = None
            # 이미 정규화된 형식 [count, name, folder, path, matches] 인 경우
            if len(item) == 5 and isinstance(item[0], int):
                item_data = list(item)
            # 워커에서 보낸 중간 형식 (path, count, matches) 인 경우
            elif len(item) == 3:
                file_path, count, matches = item[0], item[1], item[2]
                folder = os.path.dirname(file_path)
                file_name = os.path.basename(file_path)
                item_data = [count, file_name, folder, file_path, matches]
            # Rust 엔진의 원시 형식 (path, matches) 인 경우
            elif len(item) == 2:
                file_path, matches = item[0], item[1]
                count = len(matches)
                folder = os.path.dirname(file_path)
                file_name = os.path.basename(file_path)
                item_data = [count, file_name, folder, file_path, matches]
            
            if item_data:
                new_items.append(item_data)
                # 개별 파일의 매치 수 상한(-1 마커) 도달 여부를 확인합니다.
                matches_list = item_data[4]
                if any(str(m[0]) == "-1" for m in matches_list):
                    self.has_truncated_results = True

        if not new_items:
            return

        if len(self._result_buffer) + len(new_items) > 100_000:
            if not self._limit_signal_sent:
                self.limit_reached.emit(100_000)
                self._limit_signal_sent = True
            
            limit = 100_000 - len(self._result_buffer)
            if limit > 0:
                new_items = new_items[:limit]
            else:
                return

        self._result_buffer.extend(new_items)

        # 새로운 항목들 중 필터에 부합하는 항목만 filtered_buffer에 추가
        for item in new_items:
            if self._matches_filter(item):
                self._filtered_buffer.append(item)

        # 초기 200건에 대해서만 실시간 정렬 (첫 화면 반응성 확보)
        if len(self._filtered_buffer) <= 200:
            self._filtered_buffer.sort(
                key=lambda x: (-x[0], x[3].lower(), x[1].lower() if isinstance(x[1], str) else "")
            )

        # go_to_page는 내부적으로 beginResetModel/endResetModel을 호출하므로
        # 별도의 layoutChanged.emit()은 불필요합니다.
        self.go_to_page(self._current_page)

    def _matches_filter(self, item) -> bool:
        """단일 항목이 현재 필터 조건을 만족하는지 확인합니다."""
        if not self._file_filter and not self._folder_filter:
            return True
        file_name = str(item[1]).lower()
        folder_path = str(item[2]).lower()
        return (self._file_filter in file_name) and (self._folder_filter in folder_path)

    def set_filters(self, file_filter: str, folder_filter: str):
        """필터를 설정하고 전체 데이터를 다시 필터링합니다."""
        new_file = file_filter.lower()
        new_folder = folder_filter.lower()
        if self._file_filter == new_file and self._folder_filter == new_folder:
            return

        self._file_filter = new_file
        self._folder_filter = new_folder
        self._apply_filters()

    def _apply_filters(self):
        """전체 버퍼에서 필터를 적용하여 _filtered_buffer를 갱신합니다."""
        if not self._file_filter and not self._folder_filter:
            self._filtered_buffer = list(self._result_buffer)
        else:
            self._filtered_buffer = [item for item in self._result_buffer if self._matches_filter(item)]

        # 필터 적용 후 첫 페이지로 이동
        self._current_page = 1
        self.go_to_page(1)

    def sort_results(self):
        """전체 데이터를 전역 규칙에 따라 정렬합니다 (비동기)."""
        if not self._result_buffer or self._is_sorting:
            return
        # 동기 정렬 대신 성능을 위해 비동기 전역 정렬 메서드를 호출합니다.
        self.sort_globally()

    def rowCount(self, parent=QModelIndex()):
        """현재 로드된 행의 수입니다."""
        return len(self._data)

    def columnCount(self, parent=QModelIndex()):
        """항목 컬럼 수 (매치 수, 파일, 폴더)입니다."""
        return len(self.headers)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        """요청된 인덱스의 데이터를 반환합니다."""
        if not index.isValid() or not (0 <= index.row() < len(self._data)):
            return None
        row_data = self._data[index.row()]
        col = index.column()
        if role in [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole]:
            val = row_data[col]
            # 파일명(col=1) 하이라이팅 처리
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
        elif role == Qt.ItemDataRole.UserRole:
            return row_data[3], row_data[4]  # 경로, 매치 상세
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        """헤더 텍스트를 반환합니다."""
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            # SearchResultModel은 항상 다중 컬럼 헤더를 유지합니다.
            if section < len(self.headers):
                return self.headers[section]
        return None

    def get_all_results(self):
        """저장된 모든 결과 데이터를 반환합니다."""
        return self._result_buffer

    def clear(self):
        self._data = []
        self._result_buffer = []
        self._filtered_buffer = []
        self._current_page = 1
        self._limit_signal_sent = False
        self.has_truncated_results = False  # 상태를 초기화합니다.
        self._apply_filters()  # 내부적으로 리셋 신호 발생

    def get_total_pages(self) -> int:
        """전체 페이지 수를 계산하여 반환합니다."""
        if not self._pagination_enabled or not self._filtered_buffer:
            return 1
        return (len(self._filtered_buffer) + self._page_size - 1) // self._page_size

    def get_current_page(self) -> int:
        """현재 페이지 번호를 반환합니다."""
        return self._current_page

    def go_to_page(self, page_number: int):
        """지정된 페이지 번호로 이동합니다."""
        if not self._pagination_enabled:
            return
        total_pages = self.get_total_pages()
        if page_number < 1:
            page_number = 1
        elif page_number > total_pages:
            page_number = total_pages

        self._current_page = page_number
        self._load_current_page_data()

    def _load_current_page_data(self):
        """현재 페이지에 해당하는 데이터를 버퍼에서 슬라이싱하여 로드합니다."""
        self.beginResetModel()
        self._load_current_page_data_no_reset()
        self.endResetModel()

    def _load_current_page_data_no_reset(self):
        """reset 신호 없이 데이터만 로드합니다 (다른 reset 블록 내에서 사용용)."""
        start_idx = (self._current_page - 1) * self._page_size
        end_idx = min(start_idx + self._page_size, len(self._filtered_buffer))
        self._data = self._filtered_buffer[start_idx:end_idx]

    def set_page_size(self, size: int):
        """페이지 크기 변경 후 첫 페이지로 리셋합니다."""
        if size < 100:
            size = 100
        self._page_size = size
        self._current_page = 1
        self._load_current_page_data()

    def get_total_result_count(self) -> int:
        """전체 결과 수를 반환합니다 (버퍼에 있는 모든 결과)"""
        return len(self._result_buffer)

    def set_pagination_enabled(self, enabled):
        """페이지네이션 활성화/비활성화를 설정합니다"""
        self._pagination_enabled = enabled
        if not enabled:
            self._load_all_from_buffer()
        else:
            self._current_page = 1
            self._load_current_page_data()

    def sort_globally(self):
        """전체 데이터를 매치 수 DESC, 경로 ASC 순으로 정렬합니다 (비동기)."""
        if not self._result_buffer or self._is_sorting:
            return

        self._is_sorting = True
        self.layoutAboutToBeChanged.emit()

        # 전역 정렬 규칙: 매치 수(0번 컬럼) DESC, 경로(3번 컬럼) ASC
        def global_key(x):
            try:
                # x[0]이 None인 경우 0으로 처리, x[3]은 경로(str)
                count = x[0] if (len(x) > 0 and x[0] is not None) else 0
                path = str(x[3]).lower() if len(x) > 3 else ""
                name = str(x[1]).lower() if len(x) > 1 else ""
                return (-count, path, name)
            except (IndexError, AttributeError):
                return (0, "", "")

        class GlobalSortWorker(QRunnable):
            def __init__(self, data, key, callback):
                super().__init__()
                self.setAutoDelete(False)  # signals 소멸 방지 (autoDelete 시 소멸 레이스 차단)
                self.data = data
                self.key = key
                self.callback = callback
                self.signals = SearchResultModel.SortWorkerSignals()
                self.signals.finished.connect(self.callback)

            def run(self):
                try:
                    self.data.sort(key=self.key)
                    self.signals.finished.emit(self.data)
                except Exception as e:
                    logger.error(AppStrings.ERROR_GLOBAL_SORT_WORKER.format(e))
                    self.signals.finished.emit([])

        worker = GlobalSortWorker(list(self._result_buffer), global_key, self._on_sort_finished)
        QThreadPool.globalInstance().start(worker)

    def _load_all_from_buffer(self):
        """버퍼의 모든 결과를 즉시 로드합니다 (페이지네이션 비활성화 시)."""
        self.beginResetModel()
        self._data = list(self._filtered_buffer)
        self.endResetModel()

    def get_full_data(self, row):
        """특정 행의 전체 데이터(경로 및 매칭 리스트)를 반환합니다."""
        if 0 <= row < len(self._data):
            return self._data[row][3], self._data[row][4]
        return None, None

    def get_item(self, row):
        """특정 행의 (경로, 개수, 매칭 리스트) 튜플을 반환합니다."""
        if 0 <= row < len(self._data):
            d = self._data[row]
            return d[3], d[0], d[4]
        return None

    def set_filename_filters(self, filters):
        """하이라이팅을 위한 파일명 필터 목록을 설정합니다."""
        if self.filename_filters == filters:
            return
        self.filename_filters = filters if filters else []
        self.highlight_pattern = None
        if self.filename_filters:
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
        self._headers = [AppStrings.HEADER_POSITION, AppStrings.HEADER_CONTENT]
        self._data: List[SearchMatchSchema] = []
        self._all_data_buffer: List[SearchMatchSchema] = []
        self._page_size = 100
        self._current_page = 1
        self.current_file_path = ""
        self.search_text = ""
        self.search_mode = Constants.MODE_NORMAL
        self.highlight_pattern: Optional[re.Pattern] = None
        self._filters: Dict[int, str] = {}
        self._filtered_buffer: List[SearchMatchSchema] = []

    @property
    def match_count(self) -> int:
        """전체 매치 항목의 총 개수를 반환합니다."""
        return len(self._all_data_buffer)

    def rowCount(self, parent=QModelIndex()):
        return len(self._data)

    def columnCount(self, parent=QModelIndex()):
        # [UX] 검색 모드에 따라 컬럼 수 결정 (Normal은 1컬럼, 나머지는 헤더 수만큼)
        search_mode = str(self.search_mode or Constants.MODE_NORMAL)
        if search_mode == Constants.MODE_NORMAL or search_mode == AppStrings.SPECIAL_SEARCH_OFF:
            return 1
        return len(self._headers)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._data)):
            return None
        row_data = self._data[index.row()]
        col = index.column()

        # search_mode가 None일 경우를 대비한 안전 로직
        search_mode = str(self.search_mode or Constants.MODE_NORMAL)

        if role in [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole]:
            # [UX] 검색 모드가 Normal인 경우 한 줄로 표시
            search_mode = str(self.search_mode or Constants.MODE_NORMAL)
            if search_mode == Constants.MODE_NORMAL:
                is_excel = (self.current_file_path or "").lower().endswith((".xlsx", ".xlsm", ".xls", ".xlsb", ".ods"))

                # EditRole: 필터링용 순수 텍스트
                if role == Qt.ItemDataRole.EditRole:
                    if is_excel:
                        sheet_or_pos = str(row_data.position).strip()
                        cell_or_value = str(row_data.content).strip()
                        extra_val = row_data.extra_1

                        if "!" in sheet_or_pos:
                            location = sheet_or_pos
                            value_part = str(extra_val) if extra_val not in (None, "", "None") else cell_or_value
                        else:
                            location = (
                                f"{sheet_or_pos}!{cell_or_value}" if cell_or_value not in ("", "None") else sheet_or_pos
                            )
                            value_part = str(extra_val) if extra_val not in (None, "", "None") else ""

                        parts = [location]
                        if value_part not in ("", "None"):
                            parts.append(value_part)
                        return " | ".join(parts)
                    return f"{row_data.position} | {row_data.content}"

                # DisplayRole: 하이라이팅 포함
                if is_excel:
                    # 엑셀: [하이라이팅된값]
                    val = str(row_data.extra_1 if row_data.extra_1 is not None else row_data.content)
                    if val == "None":
                        val = ""  # 방어적 처리
                    rendered_val = self._render_highlighted(val)

                    pos = str(row_data.position).replace("None", "").strip()
                    sheet_info = str(row_data.content).replace("None", "").strip()
                    parts = [p for p in [pos, sheet_info] if p and p != "|"]
                    header = " | ".join(parts)

                    if header:
                        return f"<html>{escape(header)} | {rendered_val}</html>"
                    return f"<html>{rendered_val}</html>"
                else:
                    # 텍스트: 줄 번호 | [하이라이팅된내용]
                    val = str(row_data.content)
                    if row_data.position == "-1":
                        # 상한 도달 메시지는 굵은 빨간색으로 강조 표시합니다.
                        return f"<html><span style='color: #d9534f; font-weight: bold;'>⚠️ {escape(val)}</span></html>"
                        
                    if val == "None":
                        val = ""
                    rendered_val = self._render_highlighted(val)
                    return f"<html>{escape(row_data.position)} | {rendered_val}</html>"

            # [UX] 특수 검색 모드: 각 컬럼별 데이터 반환
            if col >= len(self._headers):
                return None

            val_obj = row_data[col]
            if val_obj is None:
                return ""

            val = str(val_obj)
            if val == "None":  # 문자열 "None"도 빈 값으로 처리
                return ""

            if role == Qt.ItemDataRole.EditRole:
                return val

            # 하이라이트 로직 (첫 번째 컬럼인 '위치/라인번호' 제외하고 모두 적용)
            if self.search_text and col > 0:
                return self._render_highlighted(val)
            return escape(val)
        elif role == Qt.ItemDataRole.TextAlignmentRole:
            if col == 0:
                return Qt.AlignmentFlag.AlignCenter
            return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        elif role == Qt.ItemDataRole.UserRole:
            return self.current_file_path
        return None

    def _render_highlighted(self, val: str) -> str:
        """데이터에 하이라이팅 및 이스케이프를 적용하여 반환합니다."""
        if not self.search_text:
            return escape(val)

        binary_prefix = AppStrings.MSG_BINARY_FILE.rstrip("]")
        if binary_prefix in val or val.startswith(binary_prefix):
            return escape(val)

        search_mode = str(self.search_mode or Constants.MODE_NORMAL)
        if Constants.MODE_EXACT in search_mode:
            if val.strip().lower() == self.search_text.strip().lower():
                return f"<span style='color: #ff9900; font-weight: bold;'>{escape(val)}</span>"
        else:
            if self.highlight_pattern:
                try:
                    parts = []
                    last_end = 0
                    for match in self.highlight_pattern.finditer(val):
                        parts.append(escape(val[last_end : match.start()]))
                        parts.append(f"<span style='color: #ff9900; font-weight: bold;'>{escape(match.group())}</span>")
                        last_end = match.end()
                    parts.append(escape(val[last_end:]))
                    return "".join(parts)
                except Exception:
                    pass
        return escape(val)

    def set_page_size(self, size: int):
        """페이지당 행 수를 설정하고 데이터를 다시 로드합니다."""
        if size <= 0 or self._page_size == size:
            return
        self._page_size = size
        self.go_to_page(1)

    def get_total_pages(self) -> int:
        if not self._filtered_buffer:
            return 1
        return (len(self._filtered_buffer) + self._page_size - 1) // self._page_size

    def go_to_page(self, page_number: int):
        total_pages = self.get_total_pages()
        if page_number < 1:
            page_number = 1
        elif page_number > total_pages:
            page_number = total_pages

        self._current_page = page_number
        self._load_current_page_data()

    def _load_current_page_data(self):
        self.beginResetModel()
        self._load_current_page_data_no_reset()
        self.endResetModel()

    def _load_current_page_data_no_reset(self):
        start_idx = (self._current_page - 1) * self._page_size
        end_idx = min(start_idx + self._page_size, len(self._filtered_buffer))
        self._data = self._filtered_buffer[start_idx:end_idx]

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            mode = self.search_mode or Constants.MODE_NORMAL
            if (mode == Constants.MODE_NORMAL or mode == AppStrings.SPECIAL_SEARCH_OFF) and section == 0:
                return AppStrings.HEADER_LIST
            if section < len(self._headers):
                return self._headers[section]
        return None

    def set_matches(self, file_path, matches, search_text="", search_mode=Constants.MODE_NORMAL):
        """검색 모드에 따라 컬럼 수와 헤더를 조정하고 데이터를 설정합니다."""
        if search_mode == AppStrings.SPECIAL_SEARCH_OFF or not search_mode:
            search_mode = Constants.MODE_NORMAL

        # 모델 리셋 전 헤더 정보를 미리 갱신하여 UI 캐시가 올바르게 반영되도록 합니다.
        self.search_mode = search_mode
        if Constants.MODE_JSON in search_mode:
            self._headers = [AppStrings.HEADER_POSITION, AppStrings.HEADER_JSON_KEY, AppStrings.HEADER_JSON_VALUE]
        elif Constants.MODE_XML in search_mode:
            self._headers = [AppStrings.HEADER_POSITION, AppStrings.HEADER_XML_NAME, AppStrings.HEADER_XML_VALUE]
        elif Constants.MODE_EXCEL in search_mode:
            self._headers = [
                AppStrings.HEADER_EXCEL_SHEET,
                AppStrings.HEADER_EXCEL_CELL,
                AppStrings.HEADER_EXCEL_VALUE,
            ]
        else:
            self._headers = [AppStrings.HEADER_POSITION, AppStrings.HEADER_CONTENT]

        self.current_file_path = file_path
        self.search_text = search_text
        self._all_data_buffer = []
        self._data = []
        self._current_page = 1

        if search_text and Constants.MODE_EXACT not in search_mode:
            try:
                self.highlight_pattern = re.compile(re.escape(search_text), re.IGNORECASE)
            except re.error:
                self.highlight_pattern = None
        else:
            self.highlight_pattern = None

        is_excel_file = file_path.lower().endswith((".xlsx", ".xlsm", ".xls", ".xlsb", ".ods"))
        excel_cell_pattern = re.compile(r"^[A-Za-z]+[0-9]+$") if is_excel_file else None
        normalized_matches: List[SearchMatchSchema] = []

        if matches:
            mode_upper = str(search_mode or "").upper()
            for m in matches:
                # Case 1: Excel 모드 (Line, Sheet, Cell, Val, [Offset, Length])
                if Constants.MODE_EXCEL.upper() in mode_upper and len(m) >= 4 and isinstance(m[1], str):
                    s, c, v = str(m[1]).strip(), str(m[2]).strip(), str(m[3]).strip()
                    off = m[4] if (len(m) > 4 and m[4] is not None) else f"{s}!{c}"
                    normalized_matches.append(
                        SearchMatchSchema(
                            position=s, content=c, extra_1=v, offset=off, length=m[5] if len(m) > 5 else None
                        )
                    )
                    continue

                # Case 2: XML/JSON 모드
                # 5-tuple: search_engine에서 이미 key/value 분리 (line, path, val, offset, length)
                # 4-tuple: Rust 엔진 원시값 — m[1]에 "key\tvalue" 형태 (분리 안됨)
                # 3-tuple: Python 파서 반환값 — (line, key, value) 형태
                elif Constants.MODE_XML.upper() in mode_upper or Constants.MODE_JSON.upper() in mode_upper:
                    if len(m) >= 5:
                        # 이미 분리된 5-tuple
                        normalized_matches.append(
                            SearchMatchSchema(
                                position=str(m[0]),
                                content=str(m[1]),
                                extra_1=str(m[2]),
                                offset=m[3],
                                length=m[4],
                            )
                        )
                    elif len(m) == 3:
                        # Python XML/JSON 파서가 반환하는 (line, key, value) 3-tuple
                        key_part = str(m[1])
                        val_part = str(m[2])
                        if Constants.MODE_XML.upper() in mode_upper:
                            key_part = key_part.lstrip("/").replace("/", " > ")
                        else:
                            key_part = key_part.lstrip("/").replace("/", ".")
                        normalized_matches.append(
                            SearchMatchSchema(
                                position=str(m[0]),
                                content=key_part,
                                extra_1=val_part,
                                offset=None,
                                length=None,
                            )
                        )
                    elif len(m) >= 4 and isinstance(m[1], str):
                        # Rust 엔진 원시 4-tuple: m[1] = "path\tvalue"
                        raw = str(m[1])
                        if "\t" in raw:
                            key_part, val_part = raw.split("\t", 1)
                        elif " | " in raw:
                            parts_raw = raw.split(" | ", 1)
                            key_part, val_part = parts_raw[0], parts_raw[1] if len(parts_raw) > 1 else ""
                        else:
                            key_part, val_part = raw, ""
                        # XML은 '/' 태그 경로 표기, JSON은 '.' 경로 표기
                        if Constants.MODE_XML.upper() in mode_upper:
                            key_part = key_part.lstrip("/").replace("/", " > ")
                        else:
                            key_part = key_part.lstrip("/").replace("/", ".")
                        normalized_matches.append(
                            SearchMatchSchema(
                                position=str(m[0]),
                                content=key_part,
                                extra_1=val_part,
                                offset=m[2] if len(m) > 2 else None,
                                length=m[3] if len(m) > 3 else None,
                            )
                        )
                    continue
                # 일반 텍스트 및 기타 특수 검색 (Line, Content, [Offset, Length])
                # 엑셀 파일임에도 Normal 모드인 경우의 예외 처리 포함
                if is_excel_file and search_mode == Constants.MODE_NORMAL and len(m) >= 4 and isinstance(m[1], str):
                    sheet_candidate = str(m[1]).strip()
                    cell_candidate = str(m[2]).strip()
                    if excel_cell_pattern and excel_cell_pattern.fullmatch(cell_candidate):
                        value_candidate = str(m[3]) if len(m) > 3 else ""
                        pos = f"{sheet_candidate}!{cell_candidate}"
                        normalized_matches.append(
                            SearchMatchSchema(
                                position=sheet_candidate,
                                content=cell_candidate,
                                extra_1=value_candidate,
                                offset=pos,
                            )
                        )
                        continue

                # 기본 파싱 (Line, Content, [Offset, Length])
                line_no = str(m[0])
                content = str(m[1])
                offset = m[2] if (len(m) > 2 and m[2] is not None) else None
                length = m[3] if (len(m) > 3 and m[3] is not None) else None

                # 엑셀 폴백 파싱 (Line, Pos, Val)
                if is_excel_file and len(m) == 3 and isinstance(m[2], str):
                    pos = str(m[1])
                    content_val = str(m[2])

                    if "!" in pos:
                        s, c = pos.split("!", 1)
                        normalized_matches.append(
                            SearchMatchSchema(position=s, content=c, extra_1=content_val, offset=pos)
                        )
                    elif " | " in pos:
                        parts = pos.split(" | ")
                        s = parts[0]
                        c = parts[1] if len(parts) > 1 else ""
                        normalized_matches.append(
                            SearchMatchSchema(position=s, content=c, extra_1=content_val, offset=pos)
                        )
                    else:
                        normalized_matches.append(
                            SearchMatchSchema(position=pos, content="", extra_1=content_val, offset=pos)
                        )
                    continue

                normalized_matches.append(
                    SearchMatchSchema(position=line_no, content=content, offset=offset, length=length)
                )

        self._all_data_buffer = normalized_matches
        self._apply_filters()

        # [H-05 Fix] headerDataChanged 시그널을 명시적으로 발생시켜 QHeaderView 캐시를 강제 초기화.
        # 모드 전환 시(예: NORMAL→JSON) 컬럼 수가 즉시 반영되도록 보장.
        from PySide6.QtCore import Qt
        self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, len(self._headers) - 1)

    def set_column_filter(self, column: int, text: str):
        """상세 목록의 특정 컬럼에 대한 필터를 설정합니다."""
        new_text = text.lower().strip()
        
        # [H-06 Fix] 인덱스 매핑 변경에 대응하기 위해, 
        # 값이 없는 경우라도 dict에서 제거하여 이전 모드의 찌꺼기 필터가 영향 주지 않게 함
        if not new_text:
            self._filters.pop(column, None)
        else:
            self._filters[column] = new_text
            
        self._apply_filters()

    def _check_item_match(self, item: SearchMatchSchema, is_normal: bool) -> bool:
        """단일 항목이 현재 모든 필터 조건을 만족하는지 확인합니다."""
        for col, f_text in self._filters.items():
            if is_normal:
                # Normal 모드는 '위치 | 내용' 전체에서 검색
                data = f"{item.position} | {item.content}".lower()
            else:
                # 특수 모드는 지정된 인덱스의 필드에서 검색
                try:
                    data = str(item[col]).lower()
                except (IndexError, AttributeError):
                    data = ""
            
            if f_text not in data:
                return False
        return True

    def _apply_filters(self):
        """전체 매치 히스토리에서 필터를 적용하여 _filtered_buffer를 생성합니다."""
        if not self._filters:
            self._filtered_buffer = list(self._all_data_buffer)
        else:
            cur_mode = str(self.search_mode or Constants.MODE_NORMAL)
            is_normal = cur_mode == Constants.MODE_NORMAL
            self._filtered_buffer = [
                item for item in self._all_data_buffer 
                if self._check_item_match(item, is_normal)
            ]

        self._current_page = 1
        self.go_to_page(1)

    def add_results(self, results):
        """도착 순서대로 결과를 누적합니다 (전역 정렬은 검색 종료 후 호출)."""
        if not results:
            return

        new_items = []
        for m in results:
            # SearchMatchSchema로 변환하여 저장
            if isinstance(m, SearchMatchSchema):
                new_items.append(m)
            else:
                # [Line, Content, ...] 형식의 튜플인 경우 파싱
                # set_matches와 유사한 파싱 로직 적용 가능하나 간단히 처리
                line_no = str(m[0])
                content = str(m[1])
                offset = m[2] if len(m) > 2 else None
                length = m[3] if len(m) > 3 else None
                new_items.append(SearchMatchSchema(position=line_no, content=content, offset=offset, length=length))

        self._all_data_buffer.extend(new_items)

        # 필터링 및 화면 갱신 (리셋 없이 데이터만 추가하는 레이아웃 변경 신호 사용)
        self.layoutAboutToBeChanged.emit()
        self._apply_filters_no_reset()
        self.layoutChanged.emit()

    def _apply_filters_no_reset(self):
        """beginResetModel 없이 필터를 적용하고 현재 페이지 데이터를 로드합니다."""
        if not self._filters:
            self._filtered_buffer = list(self._all_data_buffer)
        else:
            cur_mode = str(self.search_mode or Constants.MODE_NORMAL)
            is_normal = cur_mode == Constants.MODE_NORMAL
            self._filtered_buffer = [
                item for item in self._all_data_buffer 
                if self._check_item_match(item, is_normal)
            ]
        self._load_current_page_data_no_reset()

    def clear(self):
        """데이터를 초기화합니다."""
        self.beginResetModel()
        self._data = []
        self._all_data_buffer = []
        self._filtered_buffer = []
        self.current_file_path = ""
        self._current_page = 1
        self.endResetModel()

    def get_line_no(self, row):
        """특정 행의 실제 소스 코드 라인 번호를 반환합니다."""
        if 0 <= row < len(self._data):
            try:
                # position 필드가 라인 번호일 경우
                return int(self._data[row].position)
            except (ValueError, TypeError, AttributeError):
                return 1
        return 1

    def get_match(self, row):
        """특정 행의 매치 데이터를 반환합니다."""
        if 0 <= row < len(self._data):
            return self._data[row]
        return None

    def get_match_info(self, row):
        """오프셋과 길이 정보를 반환합니다."""
        if 0 <= row < len(self._data):
            d = self._data[row]
            return d.offset, d.length
        return None, None
