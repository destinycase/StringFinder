import ctypes
import logging
import multiprocessing
import os
import re
import unicodedata
import warnings
from os.path import splitext
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple, Union, overload

from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants

EXCEL_EXTS = {".xlsx", ".xlsm", ".xls", ".xlsb"}
logger = logging.getLogger("StringFinder.SearchEngine")
try:
    import sf_engine

    REQUIRED_API_VERSION = 4
    engine_version = getattr(sf_engine, "API_VERSION", 0)
    if engine_version < REQUIRED_API_VERSION:
        logger.error(AppStrings.LOG_SYS_SF_ENGINE_NOT_FOUND.format("Compatible API"))
        HAS_RUST_ENGINE = False
    else:
        HAS_RUST_ENGINE = True
        logger.info(AppStrings.LOG_SYS_RUST_SUCCESS)
except ImportError:
    HAS_RUST_ENGINE = False
    logger.warning(AppStrings.LOG_SYS_RUST_FALLBACK)
SearchMatch = Tuple[Any, ...]
SearchResult = Tuple[str, int, List[SearchMatch]]
SkippedResult = Tuple[str, str]
FileInfo = Tuple[str, int]
SKIP_CODE_WALK = "ERR_WALK"
SKIP_CODE_OPEN = "ERR_OPEN"
SKIP_CODE_METADATA = "ERR_METADATA"
SKIP_CODE_MMAP = "ERR_MMAP"
SKIP_CODE_TOO_LARGE = "ERR_TOO_LARGE"
SKIP_CODE_MEMORY_GUARD = "ERR_MEMORY_GUARD"
SKIP_CODE_PANIC = "ERR_PANIC"
SKIP_CODE_CRITICAL = "ERR_CRITICAL"
SKIP_CODE_UNKNOWN = "ERR_UNKNOWN"
RUST_MATCH_MARKER_BINARY = "__SF_BINARY_MATCH__|"
RUST_MATCH_MARKER_LONG_LINE = "__SF_LONG_LINE__|"
RUST_MATCH_MARKER_EXCEL_SHEET_ERROR = "__SF_EXCEL_SHEET_ERR__|"
RUST_MATCH_MARKER_EXCEL_PANIC = "__SF_EXCEL_PANIC__|"
CRITICAL_SKIP_CODES = {
    SKIP_CODE_WALK,
    SKIP_CODE_OPEN,
    SKIP_CODE_METADATA,
    SKIP_CODE_MMAP,
    SKIP_CODE_PANIC,
    SKIP_CODE_CRITICAL,
}
_SKIP_REASON_TEMPLATES = {
    SKIP_CODE_WALK: AppStrings.SKIP_REASON_WALK,
    SKIP_CODE_OPEN: AppStrings.SKIP_REASON_OPEN,
    SKIP_CODE_METADATA: AppStrings.SKIP_REASON_METADATA,
    SKIP_CODE_MMAP: AppStrings.SKIP_REASON_MMAP,
    SKIP_CODE_TOO_LARGE: AppStrings.SKIP_REASON_TOO_LARGE,
    SKIP_CODE_MEMORY_GUARD: AppStrings.SKIP_REASON_MEMORY_GUARD,
    SKIP_CODE_PANIC: AppStrings.SKIP_REASON_PANIC,
    SKIP_CODE_CRITICAL: AppStrings.SKIP_REASON_CRITICAL,
    SKIP_CODE_UNKNOWN: AppStrings.SKIP_REASON_UNKNOWN,
}
_LEGACY_SKIP_MARKERS = (
    ("walker error", SKIP_CODE_WALK),
    ("walk error", SKIP_CODE_WALK),
    ("open error", SKIP_CODE_OPEN),
    ("metadata error", SKIP_CODE_METADATA),
    ("mmap error", SKIP_CODE_MMAP),
    ("file too large", SKIP_CODE_TOO_LARGE),
    ("panic", SKIP_CODE_PANIC),
    ("critical error", SKIP_CODE_CRITICAL),
)


def _build_skip_reason(code: str, detail: Any) -> str:
    return "{}|{}".format(code, "" if detail is None else str(detail))


def _decode_skip_reason(reason: Any) -> Tuple[str, str]:
    reason_str = str(reason or "").strip()
    if not reason_str:
        return SKIP_CODE_UNKNOWN, ""
    if "|" in reason_str:
        code, detail = reason_str.split("|", 1)
        code = code.strip().upper()
        if code.startswith("ERR_"):
            return code, detail.strip()
    bracket_match = re.match(r"^\[(ERR_[A-Z_]+)\]\s*(.*)$", reason_str)
    if bracket_match:
        code = bracket_match.group(1)
        detail = bracket_match.group(2).lstrip(":").strip()
        return code, detail
    lower_reason = reason_str.lower()
    for marker, code in _LEGACY_SKIP_MARKERS:
        if marker in lower_reason:
            detail = reason_str.split(":", 1)[1].strip() if ":" in reason_str else reason_str
            return code, detail
    return SKIP_CODE_UNKNOWN, reason_str


def parse_skip_reason_code(reason: Any) -> str:
    code, _ = _decode_skip_reason(reason)
    return code


def is_critical_skip_reason(reason: Any) -> bool:
    return parse_skip_reason_code(reason) in CRITICAL_SKIP_CODES


def format_skip_reason(reason: Any) -> str:
    code, detail = _decode_skip_reason(reason)
    template = _SKIP_REASON_TEMPLATES.get(code, AppStrings.SKIP_REASON_UNKNOWN)
    safe_detail = detail if detail else str(reason or "")
    try:
        return template.format(safe_detail)
    except Exception:
        return AppStrings.SKIP_REASON_UNKNOWN.format(str(reason or ""))


def _parse_rust_binary_count(marker_count: str, length: Any) -> int:
    try:
        count = int(str(marker_count).strip())
        if count > 0:
            return count
    except Exception:
        pass
    if length is not None:
        try:
            count = int(length)
            if count > 0:
                return count
        except Exception:
            pass
    return 1


def _normalize_rust_match(
    match_obj: Any, special_mode: Optional[str] = None
) -> Tuple[Optional[SearchMatch], Optional[int]]:
    """Rust SearchMatch 객체를 UI 모델용 튜플로 변환합니다. 특수 모드 데이터도 구조화합니다."""
    line = getattr(match_obj, "line", 1)
    content = str(getattr(match_obj, "content", ""))
    offset = getattr(match_obj, "offset", None)
    length = getattr(match_obj, "length", None)

    # 1. 특수 마커 처리 (바이너리, 긴 줄 등)
    if content.startswith(RUST_MATCH_MARKER_BINARY):
        marker_count = content[len(RUST_MATCH_MARKER_BINARY) :]
        count = _parse_rust_binary_count(marker_count, length)
        return (1, AppStrings.MSG_BINARY_MATCH.format(count), None, None), count

    if content.startswith(RUST_MATCH_MARKER_LONG_LINE):
        preview = content[len(RUST_MATCH_MARKER_LONG_LINE) :]
        return (line, AppStrings.MSG_LONG_LINE_PREVIEW.format(preview), offset, length), None

    if content.startswith("ERR_") and "|" in content:
        # 일반적인 에러 마커 처리 (ERR_PANIC 등)
        # 이 마커가 발견되면 호출부에서 None을 반환받아 무시하거나,
        # Excel 처리처럼 별도 fetch가 필여할 수 있음.
        # 여기서는 None을 반환하여 일반 결과에서 제외되도록 함.
        return None, None

    if content.startswith(RUST_MATCH_MARKER_EXCEL_SHEET_ERROR):
        # 엑셀 시트 파싱 오류 마커 처리
        # 이전: (None, None, None, None) 튜플 반환 후 호출부의 'is not None' 체크가 통과하여 TypeError 위험
        # 수정: None 반환으로 caller에서 안전하게 예외 처리
        error_msg = content[len(RUST_MATCH_MARKER_EXCEL_SHEET_ERROR) :]
        logger.warning(f"Excel sheet parse error: {error_msg}")
        return None, None

    if content.startswith(RUST_MATCH_MARKER_EXCEL_PANIC):
        # 엑셀 패닉 마커 처리 (포맷: __SF_EXCEL_PANIC__|ext|detail)
        data = content[len(RUST_MATCH_MARKER_EXCEL_PANIC) :]
        if "|" in data:
            ext, detail = data.split("|", 1)
            logger.error(f"Excel engine panic [{ext}]: {detail}")
        else:
            logger.error(f"Excel engine panic: {data}")
        return None, None

    # 2. 특수 모드 데이터 구조화 (벌크 검색 결과)
    if special_mode:
        mode_upper = str(special_mode).upper()
        if Constants.MODE_ARCHIVE.upper() in mode_upper:
            # 구분자를 Tab(\t)으로 변경: " | " 포함 가능한 데이터의 무결성 보호
            parts = content.split("\t")
            ns = parts[0] if len(parts) > 0 else ""
            key = parts[1] if len(parts) > 1 else ""
            src = parts[2] if len(parts) > 2 else ""
            trans = parts[3] if len(parts) > 3 else ""
            return (line, ns, key, src, trans, offset, length), None

        elif Constants.MODE_EXCEL.upper() in mode_upper:
            # Rust 엔진에서 '\t' 구분자 사용: Sheet\tCell\tVal
            parts = content.split("\t", 2)
            if len(parts) >= 3:
                # (Line, Sheet, Cell, Val, Offset, Length) 6-튜플 반환
                return (line, parts[0], parts[1], parts[2], offset, length), None

        elif Constants.MODE_XML.upper() in mode_upper or Constants.MODE_JSON.upper() in mode_upper:
            # Rust 엔진에서 '\t' 구분자 사용: Name/Path\tValue
            parts = content.split("\t", 1)
            if len(parts) >= 2:
                # (Line, Name/Path, Value, Offset, Length) 5-튜플 반환
                return (line, parts[0], parts[1], offset, length), None

    if content.startswith("__SF_"):
        return None, None

    # [H-02] 엑셀 결과가 'Sheet | Cell | Value' 형태로 올 때의 파싱 강화
    if " | " in content:
        parts = content.split(" | ")
        if len(parts) >= 2:
            sheet_name = parts[0]
            cell_pos = parts[1]
            val = parts[2] if len(parts) > 2 else ""
            # (Line, Sheet, Cell, Val, Offset, Length) 6-튜플 반환 (Excel 모드 계약)
            return (line, sheet_name, cell_pos, val, offset, length), None

    return (line, content, offset, length), None


def _normalize_rust_matches(matches: Any, special_mode: Optional[str] = None) -> Tuple[List[SearchMatch], int]:
    """검색 결과 리스트 전체를 정규화합니다."""
    normalized: List[SearchMatch] = []
    binary_count = 0
    for match_obj in matches:
        normalized_match, marker_count = _normalize_rust_match(match_obj, special_mode)
        if normalized_match is not None:
            normalized.append(normalized_match)
        if marker_count is not None:
            binary_count += marker_count
    return normalized, binary_count


def _extract_excel_marker_skip_reason(matches: Any) -> Optional[str]:
    """Excel marker-only 결과를 skip 사유로 복원한다."""
    for match_obj in matches:
        content = str(getattr(match_obj, "content", ""))
        if content.startswith(RUST_MATCH_MARKER_EXCEL_PANIC):
            panic_detail = content[len(RUST_MATCH_MARKER_EXCEL_PANIC) :].strip() or "unknown"
            return AppStrings.ERROR_EXCEL_PANIC.format(panic_detail)
        if content.startswith(RUST_MATCH_MARKER_EXCEL_SHEET_ERROR):
            payload = content[len(RUST_MATCH_MARKER_EXCEL_SHEET_ERROR) :]
            if "|" in payload:
                sheet_name, detail = payload.split("|", 1)
            else:
                sheet_name, detail = "unknown", payload
            return AppStrings.ERROR_SEARCH_EXCEL_SHEET.format(sheet_name, detail)
    return None


def normalize_unicode(text: Optional[str]) -> str:
    """normalize_unicode ?⑥닔."""
    if text is None:
        return ""
    return unicodedata.normalize("NFC", str(text))


def is_hidden_windows(path: str) -> bool:
    """is_hidden_windows ?⑥닔."""
    if os.name != "nt":
        return False
    try:
        attrs = ctypes.windll.kernel32.GetFileAttributesW(path)
        return attrs != -1 and bool(attrs & 0x02)
    except Exception:
        return False


def get_rust_mode_bits(special_mode: Optional[str], exclude_binary: bool = False, is_boolean: bool = False) -> int:
    """get_rust_mode_bits ?⑥닔."""
    bits = Constants.RUST_MODE_NORMAL
    if exclude_binary:
        bits |= Constants.RUST_MODE_EXCLUDE_BINARY
    if is_boolean:
        bits |= Constants.RUST_MODE_BOOLEAN_ONLY

    if not special_mode:
        return bits
    if Constants.MODE_XML in special_mode:
        bits |= Constants.RUST_MODE_XML
    if Constants.MODE_JSON in special_mode:
        bits |= Constants.RUST_MODE_JSON
    if Constants.MODE_ARCHIVE in special_mode:
        bits |= Constants.RUST_MODE_ARCHIVE
    if Constants.MODE_EXACT in special_mode:
        bits |= Constants.RUST_MODE_EXACT
    return bits


def is_binary_file(file_path: str) -> bool:
    """is_binary_file ?⑥닔."""
    try:
        with open(file_path, "rb") as f:
            # 1KB -> 8KB로 확대: PDF의 처음 1KB 이후에 NUL로 시작하는 바이너리 판정 버그 수정
            chunk = f.read(8192)
            return b"\x00" in chunk
    except (IOError, OSError, PermissionError):
        return False
    except Exception as e:
        logger.debug(AppStrings.LOG_SCH_BINARY_CHECK_FAIL.format(file_path, e))
        return False


def detect_encoding_quickly(data: bytes) -> str:
    """detect_encoding_quickly ?⑥닔."""
    if not data:
        return Constants.ENC_UTF8
    if data.startswith(b"\xef\xbb\xbf"):
        return Constants.ENC_UTF8_SIG
    if data.startswith(b"\xff\xfe"):
        return Constants.ENC_UTF16
    if data.startswith(b"\xfe\xff"):
        return Constants.ENC_UTF16_BE
    try:
        data.decode(Constants.ENC_UTF8)
        return Constants.ENC_UTF8
    except UnicodeDecodeError:
        pass
    # EUC-KR 거짓양성 완화: 단순 디코딩 성공이 아닌 실제 한글(AC00-D7A3) 포함 여부에만 판정
    # EUC-KR은 2바이트 범위가 넓어 다른 인코딩 바이너리도 디코딩되므로 대표 -> 실제 주요 코드포인트 보유 여부로 판정
    # any(... for c in decoded) 전체 문자 순회 대신 re.search()로 단락평가하여 O(n) -> O(1) 개선
    try:
        decoded_euckr = data.decode(Constants.ENC_EUCKR)
        if re.search("[\uac00-\ud7a3]", decoded_euckr):
            return Constants.ENC_EUCKR
    except UnicodeDecodeError:
        pass
    try:
        decoded_cp = data.decode(Constants.ENC_CP949)
        if re.search("[\uac00-\ud7a3]", decoded_cp):
            return Constants.ENC_CP949
    except UnicodeDecodeError:
        pass
    return Constants.ENC_UTF8


def read_text_file_with_encoding(file_path: str) -> Tuple[str, str]:
    """파일 경로를 받아 자동으로 인코딩을 감지한 뒤 텍스트를 반환합니다."""
    try:
        # 1 KB 샘플 → 64 KB 샘플로 증대하여 EUC-KR/CP949 오탐(false-negative) 방지
        # 한글 파일은 앞 1 KB에 BOM 없이 시작하는 경우 UTF-8로 오탐될 수 있음
        with open(file_path, "rb") as f:
            head = f.read(65536)
            encoding = detect_encoding_quickly(head)
        with open(file_path, "r", encoding=encoding, errors="replace") as f:
            content = f.read()
        return content, encoding
    except (IOError, OSError) as e:
        logger.debug(AppStrings.ERROR_READ_FILE.format(file_path, e))
        raise


def parse_excel_cell_address(address: str) -> Tuple[str, int, int]:
    """
    'Sheet1!B5' 또는 'B5' 형식을 분리하여 (시트명 또는 None, 열 인덱스, 행 인덱스)를 반환합니다.
    인덱스는 0부터 시작합니다.
    """
    sheet_name = ""
    cell_info = address
    if "!" in address:
        sheet_name, cell_info = address.split("!", 1)

    # 행/열 파싱 (숫자 부분 분리)
    match = re.match(r"([A-Z]+)([0-9]+)", cell_info.upper())
    if not match:
        return sheet_name, 0, 0

    col_str, row_str = match.groups()

    # 열 인덱스 계산 (A=0, B=1, ..., Z=25, AA=26)
    col_idx = 0
    for char in col_str:
        col_idx = col_idx * 26 + (ord(char) - ord("A") + 1)
    col_idx -= 1

    row_idx = int(row_str) - 1
    return sheet_name, col_idx, row_idx


class FileScanner:
    """FileScanner 클래스."""

    def __init__(
        self,
        folders: List[str],
        extensions: List[str],
        filename_filter: Optional[Union[str, List[str]]] = None,
        stop_check_callback: Optional[Callable[[], bool]] = None,
        **kwargs,
    ):
        """__init__ ?⑥닔."""
        self.folders = folders
        self.extensions = [(e if e.startswith(".") else "." + e).lower() for e in extensions]
        if not filename_filter:
            self.filename_filters: List[str] = []
        elif isinstance(filename_filter, str):
            self.filename_filters = [filename_filter.lower()]
        else:
            self.filename_filters = [f.lower() for f in filename_filter]
        # 파일 패턴 필터를 __init__에서 한 번만 처리
        # 이전: _scan_recursive 루프에서 파일마다 리스트 컴프리헨션 반복 실행 -> 오버헤드
        # 수정: 생성 시점에 한 번 계산 후 재사용
        import fnmatch as _fnmatch

        self._fnmatch = _fnmatch
        self.processed_filename_filters: List[str] = [
            (f"*{f}*" if "*" not in f else f).lower() for f in self.filename_filters
        ]
        self.exclude_hidden = bool(kwargs.get("exclude_hidden", False))
        self.stop_check_callback = stop_check_callback
        self._yield_counter: int = 0

    def scan(self) -> List[FileInfo]:
        """scan ?⑥닔."""
        file_list: List[FileInfo] = []
        visited = set()
        for folder in self.folders:
            if self.stop_check_callback and self.stop_check_callback():
                break
            if not os.path.exists(folder):
                continue
            real_folder = os.path.realpath(folder)
            if real_folder in visited:
                continue
            visited.add(real_folder)
            self._scan_recursive(folder, file_list, visited)
        return file_list

    def _scan_recursive(self, folder: str, file_list: List[FileInfo], visited: Optional[set] = None):
        """_scan_recursive ?⑥닔."""
        if visited is None:
            visited = set()
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    if self.stop_check_callback and self.stop_check_callback():
                        return
                    if self.exclude_hidden and is_hidden_windows(entry.path):
                        continue
                    if hasattr(self, "_yield_counter"):
                        self._yield_counter += 1
                        if self._yield_counter % 5000 == 0:
                            import time

                            time.sleep(Constants.YIELD_SLEEP_TIME)
                    else:
                        self._yield_counter = 1
                    try:
                        if entry.is_dir():
                            real_path = os.path.realpath(entry.path)
                            if real_path not in visited:
                                visited.add(real_path)
                                self._scan_recursive(entry.path, file_list, visited)
                        elif entry.is_file():
                            ext = splitext(entry.name)[1].lower()
                            if ext in self.extensions:
                                if self.processed_filename_filters:
                                    is_matched = False
                                    fname_lower = entry.name.lower()
                                    # __init__에서 전처리된 패턴 재사용 (파일마다 생성하던 것 제거)
                                    for pattern in self.processed_filename_filters:
                                        if self._fnmatch.fnmatch(fname_lower, pattern):
                                            is_matched = True
                                            break
                                    if not is_matched:
                                        continue
                                file_list.append((entry.path, entry.stat().st_size))
                    except PermissionError as e:
                        logger.debug(AppStrings.LOG_SCH_BINARY_CHECK_FAIL.format(entry.path, e))
                    except OSError as e:
                        logger.debug(AppStrings.LOG_SCH_BINARY_CHECK_FAIL.format(entry.path, e))
        except PermissionError as e:
            logger.warning(AppStrings.LOG_SCH_FOLDER_ACCESS_DENIED.format(folder, e))
        except OSError as e:
            logger.error(AppStrings.LOG_SCH_SCAN_OS_ERROR.format(folder, e))


def strip_comments(content: str, ext: str) -> str:
    """[DEPRECATED] 검색 무결성 보장을 위해 비활성화됨. 추후 제거 예정."""
    # 기능 의도는 제거 전 유수하여 공개 호출 유지 -> deprecated 경고 발생으로 내부 사용 방지
    warnings.warn(
        "strip_comments는 비활성화되었습니다. 사용을 중단하세요.",
        DeprecationWarning,
        stacklevel=2,
    )
    return content  # 원본 반환 (실제 콘텐츠 수정 없음)


def _check_excel_signature(file_path: str) -> Tuple[bool, Optional[str]]:
    """_check_excel_signature ?⑥닔."""
    try:
        with open(file_path, "rb") as f:
            header = f.read(8)
            if header.startswith(b"\xd0\xcf\x11\xe0"):
                return True, None
            if header.startswith(b"PK\x03\x04"):
                return True, None
            return False, AppStrings.ERROR_EXCEL_SIGNATURE
    except (IOError, OSError, PermissionError) as e:
        return False, AppStrings.ERROR_EXCEL_ACCESS.format(e)
    except Exception as e:
        logger.debug(AppStrings.ERROR_EXCEL_PROCESS.format(e))
        return False, AppStrings.ERROR_EXCEL_PROCESS.format(e)


def is_valid_excel_signature(file_path: str) -> bool:
    """is_valid_excel_signature ?⑥닔."""
    is_valid, _ = _check_excel_signature(file_path)
    return is_valid


def search_in_excel_special(
    file_path: str,
    search_string: str,
    exact_match: bool = False,
    use_complex_search: bool = False,
    stop_event=None,
) -> Optional[Union[SearchResult, SkippedResult]]:
    """search_in_excel_special ?⑥닔."""
    signature_ok, signature_error = _check_excel_signature(file_path)
    if not signature_ok:
        return (Constants.STATUS_SKIPPED, signature_error or AppStrings.ERROR_EXCEL_SIGNATURE)
    if HAS_RUST_ENGINE and not use_complex_search:
        try:
            mode_bits = Constants.RUST_MODE_NORMAL | Constants.RUST_MODE_EXCEL
            if exact_match:
                mode_bits |= Constants.RUST_MODE_EXACT
            results = sf_engine.search_file(str(file_path), search_string, mode_bits)
            if results:
                processed = []
                sheet_errors: List[str] = []
                for m in results:
                    content = str(m.content)
                    if content.startswith(RUST_MATCH_MARKER_EXCEL_PANIC):
                        panic_detail = content[len(RUST_MATCH_MARKER_EXCEL_PANIC) :].strip() or "unknown"
                        return (Constants.STATUS_SKIPPED, AppStrings.ERROR_EXCEL_PANIC.format(panic_detail))
                    if content.startswith(RUST_MATCH_MARKER_EXCEL_SHEET_ERROR):
                        payload = content[len(RUST_MATCH_MARKER_EXCEL_SHEET_ERROR) :]
                        if "|" in payload:
                            sheet_name, detail = payload.split("|", 1)
                        else:
                            sheet_name, detail = "unknown", payload
                        sheet_error = AppStrings.ERROR_SEARCH_EXCEL_SHEET.format(sheet_name, detail)
                        logger.warning(AppStrings.LOG_SCH_ERROR_FILE.format(file_path, sheet_error))
                        sheet_errors.append(sheet_error)
                        continue
                    # [H-02] Rust 엔진 결과가 '\t' 또는 ' | ' 구분자로 올 수 있으므로 모두 지원
                    if "\t" in content:
                        parts = content.split("\t", 2)
                    else:
                        parts = content.split(" | ", 2)

                    if len(parts) >= 3:
                        processed.append((m.line, parts[0], parts[1], parts[2]))
                    else:
                        processed.append((m.line, content, "", ""))
                if processed:
                    return (file_path, len(processed), processed)
                if sheet_errors:
                    return (Constants.STATUS_SKIPPED, sheet_errors[0])
            # [방어 정책] Rust 엔진에서 결과가 없으면 즉시 종료합니다.
            # 이는 중복 파싱과 다른 성능 비용을 방지하기 위한 의도적인 설계입니다.
            # 복합 검색(대소문자 비교) 상황에서는 Python 폴백을 사용하여 무결성을 보장합니다.
            return None
        except BaseException as e:
            logger.error(AppStrings.LOG_SCH_RUST_EXCEL_FAIL.format(file_path, e))
            # [Policy] 자동 폴백 중단: 오류 시 해당 파일을 스킵 처리
            return (Constants.STATUS_SKIPPED, AppStrings.ERROR_SEARCH_EXCEL.format(file_path, e))
    try:
        from python_calamine import CalamineWorkbook

        try:
            workbook = CalamineWorkbook.from_path(file_path)
        except (IOError, OSError) as e:
            return (Constants.STATUS_SKIPPED, AppStrings.ERROR_EXCEL_ACCESS.format(e))
        except BaseException as e:  # PanicException 등 C 레벨 예외까지 포착
            return (Constants.STATUS_SKIPPED, AppStrings.ERROR_EXCEL_PROCESS.format(e))

        count = 0
        matches = []
        search_string_norm = re.sub(r"\s+", " ", search_string).strip()
        search_string_lower = search_string.casefold()

        for sheet_name in workbook.sheet_names:
            if stop_event and stop_event.is_set():
                break
            try:
                # [성능 최적화] to_python() 대신 상위 이너레이터를 사용하여 메모리 효율 향상
                sheet = workbook.get_sheet_by_name(sheet_name)
                # iter_rows() 호출 후 회수 시점에서 발생하는 패닉 처리
                rows_iter = sheet.iter_rows()
                for row_idx, row in enumerate(rows_iter):
                    if row_idx % 100 == 0 and stop_event and stop_event.is_set():
                        break
                    for col_idx, cell_value in enumerate(row):
                        if cell_value is not None:
                            val_str = normalize_unicode(str(cell_value))
                            val_norm = re.sub(r"\s+", " ", val_str).casefold().strip()
                            val_lower = val_str.casefold()
                            is_match = False
                            if exact_match:
                                is_match = (val_norm == search_string_norm) or (
                                    val_norm.replace(" ", "") == search_string_norm.replace(" ", "")
                                )
                            else:
                                is_match = (search_string_norm in val_norm) or (search_string_lower in val_lower)
                            if is_match:
                                count += 1
                                col_letter = ""
                                temp_col = col_idx
                                while temp_col >= 0:
                                    col_letter = chr(65 + (temp_col % 26)) + col_letter
                                    temp_col = (temp_col // 26) - 1
                                # offset_row 반영하여 정확한 셀 좌표 보고
                                abs_row = (
                                    row_idx + 1 + (sheet.start[0] if hasattr(sheet, "start") and sheet.start else 0)
                                )
                                matches.append((0, sheet_name, f"{col_letter}{abs_row}", val_str))
            except BaseException as e:  # 특정 시트에서 패닉 발생 시 해당 시트만 스킵
                sheet_err_msg = AppStrings.ERROR_SEARCH_EXCEL_SHEET.format(sheet_name, e)
                logger.error(AppStrings.LOG_SCH_ERROR_FILE.format(file_path, sheet_err_msg))
                continue

        if count > 0:
            return (file_path, count, matches)
    except ImportError:
        return (Constants.STATUS_SKIPPED, AppStrings.ERROR_EXCEL_CALAMINE)
    except BaseException as e:  # 전체 파일 처리 중 발생하는 모든 치명적 예외 포착
        return (Constants.STATUS_SKIPPED, AppStrings.ERROR_SEARCH_EXCEL.format(file_path, e))
    return None


def search_in_json_special(
    file_path: str,
    search_string: str,
    exact_match: bool = False,
    use_complex_search: bool = False,
    stop_event=None,
) -> Optional[Union[SearchResult, SkippedResult]]:
    """
    JSON 특수 검색을 수행합니다.
    """
    if HAS_RUST_ENGINE and not use_complex_search:
        try:
            mode_bits = Constants.RUST_MODE_JSON
            if exact_match:
                mode_bits |= Constants.RUST_MODE_EXACT
            results = sf_engine.search_file(str(file_path), search_string, mode_bits)
            if results:
                processed = []
                for m in results:
                    # 메모리 한도 확인 (큰 파일 안전성 향상)
                    if "ERR_MEMORY_GUARD" in m.content:
                        logger.warning(AppStrings.LOG_SRCH_RUST_MEM_GUARD_WARN.format(file_path))
                        return (Constants.STATUS_SKIPPED, AppStrings.SKIP_REASON_MEMORY_GUARD.format(m.content))
                    parts = m.content.split("\t", 1)
                    # [H-24] JSON 경로 정규화: /를 .으로 변경하고 시작 / 제거
                    json_path = parts[0].lstrip("/").replace("/", ".")
                    val = parts[1] if len(parts) > 1 else ""
                    processed.append((m.line, json_path, val, m.offset, m.length))
                return (file_path, len(processed), processed)
            return None
        except BaseException as e:
            logger.error(AppStrings.LOG_SCH_RUST_JSON_FAIL.format(file_path, e))
            # [Policy] 자동 폴백 중단
            return (Constants.STATUS_SKIPPED, AppStrings.ERROR_JSON_PARSE)
    try:
        import json

        try:
            file_size = os.path.getsize(file_path)
            if file_size > Constants.MAX_JSON_DOM_SIZE:
                logger.warning(AppStrings.LOG_SCH_JSON_LIMIT.format(file_path))
                return (Constants.STATUS_SKIPPED, AppStrings.SKIP_REASON_TOO_LARGE.format(f"{file_size} bytes"))
        except (OSError, IOError):
            pass
        raw_content, encoding = read_text_file_with_encoding(file_path)
        processed_content = raw_content
        # [무결성 정의] 아래 정규식은 자의적으로 사용하지 않도록 되어 있어 인라인 테스트가 위험할 수 있으므로
        # 반드시 정적인 조건에서만 실행해야 합니다. 어디 포함되었는지가 아니라 안전하게 처리합니다.
        # processed_content = re.sub(r",(\s*)([}\]])", r"\1\2", processed_content)
        try:
            data = json.loads(processed_content, strict=False)
        except json.JSONDecodeError:
            return (Constants.STATUS_SKIPPED, AppStrings.ERROR_JSON_PARSE)
        matches = []
        # 특수 문자(ß, İ 등)의 정규화를 위해 casefold() 사용
        search_string = normalize_unicode(search_string).casefold()
        # 재귀 DFS 대신 명시적 일반 반복문 Stack-based DFS 사용
        # 깊은 중첩 구조에서 RecursionError 발생을 사전 차단합니다.
        stack = [(data, "", 0)]  # (obj, path, depth)
        MAX_JSON_DEPTH = 2000
        total_count = 0
        _iter_count = 0  # independent iteration counter

        while stack:
            # Use _iter_count to check stop_event, not total_count.
            # total_count (%100) is always 0 when no matches, causing IPC call every step.
            _iter_count += 1
            if _iter_count % 1000 == 0 and stop_event and stop_event.is_set():
                break
            obj, path, depth = stack.pop()

            if depth >= MAX_JSON_DEPTH:
                continue

            if isinstance(obj, dict):
                for k, v in obj.items():
                    new_path = f"{path}.{k}" if path else str(k)
                    stack.append((v, new_path, depth + 1))
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                    new_path = f"{path}[{i}]" if path else f"[{i}]"
                    stack.append((v, new_path, depth + 1))
            else:
                val_raw = normalize_unicode(str(obj))
                val_comp = val_raw.casefold()
                is_match = (val_comp == search_string) if exact_match else (search_string in val_comp)
                if is_match:
                    total_count += 1
                    matches.append((1, path or "root", val_raw))

        if total_count > 0:
            return (file_path, total_count, matches)
        return None
    except Exception as e:
        logger.error(AppStrings.LOG_SCH_JSON_FAIL.format(e))
        return (Constants.STATUS_SKIPPED, str(e))


def search_in_xml_special(
    file_path: str,
    search_string: str,
    exact_match: bool = False,
    use_complex_search: bool = False,
    stop_event=None,
) -> Optional[Union[SearchResult, SkippedResult]]:
    """
    XML 특수 검색을 수행합니다.
    """
    if HAS_RUST_ENGINE and not use_complex_search:
        try:
            mode_bits = Constants.RUST_MODE_XML
            if exact_match:
                mode_bits |= Constants.RUST_MODE_EXACT
            results = sf_engine.search_file(str(file_path), search_string, mode_bits)
            if results:
                processed = []
                for m in results:
                    # 메모리 한도 확인
                    if "ERR_MEMORY_GUARD" in m.content:
                        return (Constants.STATUS_SKIPPED, AppStrings.SKIP_REASON_MEMORY_GUARD.format(m.content))
                    parts = m.content.split("\t", 1)
                    tag_info = parts[0]
                    content_val = parts[1] if len(parts) > 1 else ""
                    processed.append((m.line, tag_info, content_val, m.offset, m.length))
                return (file_path, len(processed), processed)
            return None
        except BaseException as e:
            logger.error(AppStrings.LOG_SCH_RUST_XML_FAIL.format(file_path, e))
            # [Policy] 자동 폴백 중단
            return (Constants.STATUS_SKIPPED, AppStrings.ERROR_XML_PARSE.format(e))
    try:
        import xml.parsers.expat

        # XML DOM 로딩 전 크기 확인 (JSON/Archive와 동일한 패턴)
        # 이유: 크기 제한 없이 전체 파일을 메모리에 로드 -> 대용량 XML에서 OOM 위험
        try:
            file_size = os.path.getsize(file_path)
            if file_size > Constants.MAX_JSON_DOM_SIZE:
                logger.warning(AppStrings.LOG_SCH_JSON_LIMIT.format(file_path))
                return (Constants.STATUS_SKIPPED, AppStrings.SKIP_REASON_TOO_LARGE.format(f"{file_size} bytes"))
        except (OSError, IOError):
            pass

        raw_content, encoding = read_text_file_with_encoding(file_path)
        processed_content = raw_content  # 주석 제거 기능 없음
        matches = []
        count = 0
        search_string = normalize_unicode(search_string).casefold()
        # stop_event 참조를 핸들러에 전달하기 위해 클로저로 캡처
        _stop_event = stop_event

        class XMLSearcher:
            def __init__(self):
                self.parser = xml.parsers.expat.ParserCreate()
                self.parser.StartElementHandler = self.start_element
                self.parser.CharacterDataHandler = self.char_data
                self.current_tags = []

            def start_element(self, name, attrs):
                nonlocal count
                # 중단 신호 확인 후 즉시 예외로 실제 종료
                if _stop_event and _stop_event.is_set():
                    raise xml.parsers.expat.ExpatError("User stopped")
                self.current_tags.append(name)
                for k, v in attrs.items():
                    val = normalize_unicode(str(v))
                    val_comp = val.casefold()
                    if (search_string in val_comp) if not exact_match else (search_string == val_comp):
                        count += 1
                        matches.append((self.parser.CurrentLineNumber, str(k), val))

            def char_data(self, data):
                nonlocal count
                text = normalize_unicode(data).strip()
                if text:
                    text_comp = text.casefold()
                    if (search_string in text_comp) if not exact_match else (search_string == text_comp):
                        count += 1
                        matches.append(
                            (
                                self.parser.CurrentLineNumber,
                                self.current_tags[-1] if self.current_tags else "root",
                                text,
                            )
                        )

        searcher = XMLSearcher()
        try:
            searcher.parser.Parse(processed_content, True)
        except xml.parsers.expat.ExpatError as xe:
            # stop_event에 의한 중단이 사용자 요청이므로 완료로 처리, 잔여 파싱 결과도 처리
            if _stop_event and _stop_event.is_set():
                return (Constants.STATUS_SKIPPED, AppStrings.LOG_SCH_STOPPED_BY_USER)
            logger.warning(AppStrings.LOG_SCH_XML_FAIL.format(xe))
            return (Constants.STATUS_SKIPPED, str(xe))
        if count > 0:
            return (file_path, count, matches)
        return None
    except Exception as e:
        logger.error(AppStrings.LOG_SCH_XML_FAIL.format(e))
        return (Constants.STATUS_SKIPPED, str(e))


def search_in_archive_special(
    file_path: str,
    search_string: str,
    exact_match: bool = False,
    use_complex_search: bool = False,
    stop_event=None,
) -> Optional[Union[SearchResult, SkippedResult]]:
    """
    Archive 특수 검색을 수행합니다.
    """
    if HAS_RUST_ENGINE and not use_complex_search:
        try:
            mode_bits = Constants.RUST_MODE_ARCHIVE
            if exact_match:
                mode_bits |= Constants.RUST_MODE_EXACT
            results = sf_engine.search_file(str(file_path), search_string, mode_bits)
            if results:
                processed = []
                for m in results:
                    # 메모리 한도 확인
                    if "ERR_MEMORY_GUARD" in m.content:
                        return (Constants.STATUS_SKIPPED, AppStrings.SKIP_REASON_MEMORY_GUARD.format(m.content))
                    parts = m.content.split("\t")
                    ns = parts[0].replace("NS: ", "") if len(parts) > 0 else ""
                    key = parts[1].replace("Key: ", "") if len(parts) > 1 else ""
                    src = parts[2].replace("S: ", "") if len(parts) > 2 else ""
                    trans = parts[3].replace("T: ", "") if len(parts) > 3 else ""
                    processed.append((m.line, ns, key, src, trans))
                return (file_path, len(processed), processed)
            return None
        except BaseException as e:
            logger.error(AppStrings.LOG_SCH_RUST_ARCHIVE_FAIL.format(file_path, e))
            # [Policy] 자동 폴백 중단
            return (Constants.STATUS_SKIPPED, AppStrings.ERROR_IO_DURING_SEARCH.format(e))
    try:
        import json

        # .archive 파일도 JSON DOM 방식 사용 (OOM 방지)
        try:
            f_size = os.path.getsize(file_path)
            if f_size > Constants.MAX_JSON_DOM_SIZE:
                logger.warning(AppStrings.LOG_SCH_JSON_LIMIT.format(file_path))
                return (Constants.STATUS_SKIPPED, AppStrings.SKIP_REASON_MEMORY_GUARD.format(f"{f_size} bytes"))
        except (OSError, IOError):
            pass
        raw_content, encoding = read_text_file_with_encoding(file_path)
        data = json.loads(raw_content)
        matches = []
        count = 0
        search_string = normalize_unicode(search_string).casefold()
        for sn in data.get("Subnamespaces", []):
            if stop_event and stop_event.is_set():
                break
            ns = sn.get("Namespace", "")
            for child in sn.get("Children", []):
                s = normalize_unicode(child.get("Source", {}).get("Text", ""))
                t = normalize_unicode(child.get("Translation", {}).get("Text", ""))
                s_comp = s.casefold()
                t_comp = t.casefold()
                if (
                    (search_string in s_comp or search_string in t_comp)
                    if not exact_match
                    else (search_string == s_comp or search_string == t_comp)
                ):
                    count += 1
                    matches.append((1, ns, child.get("Key", ""), s, t))
        if count > 0:
            return (file_path, count, matches)
        return None
    except Exception as e:
        logger.error(AppStrings.LOG_SCH_ARCHIVE_FAIL.format(e))
        return (Constants.STATUS_SKIPPED, str(e))


def search_in_file(
    file_path: str,
    search_string: str,
    file_size: Optional[int] = None,
    special_mode: Optional[str] = None,
    use_complex_search: bool = False,
    stop_event=None,
    force_python: bool = False,
    **kwargs,
) -> Optional[Union[SearchResult, SkippedResult]]:
    """search_in_file ?⑥닔."""
    search_string_nfc = normalize_unicode(search_string)
    ext = splitext(file_path)[1].lower()
    if ext in [".xlsx", ".xlsm", ".xls", ".xlsb"]:
        is_exact = False
        if special_mode and Constants.MODE_EXACT in special_mode:
            is_exact = True
        try:
            return search_in_excel_special(
                file_path, search_string_nfc, is_exact, use_complex_search=use_complex_search, stop_event=stop_event
            )
        except ImportError as e:
            logger.error(AppStrings.ERROR_EXCEL_LIB.format(e))
            raise RuntimeError(AppStrings.ERROR_EXCEL_CALAMINE_REQ)
        except Exception as e:
            logger.error(AppStrings.ERROR_SEARCH_EXCEL.format(file_path, e), exc_info=True)
            return (Constants.STATUS_SKIPPED, AppStrings.ERROR_SEARCH_EXCEL.format(file_path, e))
    if special_mode:
        is_exact = Constants.MODE_EXACT in special_mode
        if Constants.MODE_XML in special_mode:
            return search_in_xml_special(
                file_path, search_string_nfc, is_exact, use_complex_search, stop_event=stop_event
            )
        elif Constants.MODE_JSON in special_mode:
            return search_in_json_special(
                file_path, search_string_nfc, is_exact, use_complex_search, stop_event=stop_event
            )
        elif Constants.MODE_ARCHIVE in special_mode:
            return search_in_archive_special(
                file_path, search_string_nfc, is_exact, use_complex_search, stop_event=stop_event
            )
        elif Constants.MODE_EXCEL in special_mode:
            return search_in_excel_special(
                file_path, search_string_nfc, is_exact, use_complex_search=use_complex_search, stop_event=stop_event
            )
    try:
        if file_size is None:
            file_size = os.path.getsize(file_path)
        if file_size == 0:
            return (Constants.STATUS_SKIPPED, AppStrings.SKIP_EMPTY_FILE)
    except (OSError, IOError) as e:
        logger.debug(AppStrings.LOG_SCH_BINARY_CHECK_ERROR.format(file_path, e))
        return (Constants.STATUS_SKIPPED, AppStrings.ERROR_FILE_ACCESS_BINARY.format(e))
    # [엔진 선택 정책]
    # - 기본 검색: Rust 엔진(정규식/패턴/일반 특수자) 우선 사용 (빠른 처리)
    # - 복합검색(대소문자): '특별한 유니코드 문자' 옵션 활성화 시 Python 폴백으로
    # - 별도로 정의된 유니코드 검색 방식 차이의 성능/무결성 정책을 스레드에서 처리 (어디 포함되었는지)
    # 복합 검색(use_complex_search=True) 시 Rust 엔진의 Simple CaseFolding 정책
    # Rust 엔진 검색을 건너뛰고 바로 Python 폴백 엔진으로 진입합니다. (코드 리뷰 코드에 미반영)
    is_binary = is_binary_file(file_path) if not HAS_RUST_ENGINE or use_complex_search else False
    if HAS_RUST_ENGINE and not use_complex_search and not force_python:
        try:
            exclude_binary = bool(kwargs.get("exclude_binary", False))
            mode_bits = get_rust_mode_bits(special_mode, exclude_binary=exclude_binary)
            # search_file API 호출 (stop_event는 지원 여부에 따라 선택적 전달 필요할 수 있음)
            rust_results = sf_engine.search_file(str(file_path), search_string_nfc, mode_bits)
            if rust_results:
                normalized, binary_count = _normalize_rust_matches(rust_results, special_mode)
                if binary_count > 0:
                    return (
                        file_path,
                        binary_count,
                        [(1, AppStrings.MSG_BINARY_MATCH.format(binary_count), None, None)],
                    )
                if is_binary:
                    count = 0
                    for m in rust_results:
                        count += int(m.length) if (m.length is not None and m.length > 0) else 1
                    return (file_path, count, [(1, AppStrings.MSG_BINARY_MATCH.format(count), None, None)])
                return (file_path, len(normalized), normalized)

            # [무결성 정책] Rust가 결과를 찾지 못했을 때 Python 폴백으로 재시도합니다.
            return None
        except BaseException as e:
            # 상세 에러 로깅 (포맷 수정 반영: {path}: {error})
            logger.error(AppStrings.LOG_SCH_RUST_ENGINE_ERROR.format(file_path, e), exc_info=True)
            # [Policy] 자동 폴백 중단: 오류 발생 시 해당 파일은 스킵됨
            return (Constants.STATUS_SKIPPED, AppStrings.ERROR_UNEXPECTED_FILE.format(file_path, e))

    # [Policy] Python 엔진 진입 제한: '특별한 문자열 검색' 옵션이 켜져 있을 때만 Python 구동
    if not use_complex_search:
        return None

    try:
        if file_size is None:
            file_size = os.path.getsize(file_path)
        encoding = None
        if file_size < Constants.MAX_SMALL_FILE_SIZE:  # 10MB 상수 참조
            if not encoding:
                with open(file_path, "rb") as f_head:
                    head_data = f_head.read(65536)
                    encoding = detect_encoding_quickly(head_data)
            with open(file_path, "r", encoding=encoding, errors="replace") as f_text:
                content = f_text.read()
            processed_content = content  # 주석 제거 기능 없음
            if not use_complex_search:
                if search_string_nfc.casefold() not in normalize_unicode(processed_content).casefold():
                    return None
            matches = []
            count = 0
            lines = processed_content.splitlines()
            is_exact = bool(special_mode and Constants.MODE_EXACT in special_mode)
            search_fold = search_string_nfc.casefold()
            for i, line in enumerate(lines):
                if i % 1000 == 0 and stop_event and stop_event.is_set():
                    return Constants.STATUS_SKIPPED, AppStrings.LOG_SCH_STOPPED_BY_USER
                line_trimmed = line.strip()
                if is_exact:
                    if line_trimmed.casefold() == search_fold:
                        count += 1
                        matches.append((i + 1, line_trimmed))
                elif search_fold in line.casefold():
                    count += 1
                    matches.append((i + 1, line_trimmed))
            if count > 0:
                if is_binary:
                    return (file_path, count, [(1, AppStrings.MSG_BINARY_MATCH.format(count), None, None)])
                return (file_path, count, matches)
            return None
        with open(file_path, "rb") as f_bin:
            file_size_actual = os.fstat(f_bin.fileno()).st_size
            if file_size_actual == 0:
                return (Constants.STATUS_SKIPPED, AppStrings.SKIP_EMPTY_FILE)
            head = f_bin.read(4096)
            detected_enc = detect_encoding_quickly(head)
            if not detected_enc:
                detected_enc = "utf-8"
            current_line = 0
            matches = []
            count = 0
            is_exact = bool(special_mode and Constants.MODE_EXACT in special_mode)
            try:
                with open(file_path, "r", encoding=detected_enc, errors="replace") as f_text:
                    search_fold = search_string_nfc.casefold()
                    for line in f_text:
                        current_line += 1
                        if current_line % 1000 == 0 and stop_event and stop_event.is_set():
                            return Constants.STATUS_SKIPPED, AppStrings.LOG_SCH_STOPPED_BY_USER
                        line_trimmed = line.strip()
                        if is_exact:
                            if line_trimmed.casefold() == search_fold:
                                count += 1
                                matches.append((current_line, line_trimmed))
                        elif search_fold in line.casefold():
                            count += 1
                            matches.append((current_line, line_trimmed))
            except Exception as e:
                logger.debug(AppStrings.LOG_SCH_STREAM_ERROR.format(e))
                return (Constants.STATUS_SKIPPED, AppStrings.ERROR_IO_DURING_SEARCH.format(e))
            if count > 0:
                if is_binary:
                    return (file_path, count, [(1, AppStrings.MSG_BINARY_MATCH.format(count), None, None)])
                return (file_path, count, matches)
            return None
    except (IOError, OSError) as e:
        logger.debug(AppStrings.LOG_SCH_ERROR_FILE.format(file_path, e))
        return (Constants.STATUS_SKIPPED, AppStrings.ERROR_IO_DURING_SEARCH.format(e))
    except BaseException as e:  # Exception 위에 있는 PanicException 등 포착
        logger.error(AppStrings.LOG_SCH_UNEXPECTED_ERROR.format(AppStrings.HEADER_FILE, file_path, e), exc_info=True)
        return (Constants.STATUS_SKIPPED, AppStrings.ERROR_UNEXPECTED_FILE.format(file_path, e))
    return None


def search_in_files_batch(
    file_batch: List[FileInfo],
    search_string: str,
    special_mode: Optional[str] = None,
    use_complex_search: bool = False,
    stop_event=None,
    force_python: bool = False,
    **kwargs,
) -> Dict[str, List]:
    """search_in_files_batch ?⑥닔."""
    results = []
    skipped = []
    for f_path, f_size in file_batch:
        # [M-05] 프로세스 생존 확인: 부모 프로세스가 죽었으면 즉시 종료 (좀비 프로세스 방지)
        # pp가 None이면 현재 프로세스가 메인 프로세스이므로 건너뛴다.
        pp = multiprocessing.parent_process()
        if pp and not pp.is_alive():
            break

        if stop_event and hasattr(stop_event, "is_set") and stop_event.is_set():
            break
        res = search_in_file(
            f_path,
            search_string,
            f_size,
            special_mode,
            use_complex_search=use_complex_search,
            stop_event=stop_event,
            force_python=force_python,
            **kwargs,
        )
        if isinstance(res, tuple) and res[0] == Constants.STATUS_SKIPPED:
            skipped.append((f_path, res[1]))
        elif res == Constants.STATUS_SKIPPED:
            skipped.append((f_path, AppStrings.ERROR_UNKNOWN))
        elif res:
            results.append(res)
    return {"results": results, "skipped": skipped}


def search_directory_fast(
    search_paths: List[str],
    search_string: str,
    extensions: Optional[List[str]] = None,
    stop_event=None,
    progress_callback=None,
    is_boolean: bool = False,
    **kwargs,
) -> Dict[str, List]:
    """search_directory_fast ?⑥닔."""
    if not HAS_RUST_ENGINE:
        return {"results": [], "skipped": []}
    try:
        rust_pattern = normalize_unicode(search_string)
        rust_exts = None
        if extensions:
            rust_exts = [ext.lstrip(".").lower() for ext in extensions]
        filename_filter = kwargs.get("filename_filter")
        rust_fn_filters = None
        if filename_filter:
            if isinstance(filename_filter, str):
                rust_fn_filters = [f.strip() for f in filename_filter.split(",") if f.strip()]
            else:
                rust_fn_filters = filename_filter
        exclude_hidden = bool(kwargs.get("exclude_hidden", False))
        search_dir_func = getattr(sf_engine, "search_dir", None)
        if not search_dir_func:
            logger.error(AppStrings.LOG_SYS_SF_ENGINE_NOT_FOUND.format("sf_engine.search_dir"))
            # [Integrity] 엔진 특수 사용 시에도 사용자 설정 무시 않고 Python 폴백을 통해 재검색
            # (위에서 HAS_RUST_ENGINE 체크가 통과했음에도 엔진이 없는 경우여서 이러한 처리)
            raise AttributeError(AppStrings.LOG_SYS_SF_ENGINE_NOT_FOUND.format("sf_engine.search_dir"))
        exclude_binary = bool(kwargs.get("exclude_binary", False))
        mode_bits = get_rust_mode_bits(kwargs.get("special_mode"), exclude_binary=exclude_binary, is_boolean=is_boolean)
        raw_ret = search_dir_func(
            search_paths,
            rust_pattern,
            rust_exts,
            mode_bits,
            rust_fn_filters,
            exclude_hidden,
            stop_event,
            progress_callback,
            kwargs.get("results_callback"),
        )
        formatted_results = []
        skipped_results = []
        if raw_ret:
            matches_list, skipped_list = raw_ret
            for path, matches in matches_list:
                match_tuples, marker_binary_count = _normalize_rust_matches(matches, kwargs.get("special_mode"))
                if marker_binary_count > 0:
                    formatted_results.append(
                        (
                            path,
                            marker_binary_count,
                            [(1, AppStrings.MSG_BINARY_MATCH.format(marker_binary_count), None, None)],
                        )
                    )
                elif match_tuples:
                    formatted_results.append((path, len(match_tuples), match_tuples))
                else:
                    excel_skip_reason = _extract_excel_marker_skip_reason(matches)
                    if excel_skip_reason:
                        skipped_results.append((path, excel_skip_reason))
            for path, reason in skipped_list:
                skipped_results.append((path, format_skip_reason(reason)))
        return {"results": formatted_results, "skipped": skipped_results}
    except (IOError, OSError, RuntimeError) as e:
        logger.error(AppStrings.LOG_SCH_RUST_DIR_SEARCH_ERROR.format("SearchEngine.Directory", e))
        raise
    except BaseException as e:
        logger.critical(AppStrings.LOG_SCH_RUST_DIR_FATAL.format("SearchEngine.Directory", e), exc_info=True)
        raise


def search_files_list_fast(
    file_list: List[str],
    search_string: str,
    special_mode: Optional[str] = None,
    stop_event=None,
    progress_callback=None,
    **kwargs,
) -> Dict[str, List]:
    """search_files_list_fast ?⑥닔."""
    if not HAS_RUST_ENGINE or not file_list:
        return {"results": [], "skipped": []}
    try:
        rust_pattern = normalize_unicode(search_string)
        search_func = getattr(sf_engine, "search_files_list", None)
        if not search_func:
            # Rust API에서 인수 부락 시 타입 예외 발생 (없는 결과 반환 등)
            raise AttributeError(AppStrings.LOG_SYS_SF_ENGINE_NOT_FOUND.format("search_files_list"))
        exclude_hidden = bool(kwargs.get("exclude_hidden", False))
        # [Policy] 명시적으로 지정한 파일 리스트(file_list) 검색 시에는 exclude_binary 옵션과 무관하게 항상 검색을 수행함
        exclude_binary = False
        mode_bits = get_rust_mode_bits(special_mode, exclude_binary=exclude_binary)
        raw_ret = search_func(
            file_list,
            rust_pattern,
            mode_bits,
            exclude_hidden,
            stop_event,
            progress_callback,
            kwargs.get("results_callback"),
        )
        formatted_results = []
        skipped_results = []
        if raw_ret:
            matches_list, skipped_list = raw_ret
            for path, matches in matches_list:
                match_tuples, marker_binary_count = _normalize_rust_matches(matches, special_mode)
                if marker_binary_count > 0:
                    formatted_results.append(
                        (
                            path,
                            marker_binary_count,
                            [(1, AppStrings.MSG_BINARY_MATCH.format(marker_binary_count), None, None)],
                        )
                    )
                elif match_tuples:
                    formatted_results.append((path, len(match_tuples), match_tuples))
                else:
                    excel_skip_reason = _extract_excel_marker_skip_reason(matches)
                    if excel_skip_reason:
                        skipped_results.append((path, excel_skip_reason))
            for path, reason in skipped_list:
                skipped_results.append((path, format_skip_reason(reason)))
        return {"results": formatted_results, "skipped": skipped_results}
    except BaseException as e:
        logger.critical(AppStrings.LOG_SCH_RUST_FILELIST_FATAL.format("SearchEngine.FileList", e), exc_info=True)
        raise


@overload
def find_files_with_keyword_fast(
    search_paths: List[str],
    search_string: str,
    extensions: Optional[List[str]] = None,
    *,
    return_skipped: Literal[False] = False,
    **kwargs,
) -> List[FileInfo]: ...


@overload
def find_files_with_keyword_fast(
    search_paths: List[str],
    search_string: str,
    extensions: Optional[List[str]] = None,
    *,
    return_skipped: Literal[True],
    **kwargs,
) -> Tuple[List[FileInfo], List[SkippedResult]]: ...


def find_files_with_keyword_fast(
    search_paths: List[str],
    search_string: str,
    extensions: Optional[List[str]] = None,
    *,
    return_skipped: bool = False,
    is_boolean: bool = False,
    **kwargs,
) -> Union[List[FileInfo], Tuple[List[FileInfo], List[SkippedResult]]]:
    """find_files_with_keyword_fast ?⑥닔."""
    if not HAS_RUST_ENGINE or not search_paths:
        return ([], []) if return_skipped else []
    try:
        rust_pattern = normalize_unicode(search_string)
        rust_exts = None
        if extensions:
            rust_exts = [ext.lstrip(".").lower() for ext in extensions]
        filename_filter = kwargs.get("filename_filter")
        rust_fn_filters = None
        if filename_filter:
            if isinstance(filename_filter, str):
                rust_fn_filters = [f.strip() for f in filename_filter.split(",") if f.strip()]
            else:
                rust_fn_filters = filename_filter
        special_mode = kwargs.get("special_mode")
        exclude_hidden = bool(kwargs.get("exclude_hidden", False))
        stop_event = kwargs.get("stop_event")
        find_func = getattr(sf_engine, "find_files_with_keyword", None)
        if not find_func:
            logger.error(AppStrings.LOG_SYS_SF_ENGINE_NOT_FOUND.format("sf_engine.find_files_with_keyword"))
            # [Integrity] 특수 엔진 사용 시에도 결과 0이 아닌 오류라서 Python 폴백 시도
            raise AttributeError(AppStrings.LOG_SYS_SF_ENGINE_NOT_FOUND.format("sf_engine.find_files_with_keyword"))
        exclude_binary = bool(kwargs.get("exclude_binary", False))
        rust_mode_bits = get_rust_mode_bits(special_mode, exclude_binary=exclude_binary, is_boolean=is_boolean)
        try:
            found_ret = find_func(
                search_paths,
                rust_pattern,
                rust_exts,
                rust_mode_bits,
                rust_fn_filters,
                exclude_hidden,
                stop_event,
                kwargs.get("results_callback"),
            )
        except TypeError:
            # Log warning to prevent silent failure.
            # Older Rust engine builds may not accept stop_event argument.
            # Check sf_engine.API_VERSION if this warning appears.
            logger.warning(
                "[M-06] Rust find_files API rejected stop_event (TypeError). "
                "Check sf_engine API_VERSION. Retrying without stop_event."
            )
            found_ret = find_func(
                search_paths,
                rust_pattern,
                rust_exts,
                rust_mode_bits,
                rust_fn_filters,
                exclude_hidden,
                kwargs.get("results_callback"),
            )
        found_files: List[FileInfo] = []
        skipped_files: List[SkippedResult] = []
        if found_ret:
            found_raw, skipped_raw = found_ret
            found_files = list(found_raw)
            skipped_files = [(path, format_skip_reason(reason)) for path, reason in list(skipped_raw)]
        if skipped_files:
            for path, reason in skipped_files:
                logger.warning(AppStrings.LOG_SCH_SMART_SCAN_SKIPPED.format(path, reason))
        if return_skipped:
            return found_files, skipped_files
        return found_files
    except (IOError, OSError, RuntimeError) as e:
        logger.error(AppStrings.LOG_SCH_RUST_SMART_SCAN_ERROR.format("SearchEngine.SmartScan", e))
        reason = format_skip_reason(_build_skip_reason(SKIP_CODE_CRITICAL, e))
        if return_skipped:
            return [], [(AppStrings.ERROR_TITLE, reason)]
        return []
    except BaseException as e:
        logger.error(AppStrings.LOG_SCH_RUST_SMART_SCAN_ERROR.format("SearchEngine.SmartScan", e))
        reason = format_skip_reason(_build_skip_reason(SKIP_CODE_CRITICAL, e))
        if return_skipped:
            return [], [(AppStrings.ERROR_TITLE, reason)]
        return []
