import bisect
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

EXCEL_EXTENSIONS = (".xlsx", ".xlsm", ".xls", ".xlsb", ".ods")
EXCEL_CELL_PATTERN = re.compile(r"^[A-Za-z]+[0-9]+$")
NOTICE_POSITIONS = {"-1", "-2"}


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
    # 여러 파일을 합쳐 볼 때 이 결과가 나온 파일입니다(7.1.24). 칸 번호 접근(__getitem__)에는 들어가지 않습니다.
    source_path: Optional[str] = None

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


NOTE_COLOR = "#E8A33D"


def _note_patterns():
    """건너뛴 파일 사유(긴 문장)를 기타 칸의 짧은 문구로 바꾸는 규칙입니다. 언어마다 문구가 달라 그때 만듭니다."""
    pairs = (
        (AppStrings.SKIP_REASON_FILE_MATCH_LIMIT, AppStrings.FILE_NOTE_MATCH_LIMIT),
        (AppStrings.SKIP_REASON_DECODE_PARTIAL, AppStrings.FILE_NOTE_DECODE_PARTIAL),
        (AppStrings.SKIP_REASON_JSON_DEPTH_LIMIT, AppStrings.FILE_NOTE_JSON_DEPTH_LIMIT),
        (AppStrings.SKIP_REASON_EXCEL_PARTIAL, AppStrings.FILE_NOTE_EXCEL_PARTIAL),
    )
    patterns = []
    for template, short in pairs:
        regex = re.escape(template).replace(re.escape("{}"), "(.*?)")
        patterns.append((re.compile(regex + r"\Z", re.DOTALL), short))
    return patterns


def short_file_note(reason: str) -> str:
    """파일 목록 기타 칸에 보일 짧은 문구입니다. 모르는 사유는 앞의 [안내] 같은 꼬리표만 떼고 그대로 씁니다."""
    patterns = _note_patterns()
    parts = []
    for line in str(reason or "").splitlines():
        line = line.strip()
        if not line:
            continue
        for regex, short in patterns:
            match = regex.match(line)
            if match:
                parts.append(short.format(*match.groups()))
                break
        else:
            parts.append(re.sub(r"^\[[^\]]*\]\s*", "", line))
    return " · ".join(dict.fromkeys(parts))


class SearchResultModel(QAbstractTableModel):
    """
    파일 검색 결과(파일 경로, 매칭 횟수)를 관리하는 고성능 모델입니다.
    수백만 개의 행이 있어도 화면에 보이는 부분만 처리하여 성능을 유지합니다.
    """

    sort_completed = Signal()

    def __init__(self, icon_provider=None):
        """모델을 초기화하고 검색 엔진의 결과 항목 헤더를 설정합니다."""
        super().__init__()
        self.headers = [AppStrings.HEADER_COUNT, AppStrings.HEADER_FILE, AppStrings.HEADER_FOLDER, AppStrings.HEADER_NOTE]
        # 일부만 검색한 파일의 사유입니다(경로 → 사유 원문). 기타 칸에 짧게 보입니다(7.1.24).
        self._notes: Dict[str, str] = {}
        self._short_notes: Dict[str, str] = {}
        self._data = []
        self.icon_provider = icon_provider
        self.filename_filters = []
        self.highlight_pattern = None
        self._result_buffer = []
        self._filtered_buffer = []  # 필터링된 데이터
        self._current_page = 1
        self._page_size = 1000  # 기본 페이지 크기를 1000건으로 설정합니다.
        self._pagination_enabled = True
        self._is_sorting = False
        self._sort_worker = None
        self._data_revision = 0
        self._sort_request = 0
        self._active_sort = None
        self._pending_sort = None
        self._sort_workers = {}
        self._file_filter = ""
        self._folder_filter = ""
        self.search_mode = Constants.MODE_NORMAL

    class SortWorkerSignals(QObject):
        finished = Signal(int, int, object)

    class SortWorker(QRunnable):
        def __init__(self, data, key, reverse, request, revision, callback, tie_key=None):
            super().__init__()
            self.setAutoDelete(True)
            self.data = data
            self.key = key
            self.tie_key = tie_key
            self.reverse = reverse
            self.request = request
            self.revision = revision
            self.callback = callback
            self.signals = SearchResultModel.SortWorkerSignals()
            self.signals.finished.connect(self.callback)

        def run(self):
            # 대규모 데이터 정렬 수행 (메모리 중복 방지를 위해 in-place 정렬 시도)
            try:
                if self.tie_key is not None:
                    # 먼저 경로순으로 놓고 안정 정렬하면, 값이 같은 행은 내림차순이어도 경로순으로 남습니다.
                    self.data.sort(key=self.tie_key)
                self.data.sort(key=self.key, reverse=self.reverse)
                self.signals.finished.emit(self.request, self.revision, self.data)
            except Exception as e:
                logger.error(AppStrings.ERROR_SORT_WORKER.format(e))
                self.signals.finished.emit(self.request, self.revision, None)

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        """비동기 방식으로 데이터를 정렬합니다."""
        self.sort_all(column, order)

    def sort_all(self, column, order=Qt.SortOrder.AscendingOrder):
        """전체 결과(모든 쪽)를 머리글 열 기준으로 정렬합니다. 숫자는 값으로, 글자는 대소문자 없이 비교합니다."""
        if column not in (0, 1, 2, 3):
            return
        # 검색 중에 누른 기준은 결과가 계속 들어와 바로 적용되지 않으므로 기억했다가,
        # 검색이 끝날 때의 정렬(sort_results)에 씁니다. 머리글 화살표와 같은 기준을 유지합니다.
        self._header_sort = (column, order)
        if column == 0 and order == Qt.SortOrder.DescendingOrder:
            # 기본 정렬(매치 수 많은 순, 같으면 경로순)과 같아야 합니다. 표를 만들 때 Qt가 이 기준으로
            # 한 번 부르므로, 여기서 다르게 정렬하면 검색마다 같은 건수 파일의 순서가 바뀝니다.
            self.sort_globally()
            return

        def key(row):
            if column == 3:
                return self.short_note_for(row[3] if len(row) > 3 else "").lower()
            value = row[column] if len(row) > column else None
            if column == 0:
                try:
                    return int(value or 0)
                except (TypeError, ValueError):
                    return 0
            return str(value or "").lower()

        def path_key(row):
            path = str(row[3]).lower() if len(row) > 3 else ""
            name = str(row[1]).lower() if len(row) > 1 else ""
            return (path, name)

        self._start_sort(key, order == Qt.SortOrder.DescendingOrder, tie_key=path_key)

    def _start_sort(self, key, reverse=False, tie_key=None):
        if not self._result_buffer:
            return
        if self._is_sorting:
            self._pending_sort = (key, reverse, tie_key)
            return
        self._sort_request += 1
        self._active_sort = self._sort_request
        self._is_sorting = True
        self._sort_worker = self.SortWorker(
            list(self._result_buffer), key, reverse, self._sort_request,
            self._data_revision, self._on_sort_finished, tie_key,
        )
        self._sort_workers[self._sort_request] = self._sort_worker
        QThreadPool.globalInstance().start(self._sort_worker)

    def _on_sort_finished(self, request, revision, sorted_data):
        self._sort_workers.pop(request, None)
        if request != self._active_sort:
            return
        self._active_sort = None
        self._sort_worker = None
        self._is_sorting = False
        if revision == self._data_revision and sorted_data is not None:
            self._result_buffer = sorted_data
            self._apply_filters(reset_page=False)
            self.sort_completed.emit()
        pending, self._pending_sort = self._pending_sort, None
        if pending is not None:
            self._start_sort(*pending)

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

        if not new_items:
            return

        self._result_buffer.extend(new_items)
        self._data_revision += 1

        # 새로운 항목들 중 필터에 부합하는 항목만 filtered_buffer에 추가
        for item in new_items:
            if self._matches_filter(item):
                self._filtered_buffer.append(item)

        # 초기 200건에 대해서만 실시간 정렬 (첫 화면 반응성 확보)
        if len(self._filtered_buffer) <= 200:
            def sort_count(item):
                value = item[0] if item else 0
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return 0

            self._filtered_buffer.sort(
                key=lambda x: (
                    -sort_count(x),
                    str(x[3]).lower() if len(x) > 3 and x[3] is not None else "",
                    str(x[1]).lower() if len(x) > 1 and x[1] is not None else "",
                )
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

    def _apply_filters(self, reset_page: bool = True):
        """전체 버퍼에서 필터를 적용하여 _filtered_buffer를 갱신합니다."""
        if not self._file_filter and not self._folder_filter:
            self._filtered_buffer = list(self._result_buffer)
        else:
            self._filtered_buffer = [item for item in self._result_buffer if self._matches_filter(item)]

        # 필터 적용 후 첫 페이지로 이동
        if reset_page:
            self._current_page = 1
        self.go_to_page(self._current_page)

    def sort_results(self):
        """전체 데이터를 전역 규칙에 따라 정렬합니다 (비동기)."""
        if not self._result_buffer:
            return
        header_sort = getattr(self, "_header_sort", None)
        if header_sort is not None:
            self.sort_all(*header_sort)
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
        if col == 3:
            path = row_data[3] if len(row_data) > 3 else ""
            if role == Qt.ItemDataRole.DisplayRole:
                short = self.short_note_for(path)
                return f"<span style='color: {NOTE_COLOR};'>⚠ {escape(short)}</span>" if short else ""
            if role == Qt.ItemDataRole.EditRole:
                return self._notes.get(path, "")
            if role == Qt.ItemDataRole.ToolTipRole:
                return self._notes.get(path) or None
            if role == Qt.ItemDataRole.TextAlignmentRole:
                return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            if role == Qt.ItemDataRole.UserRole:
                return row_data[3], row_data[4]
            return None
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

    def set_file_notes(self, notes):
        """일부만 검색한 파일의 사유(경로 → 사유 원문)를 정합니다."""
        notes = {str(path): str(reason) for path, reason in (notes or {}).items() if reason}
        if notes == self._notes:
            return
        self._notes = notes
        self._short_notes = {}
        if self._data:
            self.dataChanged.emit(self.index(0, 3), self.index(len(self._data) - 1, 3))

    def note_for(self, path) -> str:
        return self._notes.get(str(path or ""), "")

    def short_note_for(self, path) -> str:
        path = str(path or "")
        reason = self._notes.get(path)
        if not reason:
            return ""
        short = self._short_notes.get(path)
        if short is None:
            short = short_file_note(reason)
            self._short_notes[path] = short
        return short

    def get_all_results(self):
        """저장된 모든 결과 데이터를 반환합니다."""
        return self._result_buffer

    def clear(self):
        # Running workers keep their lifetime, but cannot publish into a new search.
        self._data_revision += 1
        self._active_sort = None
        self._pending_sort = None
        self._is_sorting = False
        self._sort_worker = None
        self._data = []
        self._result_buffer = []
        self._filtered_buffer = []
        self._current_page = 1
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


    def sort_globally(self):
        """전체 데이터를 매치 수 DESC, 경로 ASC 순으로 정렬합니다 (비동기)."""
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

        self._start_sort(global_key)


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
        self._segments: List[tuple] = []  # [(파일 경로, 안내 행을 뺀 원시 결과)]
        self._seg_starts: List[int] = []
        self._total = 0
        self._cache: Dict[int, SearchMatchSchema] = {}
        self._filtered_indices: Union[range, List[int]] = range(0)
        self.multi_file = False
        self._page_size = 100
        self._current_page = 1
        self.current_file_path = ""
        self.search_text = ""
        self.search_mode = Constants.MODE_NORMAL
        self.highlight_pattern: Optional[re.Pattern] = None
        # 강조는 검색과 같은 비교 기준(엔진)으로 찾습니다. 엔진이 없을 때만 highlight_pattern을 씁니다.
        self.highlight_query = ""
        self._filters: Dict[int, str] = {}

    @property
    def match_count(self) -> int:
        """전체 매치 항목의 총 개수를 반환합니다."""
        return self._total

    def rowCount(self, parent=QModelIndex()):
        return len(self._data)

    def columnCount(self, parent=QModelIndex()):
        # [UX] 검색 모드에 따라 컬럼 수 결정 (Normal은 1컬럼, 나머지는 헤더 수만큼)
        search_mode = str(self.search_mode or Constants.MODE_NORMAL)
        file_col = 1 if self.multi_file else 0
        if search_mode == Constants.MODE_NORMAL or search_mode == AppStrings.SPECIAL_SEARCH_OFF:
            return 1 + file_col
        return len(self._headers) + file_col

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._data)):
            return None
        row_data = self._data[index.row()]
        col = index.column()
        row_path = row_data.source_path or self.current_file_path or ""
        if role == Qt.ItemDataRole.UserRole:
            return row_path
        if self.multi_file:
            # 여러 파일을 합쳐 볼 때 맨 앞 칸은 파일 이름입니다. 복사·거르기용 값과 설명은 전체 경로입니다.
            if col == 0:
                if role == Qt.ItemDataRole.DisplayRole:
                    return escape(os.path.basename(row_path))
                if role in (Qt.ItemDataRole.EditRole, Qt.ItemDataRole.ToolTipRole):
                    return row_path
                if role == Qt.ItemDataRole.TextAlignmentRole:
                    return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                return None
            col -= 1

        # search_mode가 None일 경우를 대비한 안전 로직
        search_mode = str(self.search_mode or Constants.MODE_NORMAL)

        if role in [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole]:
            # [UX] 검색 모드가 Normal인 경우 한 줄로 표시
            search_mode = str(self.search_mode or Constants.MODE_NORMAL)
            if search_mode == Constants.MODE_NORMAL:
                is_excel = row_path.lower().endswith(EXCEL_EXTENSIONS)

                # EditRole: 필터링용 순수 텍스트
                if role == Qt.ItemDataRole.EditRole:
                    if is_excel:
                        sheet_or_pos = str(row_data.position or "").strip()
                        cell_or_value = str(row_data.content if row_data.content is not None else "").strip()
                        extra_val = row_data.extra_1

                        if "!" in sheet_or_pos:
                            location = sheet_or_pos
                            value_part = str(extra_val) if extra_val is not None else cell_or_value
                        else:
                            location = (
                                f"{sheet_or_pos}!{cell_or_value}" if cell_or_value else sheet_or_pos
                            )
                            value_part = str(extra_val) if extra_val is not None else ""

                        parts = [location]
                        if value_part:
                            parts.append(value_part)
                        return " | ".join(parts)
                    return f"{row_data.position} | {row_data.content}"

                # DisplayRole: 하이라이팅 포함
                if is_excel:
                    # 엑셀: 시트!셀 | [하이라이팅된 값]
                    value = row_data.extra_1 if row_data.extra_1 is not None else row_data.content
                    val = str(value if value is not None else "")
                    rendered_val = self._render_highlighted(val)

                    sheet_or_pos = str(row_data.position if row_data.position is not None else "").strip()
                    cell = str(row_data.content if row_data.content is not None else "").strip()
                    if "!" in sheet_or_pos:
                        location = sheet_or_pos
                    elif sheet_or_pos and cell:
                        location = f"{sheet_or_pos}!{cell}"
                    else:
                        location = sheet_or_pos or cell

                    if location:
                        return f"<html>{escape(location)} | {rendered_val}</html>"
                    return f"<html>{rendered_val}</html>"
                else:
                    # 텍스트: 줄 번호 | [하이라이팅된내용]
                    val = str(row_data.content if row_data.content is not None else "")
                    rendered_val = self._render_highlighted(val)
                    return f"<html>{escape(row_data.position)} | {rendered_val}</html>"

            # [UX] 특수 검색 모드: 각 컬럼별 데이터 반환
            if col >= len(self._headers):
                return None

            val_obj = row_data[col]
            if val_obj is None:
                return ""

            val = str(val_obj)

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
        return None

    def _highlight_spans(self, text: str, limit: int = 1000) -> List[tuple]:
        """강조할 (시작, 끝) 글자 구간입니다. 검색처럼 `strasse`→`Straße`, 분해형 한글도 강조합니다(검수 F-3)."""
        if self.highlight_query:
            from core.search_engine import HAS_RUST_ENGINE, find_key_match_spans

            if HAS_RUST_ENGINE:
                return find_key_match_spans(text, self.highlight_query, limit)
        if self.highlight_pattern:
            return [match.span() for _, match in zip(range(limit), self.highlight_pattern.finditer(text))]
        return []

    def _render_highlighted(self, val: str) -> str:
        """데이터에 하이라이팅 및 이스케이프를 적용하여 반환합니다."""
        if not self.search_text:
            return escape(val)

        # Bound HTML rendering for huge single-line files. Keep the complete
        # value in the model, but show only a context window in the detail view.
        display_val = val
        context_limit = 8192
        if len(display_val) > context_limit:
            try:
                first_spans = self._highlight_spans(display_val, 1)
                center = first_spans[0][0] if first_spans else 0
                start = max(0, center - context_limit // 2)
                end = min(len(display_val), start + context_limit)
                start = max(0, end - context_limit)
                display_val = ("..." if start else "") + display_val[start:end] + ("..." if end < len(val) else "")
            except Exception:
                display_val = display_val[:context_limit] + "..."

        binary_prefix = AppStrings.MSG_BINARY_FILE.rstrip("]")
        if binary_prefix in display_val or display_val.startswith(binary_prefix):
            return escape(display_val)

        search_mode = str(self.search_mode or Constants.MODE_NORMAL)
        if Constants.MODE_EXACT in search_mode:
            if display_val.strip().lower() == self.search_text.strip().lower():
                return f"<span style='color: #ff9900; font-weight: bold;'>{escape(display_val)}</span>"
        else:
            if self.highlight_query or self.highlight_pattern:
                try:
                    parts = []
                    last_end = 0
                    for start, end in self._highlight_spans(display_val):
                        parts.append(escape(display_val[last_end:start]))
                        parts.append(
                            f"<span style='color: #ff9900; font-weight: bold;'>{escape(display_val[start:end])}</span>"
                        )
                        last_end = end
                    parts.append(escape(display_val[last_end:]))
                    return "".join(parts)
                except Exception:
                    # 강조만 실패하면 원문을 이스케이프해 일반 텍스트로 표시합니다.
                    pass
        return escape(display_val)

    def set_page_size(self, size: int):
        """페이지당 행 수를 설정하고 데이터를 다시 로드합니다."""
        if size <= 0 or self._page_size == size:
            return
        self._page_size = size
        self.go_to_page(1)

    def get_total_pages(self) -> int:
        count = len(self._filtered_indices)
        if not count:
            return 1
        return (count + self._page_size - 1) // self._page_size

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
        end_idx = min(start_idx + self._page_size, len(self._filtered_indices))
        self._data = [self._item_at(i) for i in self._filtered_indices[start_idx:end_idx]]

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            mode = self.search_mode or Constants.MODE_NORMAL
            if self.multi_file:
                if section == 0:
                    return AppStrings.HEADER_FILE
                section -= 1
            if (mode == Constants.MODE_NORMAL or mode == AppStrings.SPECIAL_SEARCH_OFF) and section == 0:
                return AppStrings.HEADER_LIST
            if section < len(self._headers):
                return self._headers[section]
        return None

    def set_matches(self, file_path, matches, search_text="", search_mode=Constants.MODE_NORMAL):
        """파일 하나의 결과를 보여 줍니다."""
        self.set_files([(file_path, matches)], search_text, search_mode)

    def set_files(self, entries, search_text="", search_mode=Constants.MODE_NORMAL):
        """한 개 이상 파일의 결과를 이어서 보여 줍니다(7.1.24).

        결과는 미리 한꺼번에 다듬지 않고 보는 페이지의 행만 그때그때 정리합니다.
        파일당 상한·부분 검색 안내 행(-1/-2)은 파일 목록의 기타 칸에 보이므로 여기서는 뺍니다.
        """
        if search_mode == AppStrings.SPECIAL_SEARCH_OFF or not search_mode:
            search_mode = Constants.MODE_NORMAL

        # 모델 리셋 전 헤더 정보를 미리 갱신하여 UI 캐시가 올바르게 반영되도록 합니다.
        self.search_mode = search_mode
        if Constants.MODE_JSON in search_mode:
            self._headers = [AppStrings.HEADER_POSITION, AppStrings.HEADER_JSON_KEY, AppStrings.HEADER_JSON_VALUE]
        elif Constants.MODE_XML in search_mode:
            self._headers = [AppStrings.HEADER_POSITION, AppStrings.HEADER_XML_NAME, AppStrings.HEADER_XML_VALUE]
        elif Constants.MODE_ARCHIVE in search_mode:
            self._headers = [
                AppStrings.HEADER_LINE_NUMBER,
                AppStrings.HEADER_ARCHIVE_NAMESPACE,
                AppStrings.HEADER_ARCHIVE_KEY,
                AppStrings.HEADER_ARCHIVE_SOURCE,
                AppStrings.HEADER_ARCHIVE_TRANSLATION,
            ]
        elif Constants.MODE_EXCEL in search_mode:
            self._headers = [
                AppStrings.HEADER_EXCEL_SHEET,
                AppStrings.HEADER_EXCEL_CELL,
                AppStrings.HEADER_EXCEL_VALUE,
            ]
        else:
            self._headers = [AppStrings.HEADER_POSITION, AppStrings.HEADER_CONTENT]

        entries = [(str(path or ""), list(matches or [])) for path, matches in (entries or [])]
        self.current_file_path = entries[0][0] if entries else ""
        self.multi_file = len(entries) > 1
        self.search_text = search_text
        self._segments = []
        self._seg_starts = []
        self._cache = {}
        total = 0
        for path, matches in entries:
            visible = [m for m in matches if not (len(m) >= 2 and str(m[0]) in NOTICE_POSITIONS)]
            if not visible:
                continue
            self._seg_starts.append(total)
            self._segments.append((path, visible))
            total += len(visible)
        self._total = total
        self._data = []
        self._current_page = 1

        self.highlight_query = search_text if search_text and Constants.MODE_EXACT not in search_mode else ""
        if search_text and Constants.MODE_EXACT not in search_mode:
            try:
                self.highlight_pattern = re.compile(re.escape(search_text), re.IGNORECASE)
            except re.error:
                self.highlight_pattern = None
        else:
            self.highlight_pattern = None

        self._apply_filters()

        # [H-05 Fix] headerDataChanged 시그널을 명시적으로 발생시켜 QHeaderView 캐시를 강제 초기화.
        # 모드 전환 시(예: NORMAL→JSON) 컬럼 수가 즉시 반영되도록 보장.
        self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, max(0, self.columnCount() - 1))

    CACHE_LIMIT = 20000

    def _item_at(self, index: int, cache: bool = True) -> SearchMatchSchema:
        item = self._cache.get(index)
        if item is not None:
            return item
        seg = bisect.bisect_right(self._seg_starts, index) - 1
        path, matches = self._segments[seg]
        item = self._normalize_match(matches[index - self._seg_starts[seg]], path)
        if cache:
            if len(self._cache) >= self.CACHE_LIMIT:
                self._cache.clear()
            self._cache[index] = item
        return item

    @property
    def _all_data_buffer(self) -> List[SearchMatchSchema]:
        return [self._item_at(i, cache=False) for i in range(self._total)]

    @property
    def _filtered_buffer(self) -> List[SearchMatchSchema]:
        return [self._item_at(i, cache=False) for i in self._filtered_indices]

    def _normalize_match(self, m, file_path: str) -> SearchMatchSchema:
        """원시 결과 한 건을 화면용 행으로 바꿉니다. 보는 페이지의 행만 그때그때 부릅니다(7.1.24)."""
        item = self._normalize_match_raw(m, file_path)
        if item is None:
            # 모양을 알 수 없는 행도 빠뜨리지 않고 위치·내용으로 보여 줍니다.
            item = SearchMatchSchema(
                position=str(m[0]) if len(m) > 0 else "",
                content=str(m[1]) if len(m) > 1 and m[1] is not None else "",
            )
        item.source_path = file_path
        return item

    def _normalize_match_raw(self, m, file_path: str) -> Optional[SearchMatchSchema]:
        from core.search_engine import json_path_for_display

        search_mode = self.search_mode
        mode_upper = str(search_mode or "").upper()
        is_excel_file = file_path.lower().endswith(EXCEL_EXTENSIONS)
        excel_cell_pattern = EXCEL_CELL_PATTERN if is_excel_file else None
        # Rust/구버전 세션은 Excel 필드를 content 하나에 탭(또는 " | ")으로 묶을 수 있습니다.
        # UI 모델 진입 시 한 번만 분리하여 목록·필터·미리보기가 동일한 스키마를 사용하게 합니다.
        if is_excel_file and len(m) >= 2 and isinstance(m[1], str):
            packed = m[1]
            has_explicit_cell = (
                len(m) > 2
                and isinstance(m[2], str)
                and excel_cell_pattern is not None
                and excel_cell_pattern.fullmatch(m[2].strip()) is not None
            )
            separator = None
            if "\t" in packed:
                separator = "\t"
            elif not has_explicit_cell and " | " in packed:
                separator = " | "
            if separator:
                parts = packed.split(separator, 2)
                if len(parts) >= 2:
                    sheet_candidate = parts[0].strip()
                    cell_candidate = parts[1].strip()
                    if excel_cell_pattern and excel_cell_pattern.fullmatch(cell_candidate):
                        value_candidate = parts[2] if len(parts) == 3 else ""
                        pos = f"{sheet_candidate}!{cell_candidate}"
                        return (
                            SearchMatchSchema(
                                position=sheet_candidate,
                                content=cell_candidate,
                                extra_1=value_candidate,
                                offset=m[2] if len(m) > 2 and m[2] is not None else pos,
                                length=m[3] if len(m) > 3 else None,
                            )
                        )
                        return None
        # UE 현지화 archive: (Key 줄 번호, Namespace, Key, 원문, 번역문, [Offset, Length])
        # 상한 안내 행(-1/-2)은 JSON처럼 첫 데이터 열에 안내 문구를 둡니다.
        if Constants.MODE_ARCHIVE.upper() in mode_upper:
            if str(m[0]) in {"-1", "-2"}:
                return (
                    SearchMatchSchema(position=str(m[0]), content=str(m[1]), extra_1="", extra_2="",
                                      extra_3="")
                )
            elif len(m) >= 5:
                return (
                    SearchMatchSchema(
                        position="" if m[0] in (None, 0, "") else str(m[0]),
                        content=str(m[1] or ""),
                        extra_1=str(m[2] or ""),
                        extra_2=str(m[3] or ""),
                        extra_3=str(m[4] or ""),
                        offset=m[5] if len(m) > 5 else None,
                        length=m[6] if len(m) > 6 else None,
                    )
                )
            return None
        # Case 1: Excel 모드 (Line, Sheet, Cell, Val, [Offset, Length])
        if Constants.MODE_EXCEL.upper() in mode_upper and len(m) >= 4 and isinstance(m[1], str):
            s, c = str(m[1]).strip(), str(m[2]).strip()
            v = str(m[3]).strip() if m[3] is not None else ""
            off = m[4] if (len(m) > 4 and m[4] is not None) else f"{s}!{c}"
            return (
                SearchMatchSchema(
                    position=s, content=c, extra_1=v, offset=off, length=m[5] if len(m) > 5 else None
                )
            )
            return None
        # Case 2: XML/JSON 모드
        # 5-tuple: search_engine에서 이미 key/value 분리 (line, path, val, offset, length)
        # 4-tuple: Rust 엔진 원시값 — m[1]에 "key\tvalue" 형태 (분리 안됨)
        # 3-tuple: Python 파서 반환값 — (line, key, value) 형태
        elif Constants.MODE_XML.upper() in mode_upper or Constants.MODE_JSON.upper() in mode_upper:
            if len(m) >= 5:
                # 이미 분리된 5-tuple
                return (
                    SearchMatchSchema(
                        position=str(m[0]),
                        content=str(m[1]),
                        extra_1=str(m[2]) if m[2] is not None else "",
                        offset=m[3],
                        length=m[4],
                    )
                )
            elif len(m) == 3:
                # Python XML/JSON 파서가 반환하는 (line, key, value) 3-tuple
                key_part = str(m[1])
                val_part = str(m[2]) if m[2] is not None else ""
                if Constants.MODE_XML.upper() in mode_upper:
                    key_part = key_part.lstrip("/").replace("/", " > ")
                else:
                    key_part = json_path_for_display(key_part)
                return (
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
                    key_part = json_path_for_display(key_part)
                return (
                    SearchMatchSchema(
                        position=str(m[0]),
                        content=key_part,
                        extra_1=val_part,
                        offset=m[2] if len(m) > 2 else None,
                        length=m[3] if len(m) > 3 else None,
                    )
                )
            return None
        # 일반 텍스트 및 기타 특수 검색 (Line, Content, [Offset, Length])
        # 엑셀 파일임에도 Normal 모드인 경우의 예외 처리 포함
        if is_excel_file and search_mode == Constants.MODE_NORMAL and len(m) >= 4 and isinstance(m[1], str):
            sheet_candidate = str(m[1]).strip()
            cell_candidate = str(m[2]).strip()
            if excel_cell_pattern and excel_cell_pattern.fullmatch(cell_candidate):
                value_candidate = str(m[3]) if len(m) > 3 and m[3] is not None else ""
                pos = f"{sheet_candidate}!{cell_candidate}"
                return (
                    SearchMatchSchema(
                        position=sheet_candidate,
                        content=cell_candidate,
                        extra_1=value_candidate,
                        offset=pos,
                    )
                )
                return None
        # 기본 파싱 (Line, Content, [Offset, Length])
        line_no = str(m[0])
        content = str(m[1]) if m[1] is not None else ""
        offset = m[2] if (len(m) > 2 and m[2] is not None) else None
        length = m[3] if (len(m) > 3 and m[3] is not None) else None

        # 엑셀 폴백 파싱 (Line, Pos, Val)
        if is_excel_file and len(m) == 3 and isinstance(m[2], str):
            pos = str(m[1])
            content_val = str(m[2])

            if "!" in pos:
                s, c = pos.split("!", 1)
                return (
                    SearchMatchSchema(position=s, content=c, extra_1=content_val, offset=pos)
                )
            elif " | " in pos:
                parts = pos.split(" | ")
                s = parts[0]
                c = parts[1] if len(parts) > 1 else ""
                return (
                    SearchMatchSchema(position=s, content=c, extra_1=content_val, offset=pos)
                )
            else:
                return (
                    SearchMatchSchema(position=pos, content="", extra_1=content_val, offset=pos)
                )
            return None
        return (
            SearchMatchSchema(position=line_no, content=content, offset=offset, length=length)
        )
        return None

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
                data = f"{item.position} | {item.content} | {item.extra_1 or ''}".lower()
            else:
                # 특수 모드는 지정된 인덱스의 필드에서 검색
                try:
                    data = str(item[col]).lower()
                except (IndexError, AttributeError):
                    data = ""
            
            if f_text not in data:
                return False
        return True

    def _compute_filtered_indices(self):
        if not self._filters:
            return range(self._total)
        # 거르기는 전체를 봐야 하므로 이때만 모든 행을 정리합니다(캐시에 쌓지 않음).
        is_normal = str(self.search_mode or Constants.MODE_NORMAL) == Constants.MODE_NORMAL
        return [i for i in range(self._total) if self._check_item_match(self._item_at(i, cache=False), is_normal)]

    def _apply_filters(self):
        """거르기 조건에 맞는 행 번호를 정합니다."""
        self._filtered_indices = self._compute_filtered_indices()
        self._current_page = 1
        self.go_to_page(1)

    def add_results(self, results):
        """지금 보는 파일에 결과를 덧붙입니다."""
        if not results:
            return
        visible = [
            m for m in results
            if isinstance(m, SearchMatchSchema) or not (len(m) >= 2 and str(m[0]) in NOTICE_POSITIONS)
        ]
        if not visible:
            return
        self._seg_starts.append(self._total)
        self._segments.append((self.current_file_path, visible))
        self._total += len(visible)
        self.layoutAboutToBeChanged.emit()
        self._filtered_indices = self._compute_filtered_indices()
        self._load_current_page_data_no_reset()
        self.layoutChanged.emit()

    def clear(self):
        """데이터를 초기화합니다."""
        self.beginResetModel()
        self._data = []
        self._segments = []
        self._seg_starts = []
        self._total = 0
        self._cache = {}
        self._filtered_indices = range(0)
        self.multi_file = False
        self.current_file_path = ""
        self._current_page = 1
        self.endResetModel()

    def get_match(self, row):
        """특정 행의 매치 데이터를 반환합니다."""
        if 0 <= row < len(self._data):
            return self._data[row]
        return None
