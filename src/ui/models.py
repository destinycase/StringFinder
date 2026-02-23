import logging
import os
import re
from dataclasses import dataclass
from html import escape
from typing import List, Optional, Union

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, QRunnable, Qt, QThreadPool, Signal

from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants

logger = logging.getLogger(__name__)


@dataclass
class SearchMatchSchema:
    """검색 결과의 단일 매치 데이터를 정의하는 스키마입니다."""

    position: str
    content: str  # 일반 텍스트 모드나 Excel 값
    extra_1: Optional[str] = None  # JSON/XML Key, Archive Namespace 등
    extra_2: Optional[str] = None  # JSON/XML Value, Archive Key 등
    extra_3: Optional[str] = None  # Archive Source
    extra_4: Optional[str] = None  # Archive Translation
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

    def __init__(self, icon_provider=None):
        """모델을 초기화하고 검색 엔진의 결과 항목 헤더를 설정합니다."""
        super().__init__()
        self.headers = [AppStrings.HEADER_COUNT, AppStrings.HEADER_FILE, AppStrings.HEADER_FOLDER]
        self._data = []
        self.icon_provider = icon_provider
        self.filename_filters = []
        self.highlight_pattern = None
        self._result_buffer = []
        self._page_size = 100
        self._current_page = 1
        self._pagination_enabled = True
        self._is_sorting = False
        self.search_mode = Constants.MODE_NORMAL

    class SortWorkerSignals(QObject):
        finished = Signal(list)

    class SortWorker(QRunnable):
        def __init__(self, data, column, reverse, callback):
            super().__init__()
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
                logger.error(f"SortWorker Error: {e}")
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

    def add_result(self, result: list):
        """결과를 버퍼에 추가합니다. (성능을 위해 실시간 정렬은 제한적으로 수행)"""
        self._result_buffer.append(result)
        # [Speed] 대량 검색 시 UI 프리징 방지를 위해 매 건마다 정렬하지 않고
        # 일정 주기(예: 1000건)마다 혹은 검색 종료 후에 한 번에 정렬하도록 유도합니다.
        # 현재는 UX를 위해 최소한의 데이터만 로드한 상태를 유지합니다.
        if len(self._result_buffer) <= 100:
             self._result_buffer.sort(key=lambda x: (-x[0], x[3].lower(), x[1].lower() if isinstance(x[1], str) else ""))

        self.go_to_page(self._current_page)
        self.layoutChanged.emit()

    def sort_results(self):
        """전체 데이터를 전역 규칙에 따라 정렬합니다."""
        if not self._result_buffer:
            return
        self._result_buffer.sort(key=lambda x: (-x[0], x[3].lower(), x[1].lower() if isinstance(x[1], str) else ""))
        self.go_to_page(self._current_page)
        self.layoutChanged.emit()

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
        elif role == Qt.ItemDataRole.ToolTipRole:
            return row_data[3]  # 전체 경로
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
        """get_all_results 함수."""
        return self._result_buffer

    def clear(self):
        if not self._result_buffer and not self._data:
            return
        self.beginResetModel()
        self._data = []
        self._result_buffer = []
        self._current_page = 1
        self.endResetModel()

    def get_total_pages(self) -> int:
        """get_total_pages 함수."""
        if not self._pagination_enabled or not self._result_buffer:
            return 1
        return (len(self._result_buffer) + self._page_size - 1) // self._page_size

    def get_current_page(self) -> int:
        """get_current_page 함수."""
        return self._current_page

    def go_to_page(self, page_number: int):
        """go_to_page 함수."""
        if not self._pagination_enabled:
            return
        total_pages = self.get_total_pages()
        if page_number < 1:
            page_number = 1
        elif page_number > total_pages:
            page_number = total_pages
        if page_number == self._current_page and self._data:
            return
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
        end_idx = min(start_idx + self._page_size, len(self._result_buffer))
        self._data = self._result_buffer[start_idx:end_idx]

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

    def add_results(self, results):
        """도착 순서대로 결과를 누적합니다 (전역 정렬은 검색 종료 후 호출)."""
        if not results:
            return

        new_items = []
        for item in results:
            if len(item) >= 5:
                new_items.append(list(item))
            elif len(item) >= 3:
                file_path, count, matches = item[0], item[1], item[2]
                folder = os.path.dirname(file_path)
                file_name_with_ext = os.path.basename(file_path)
                if len(matches) >= 100000:
                    safe_matches = matches[:50000]
                else:
                    safe_matches = matches
                new_items.append([count, file_name_with_ext, folder, file_path, safe_matches])

        if not new_items:
            return

        if len(new_items) < 1000:
            self.layoutAboutToBeChanged.emit()
            self._result_buffer.extend(new_items)
            self._load_current_page_data_no_reset()
            self.layoutChanged.emit()
        else:
            self.beginResetModel()
            self._result_buffer.extend(new_items)
            self._load_current_page_data_no_reset()
            self.endResetModel()

    def sort_globally(self):
        """전체 데이터를 매치 수 DESC, 경로 ASC 순으로 정렬합니다."""
        if not self._result_buffer:
            return
        self.beginResetModel()
        self._result_buffer.sort(key=lambda x: (-(x[0] if x[0] is not None else 0), str(x[1])))
        self._current_page = 1
        self._load_current_page_data_no_reset()
        self.endResetModel()

    def _load_all_from_buffer(self):
        """버퍼의 모든 결과를 즉시 로드합니다 (페이지네이션 비활성화 시)."""
        self.beginResetModel()
        self._data = list(self._result_buffer)
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

    @property
    def match_count(self) -> int:
        """전체 매치 항목의 총 개수를 반환합니다."""
        return len(self._all_data_buffer)

    def rowCount(self, parent=QModelIndex()):
        return len(self._data)

    def columnCount(self, parent=QModelIndex()):
        mode = self.search_mode or Constants.MODE_NORMAL
        if mode == Constants.MODE_NORMAL or mode == AppStrings.SPECIAL_SEARCH_OFF:
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
                                f"{sheet_or_pos}!{cell_or_value}"
                                if cell_or_value not in ("", "None")
                                else sheet_or_pos
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
                    # 일반 모드 엑셀의 경우 content 또는 extra_1에 실제 값이 있을 수 있으므로 방어적으로 처리
                    val = str(row_data.extra_1 or row_data.content)
                    rendered_val = self._render_highlighted(val)

                    # [UI/UX] None 및 지저분한 구분자 제거
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
                    rendered_val = self._render_highlighted(val)
                    return f"<html>{escape(row_data.position)} | {rendered_val}</html>"

            if col >= len(row_data):
                return ""
            val = str(row_data[col])
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
        if not self._all_data_buffer:
            return 1
        return (len(self._all_data_buffer) + self._page_size - 1) // self._page_size

    def go_to_page(self, page_number: int):
        total_pages = self.get_total_pages()
        if page_number < 1:
            page_number = 1
        elif page_number > total_pages:
            page_number = total_pages
        if page_number == self._current_page and self._data:
            return
        self._current_page = page_number
        self._load_current_page_data()

    def _load_current_page_data(self):
        self.beginResetModel()
        self._load_current_page_data_no_reset()
        self.endResetModel()

    def _load_current_page_data_no_reset(self):
        start_idx = (self._current_page - 1) * self._page_size
        end_idx = min(start_idx + self._page_size, len(self._all_data_buffer))
        self._data = self._all_data_buffer[start_idx:end_idx]

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
        # UI 콤보박스 값("끄기") 정규화
        if search_mode == AppStrings.SPECIAL_SEARCH_OFF or not search_mode:
            search_mode = Constants.MODE_NORMAL

        self.current_file_path = file_path
        self.search_text = search_text
        self.search_mode = search_mode
        # 버퍼 초기화 보강 (rowCount 불일치 및 메모리 잔존 해결)
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
            for m in matches:
                # [상급 보안/무결성] 특수 모드 전용 튜플을 일반 언패킹보다 먼저 처리하여 데이터 누락 방지

                # Case 1: Excel (Line, Sheet, Cell, Val, [Offset, Length])
                # Case 1: Excel (Line, Sheet, Cell, Val, [Offset, Length])
                # 확장자뿐만 아니라 실제 엑셀 모드인 경우에만 엑셀 전용 구조로 파싱
                if Constants.MODE_EXCEL in search_mode and len(m) >= 4 and isinstance(m[1], str):
                    s, c, v = str(m[1]), str(m[2]), str(m[3])
                    # Offset 우선순위: m[4] (Rust 엔진 제공) > f"{s}!{c}" (계산값)
                    off = m[4] if (len(m) > 4 and m[4] is not None) else f"{s}!{c}"
                    normalized_matches.append(
                        SearchMatchSchema(
                            position=s, content=c, extra_1=v, offset=off, length=m[5] if len(m) > 5 else None
                        )
                    )
                    continue

                # Case 2: Archive (Line, NS, Key, Src, Trans, [Offset, Length])
                if Constants.MODE_ARCHIVE in search_mode and len(m) >= 5:
                    normalized_matches.append(
                        SearchMatchSchema(
                            position=str(m[0]),
                            content=str(m[1]),  # NS
                            extra_1=str(m[2]),  # Key
                            extra_2=str(m[3]),  # Src
                            extra_3=str(m[4]),  # Trans
                            offset=m[5] if len(m) > 5 else None,
                            length=m[6] if len(m) > 6 else None,
                        )
                    )
                    continue

                # Case 3: 일반 텍스트 및 기타 특수 검색 (Line, Content, Offset, Length) 대입
                # 일반 모드에서도 엑셀 4-튜플(Line, Sheet, Cell, Value)이 올 경우 위치 정보를 Sheet!Cell 형식으로 조합
                line_no: str = ""
                content: str = ""
                offset: Optional[Union[int, str]] = None
                length: Optional[int] = None

                # Rust Excel 결과(6-튜플) 및 Python Excel 결과(4-튜플) 모두 대응
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

                    parts = sheet_candidate.split(" | ", 2)
                    if len(parts) >= 3:
                        sheet_name = parts[0].strip()
                        cell_ref = parts[1].strip()
                        cell_val = parts[2]
                        if excel_cell_pattern and excel_cell_pattern.fullmatch(cell_ref):
                            pos = f"{sheet_name}!{cell_ref}"
                            normalized_matches.append(
                                SearchMatchSchema(
                                    position=sheet_name,
                                    content=cell_ref,
                                    extra_1=cell_val,
                                    offset=pos,
                                )
                            )
                            continue

                line_no = str(m[0])
                content = str(m[1])
                offset = m[2] if (len(m) > 2 and m[2] is not None) else None
                length = m[3] if (len(m) > 3 and m[3] is not None) else None

                # 기존 3-튜플(Line, Pos, Val) 호환 유지 (엑셀 폴백용)
                if is_excel_file and len(m) == 3 and isinstance(m[2], str):
                    pos = str(m[1])
                    if "!" in pos:
                        s, c = pos.split("!", 1)
                        normalized_matches.append(
                            SearchMatchSchema(position=s, content=c, extra_1=str(m[2]), offset=pos)
                        )
                    else:
                        normalized_matches.append(
                            SearchMatchSchema(position=pos, content="", extra_1=str(m[2]), offset=pos)
                        )
                    continue

                is_special_val = (
                    is_excel_file or any(mode in search_mode for mode in [Constants.MODE_JSON, Constants.MODE_XML])
                ) and isinstance(offset, str)

                parts = content.split(" | ")
                if Constants.MODE_JSON in search_mode or Constants.MODE_XML in search_mode:
                    if len(parts) >= 2:
                        off_val = str(offset) if offset is not None else ""
                        normalized_matches.append(
                            SearchMatchSchema(
                                position=str(line_no),
                                content=str(parts[0]),
                                extra_1=str(parts[1]),
                                offset=off_val,
                                length=length,
                            )
                        )
                    elif is_special_val:
                        normalized_matches.append(
                            SearchMatchSchema(
                                position=str(line_no), content=str(content), extra_1=str(offset), offset=offset
                            )
                        )
                    else:
                        normalized_matches.append(
                            SearchMatchSchema(position=str(line_no), content=str(content), offset=offset)
                        )
                else:
                    normalized_matches.append(
                        SearchMatchSchema(
                            position=str(line_no), content=str(content), offset=offset, length=length
                        )
                    )

        # 헤더 설정
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
        elif Constants.MODE_EXCEL in search_mode:
            self._headers = [
                AppStrings.HEADER_EXCEL_SHEET,
                AppStrings.HEADER_EXCEL_CELL,
                AppStrings.HEADER_EXCEL_VALUE,
            ]
        else:
            self._headers = [AppStrings.HEADER_POSITION, AppStrings.HEADER_CONTENT]

        self._all_data_buffer = normalized_matches
        self._load_current_page_data()

    def add_results(self, results):
        """도착 순서대로 결과를 누적합니다 (전역 정렬은 검색 종료 후 호출)."""
        if not results:
            return
        self.layoutAboutToBeChanged.emit()
        self._all_data_buffer.extend(results)
        self._load_current_page_data_no_reset()
        self.layoutChanged.emit()

    def clear(self):
        if not self._all_data_buffer and not self._data:
            return
        self.beginResetModel()
        self._data = []
        self._all_data_buffer = []
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

    def get_match(self, row):
        """특정 행의 매치 데이터를 반환합니다."""
        if 0 <= row < len(self._data):
            return self._data[row]
        return None

    def get_match_info(self, row):
        """get_match_info 함수."""
        if 0 <= row < len(self._data):
            d = self._data[row]
            # [Line, ..., Offset, Length] 구조이므로 마지막 두 요소를 반환
            if len(d) >= 3:
                return d[-2], d[-1]
        return None, None
