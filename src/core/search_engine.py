import ctypes
import logging
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
    """Rust SearchMatch 媛앹껜瑜?UI 紐⑤뜽???쒗뵆濡?蹂?섑븯硫? ?뱀닔 紐⑤뱶 ?곗씠?곕? 援ъ“?뷀빀?덈떎."""
    line = getattr(match_obj, "line", 1)
    content = str(getattr(match_obj, "content", ""))
    offset = getattr(match_obj, "offset", None)
    length = getattr(match_obj, "length", None)

    # 1. ?쒖뒪??留덉빱 泥섎━ (諛붿씠?덈━, 湲?以?
    if content.startswith(RUST_MATCH_MARKER_BINARY):
        marker_count = content[len(RUST_MATCH_MARKER_BINARY) :]
        count = _parse_rust_binary_count(marker_count, length)
        return (1, AppStrings.MSG_BINARY_MATCH.format(count), None, None), count

    if content.startswith(RUST_MATCH_MARKER_LONG_LINE):
        preview = content[len(RUST_MATCH_MARKER_LONG_LINE) :]
        return (line, AppStrings.MSG_LONG_LINE_PREVIEW.format(preview), offset, length), None

    if content.startswith(RUST_MATCH_MARKER_EXCEL_SHEET_ERROR):
        # [Fix 1-D] ?묒? ?쒗듃 ?뚯떛 ?ㅻ쪟 留덉빱 泥섎━
        # ?댁쟾: (None, None, None, None) ?쒗뵆 諛섑솚 ???몄텧遺??'is not None' 泥댄겕瑜??듦낵?섏뿬 TypeError ?꾪뿕
        # ?섏젙: None 諛섑솚?쇰줈 caller?먯꽌 ?꾩쟾???쒖쇅
        error_msg = content[len(RUST_MATCH_MARKER_EXCEL_SHEET_ERROR) :]
        logger.warning(f"Excel sheet parse error: {error_msg}")
        return None, None

    if content.startswith(RUST_MATCH_MARKER_EXCEL_PANIC):
        # [Fix 1-D] ?묒? ?붿쭊 ?⑤땳 留덉빱 泥섎━ (None?쇰줈 蹂寃?
        ext = content[len(RUST_MATCH_MARKER_EXCEL_PANIC) :]
        logger.error(f"Excel engine panic: {ext}")
        return None, None

    # 2. ?뱀닔 紐⑤뱶 ?곗씠??援ъ“??(踰뚰겕 寃??寃곌낵??
    if special_mode:
        if Constants.MODE_ARCHIVE in special_mode:
            # [?? 援щ텇?먮? Tab(\t)?쇰줈 蹂寃쏀븯??" | "媛 ?ы븿???곗씠?곗쓽 臾닿껐??蹂댄샇
            parts = content.split("\t")
            ns = parts[0] if len(parts) > 0 else ""
            key = parts[1] if len(parts) > 1 else ""
            src = parts[2] if len(parts) > 2 else ""
            trans = parts[3] if len(parts) > 3 else ""
            return (line, ns, key, src, trans, offset, length), None

        elif Constants.MODE_EXCEL in special_mode:
            parts = content.split(" | ", 2)
            if len(parts) >= 3:
                # (Line, Sheet, Cell, Val, Offset, Length) 6-?쒗뵆 諛섑솚
                return (line, parts[0], parts[1], parts[2], offset, length), None

    return (line, content, offset, length), None


def _normalize_rust_matches(matches: Any, special_mode: Optional[str] = None) -> Tuple[List[SearchMatch], int]:
    """寃??寃곌낵 由ъ뒪???꾩껜瑜??뺢퇋?뷀빀?덈떎."""
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


def get_rust_mode_bits(special_mode: Optional[str]) -> int:
    """get_rust_mode_bits ?⑥닔."""
    bits = Constants.RUST_MODE_NORMAL
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
            # [Fix 3-C] 1KB -> 8KB濡??뺣?: PDF ??泥섏쓬 1KB ?댄썑??NUL???쒖옉?섎뒗 諛붿씠?덈━ ?ㅽ뙋 ?ㅼ?留?
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
    # [Fix 2-F] EUC-KR 嫄곗쭞?묒꽦 ?꾪솕: ?⑥닚 ?볦퐫???깃났???꾨땶 ?ㅼ젣 ?쒓?(AC00-D7A3) ?ы븿 ?쒖뿉留??먯젙
    # EUC-KR? 2諛붿씠??踰붿쐞媛 ?볦뼱 ?ㅻⅨ ?몄퐫??諛붿씠?덈━???깆떆瑜??듬줈 ?섑몴 -> ?쒓? 利?二쇱슂 肄붾뱶?ъ씤??蹂댁쑀 ?щ?濡??먯젙
    try:
        decoded_euckr = data.decode(Constants.ENC_EUCKR)
        if any('\uAC00' <= c <= '\uD7A3' for c in decoded_euckr):
            return Constants.ENC_EUCKR
    except UnicodeDecodeError:
        pass
    try:
        decoded_cp = data.decode(Constants.ENC_CP949)
        if any('\uAC00' <= c <= '\uD7A3' for c in decoded_cp):
            return Constants.ENC_CP949
    except UnicodeDecodeError:
        pass
    return Constants.ENC_UTF8


def read_text_file_with_encoding(file_path: str) -> Tuple[str, str]:
    """read_text_file_with_encoding ?⑥닔."""
    try:
        with open(file_path, "rb") as f:
            head = f.read(1024)
            encoding = detect_encoding_quickly(head)
        with open(file_path, "r", encoding=encoding, errors="replace") as f:
            content = f.read()
        return content, encoding
    except (IOError, OSError) as e:
        logger.debug(AppStrings.ERROR_READ_FILE.format(file_path, e))
        raise


def parse_excel_cell_address(address: str) -> Tuple[str, int, int]:
    """
    'Sheet1!B5' ?먮뒗 'B5' ?뺤떇??遺꾨━?섏뿬 (?쒗듃紐? ???몃뜳?? ???몃뜳??瑜?諛섑솚?⑸땲??
    ?몃뜳?ㅻ뒗 0遺???쒖옉?⑸땲??
    """
    sheet_name = ""
    cell_info = address
    if "!" in address:
        sheet_name, cell_info = address.split("!", 1)

    # ???뚰뙆踰?怨????レ옄) 遺꾨━
    import re

    match = re.match(r"([A-Z]+)([0-9]+)", cell_info.upper())
    if not match:
        return sheet_name, 0, 0

    col_str, row_str = match.groups()

    # ???몃뜳??怨꾩궛 (A=0, B=1, ..., Z=25, AA=26)
    col_idx = 0
    for char in col_str:
        col_idx = col_idx * 26 + (ord(char) - ord("A") + 1)
    col_idx -= 1

    row_idx = int(row_str) - 1
    return sheet_name, col_idx, row_idx


class FileScanner:
    """FileScanner ?대옒??"""

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
        # [Fix 3-B] ?뚯씪紐??꾪꽣 ?⑦꽩??__init__?먯꽌 ??踰덈쭔 ?꾩쿂由?
        # ?댁쟾: _scan_recursive 猷⑦봽?먯꽌 ?뚯씪留덈떎 由ъ뒪??而댄봽由ы뿨??諛섎났 ?ㅽ뻾 -> ?ㅻ쾭?ㅻ뱶
        # ?섏젙: ?앹꽦?먯뿉????踰?怨꾩궛 ???ъ궗??
        import fnmatch as _fnmatch
        self._fnmatch = _fnmatch
        self.processed_filename_filters: List[str] = [
            (f"*{f}*" if "*" not in f else f).lower()
            for f in self.filename_filters
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
                                    # [Fix 3-B] __init__?먯꽌 ?꾩쿂由щ맂 ?⑦꽩 ?ъ궗??(?뚯씪留덈떎 ?ъ깮???쒓굅)
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
    """[DEPRECATED] 寃??臾닿껐??蹂댁옣???꾪빐 鍮꾪솢?깊솕?? ?ν썑 ?쒓굅 ?덉젙."""
    # [Fix 2-G] 湲곕뒫 ?섎룄???쒓굅 ???⑥닔留??⑥븘 怨듦컻 ?몄텧 以?-> deprecated ?섏삁 諛쒖깮?쇰줈 ?ㅼ닔 ?ъ슜 諛⑹?
    warnings.warn(
        "strip_comments??鍮꾪솢?깊솕?섏뿀?듬땲?? ?ъ슜??以묐떒?섏꽭??",
        DeprecationWarning,
        stacklevel=2,
    )
    return content  # ?먮낯 諛섑솚 (?ㅼ젣 肄섑뀗痢?蹂???놁쓬)


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
                    parts = content.split(" | ", 2)
                    if len(parts) >= 3:
                        # [Fix 1-C] ?됰쾲?몃? 0 ?섎뱶肄붾뵫 ???Rust SearchMatch.line 媛??ъ슜
                        # ?댁쟾: (0, parts[0], ...) ??UI?먯꽌 ?됰쾲????긽 0 ?쒖떆
                        # ?섏젙: (m.line, parts[0], ...) ??Rust媛 蹂닿퀬?섎뒗 ?ㅼ젣 ?됰쾲??諛섏쁺
                        processed.append((m.line, parts[0], parts[1], parts[2]))
                    else:
                        processed.append((m.line, content, "", ""))
                if processed:
                    return (file_path, len(processed), processed)
                if sheet_errors:
                    return (Constants.STATUS_SKIPPED, sheet_errors[0])
            # [?붿쭊 ?뺤콉] Rust ?붿쭊?먯꽌 寃곌낵媛 ?놁쑝硫?利됱떆 醫낅즺?⑸땲??
            # ?대뒗 以묐났 ?뚯떛???곕Ⅸ ?깅뒫 ??섎? 諛⑹??섍린 ?꾪븳 ?섎룄???ㅺ퀎?낅땲??
            # ?? 蹂듯빀 寃???뺣? 鍮꾧탳) ?곹솴?먯꽌??Python ?대갚???덉슜?섏뿬 臾닿껐?깆쓣 蹂댁옣?⑸땲??
            return None
        except BaseException as e:  # Changed from Exception to BaseException
            logger.error(AppStrings.LOG_SCH_RUST_EXCEL_FAIL.format(e))
            logger.info(AppStrings.LOG_SYS_RUST_RUNTIME_FALLBACK)
    try:
        from python_calamine import CalamineWorkbook

        try:
            workbook = CalamineWorkbook.from_path(file_path)
        except (IOError, OSError) as e:
            return (Constants.STATUS_SKIPPED, AppStrings.ERROR_EXCEL_ACCESS.format(e))
        except BaseException as e:  # [Fix] PanicException ??C ?덈꺼 ?덉쇅源뚯? ?ъ갑
            return (Constants.STATUS_SKIPPED, AppStrings.ERROR_EXCEL_PROCESS.format(e))

        count = 0
        matches = []
        search_string_norm = re.sub(r"\s+", " ", search_string).strip()
        search_string_lower = search_string.casefold()

        for sheet_name in workbook.sheet_names:
            if stop_event and stop_event.is_set():
                break
            try:
                # [?깅뒫 理쒖쟻?? to_python() ??????⑥쐞 ?쒕꼫?덉씠?곕? ?ъ슜?섏뿬 硫붾え由??⑥쑉 洹밸???
                sheet = workbook.get_sheet_by_name(sheet_name)
                # [?덉젙?? iter_rows() ?몄텧 諛??쒗쉶 ?쒖젏?먯꽌 諛쒖깮?섎뒗 ?⑤땳 諛⑹뼱
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
                                # [Fix] offset_row 諛섏쁺?섏뿬 ?뺥솗???덈? 醫뚰몴 蹂닿퀬
                                abs_row = (
                                    row_idx + 1 + (sheet.start[0] if hasattr(sheet, "start") and sheet.start else 0)
                                )
                                matches.append((0, sheet_name, f"{col_letter}{abs_row}", val_str))
            except BaseException as e:  # [Fix] ?뱀젙 ?쒗듃?먯꽌 ?⑤땳 諛쒖깮 ???대떦 ?쒗듃留??ㅽ궢?섍퀬 濡쒓렇 湲곕줉
                sheet_err_msg = AppStrings.ERROR_SEARCH_EXCEL_SHEET.format(sheet_name, e)
                logger.error(AppStrings.LOG_SCH_ERROR_FILE.format(file_path, sheet_err_msg))
                continue

        if count > 0:
            return (file_path, count, matches)
    except ImportError:
        return (Constants.STATUS_SKIPPED, AppStrings.ERROR_EXCEL_CALAMINE)
    except BaseException as e:  # [Fix] ?꾩껜 ?묒? 泥섎━ 以?諛쒖깮?섎뒗 紐⑤뱺 移섎챸???ㅻ쪟 ?ъ갑
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
    JSON ?뱀닔 寃?됱쓣 ?섑뻾?⑸땲??
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
                    # [?? 硫붾え由?媛???좏샇 ?뺤씤 (留ㅼ튂濡??ㅼ씤 諛⑹?)
                    if "ERR_MEMORY_GUARD" in m.content:
                        logger.warning(AppStrings.LOG_SRCH_RUST_MEM_GUARD_WARN.format(file_path))
                        return (Constants.STATUS_SKIPPED, AppStrings.SKIP_REASON_MEMORY_GUARD.format(m.content))
                    parts = m.content.split(" | ", 1)
                    json_path = parts[0]
                    val = parts[1] if len(parts) > 1 else ""
                    processed.append((m.line, json_path, val, m.offset, m.length))
                return (file_path, len(processed), processed)
            return None
        except BaseException as e:
            logger.error(AppStrings.LOG_SCH_RUST_JSON_FAIL.format(e))
            logger.info(AppStrings.LOG_SYS_RUST_RUNTIME_FALLBACK)
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
        # [臾닿껐??二쇱쓽] ?꾨옒 ?뺢퇋?앹? 臾몄옄???대????댁슜源뚯? 蹂?뺤떆???꾪뿕???덉쑝誘濡??ъ슜??吏?묓븯嫄곕굹
        # 留ㅼ슦 ?꾧꺽??援ъ“??臾몃㎘?먯꽌留??섑뻾?댁빞 ?⑸땲?? ?몃? 由ы룷??沅뚭퀬???곕씪 ?덉쟾?섍쾶 泥섎━?⑸땲??
        # processed_content = re.sub(r",(\s*)([}\]])", r"\1\2", processed_content)
        try:
            data = json.loads(processed_content, strict=False)
        except json.JSONDecodeError:
            return (Constants.STATUS_SKIPPED, AppStrings.ERROR_JSON_PARSE)
        matches = []
        # [以? 援?젣 臾몄옄(횩, 캅 ?? ?뺣? 寃?됱쓣 ?꾪빐 casefold() ?ъ슜
        search_string = normalize_unicode(search_string).casefold()
        # [Fix] ?ш? DFS ???紐낆떆???ㅽ깮 湲곕컲 諛섎났臾?Stack-based DFS) ?ъ슜
        # 源딆? 以묒꺽 援ъ“?먯꽌 RecursionError 諛쒖깮???먯쿇 李⑤떒?⑸땲??
        stack = [(data, "", 0)]  # (obj, path, depth)
        MAX_JSON_DEPTH = 2000
        total_count = 0

        while stack:
            # [Fix 3-D] stop_event 泥댄겕瑜?留??댄꽣?덉씠?섏뿉???섏? ?딄퀬 100嫄??⑥쐞濡?-> IPC ?몄텧 ?ㅻ쾭?ㅻ뱶 媛먯냼
            total_count_check = total_count  # 濡쒖뻗 蹂?섎줈 罹먯떆
            if total_count_check % 100 == 0 and stop_event and stop_event.is_set():
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
    XML ?뱀닔 寃?됱쓣 ?섑뻾?⑸땲??
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
                    # [?? 硫붾え由?媛???좏샇 ?뺤씤
                    if "ERR_MEMORY_GUARD" in m.content:
                        return (Constants.STATUS_SKIPPED, AppStrings.SKIP_REASON_MEMORY_GUARD.format(m.content))
                    parts = m.content.split(" | ", 1)
                    tag_info = parts[0]
                    content_val = parts[1] if len(parts) > 1 else ""
                    processed.append((m.line, tag_info, content_val, m.offset, m.length))
                return (file_path, len(processed), processed)
            return None
        except BaseException as e:
            logger.error(AppStrings.LOG_SCH_XML_FAIL.format(e))
            logger.info(AppStrings.LOG_SYS_RUST_RUNTIME_FALLBACK)
    try:
        import xml.parsers.expat

        # [Fix 2-A] XML DOM ?ш린 媛??異붽? (JSON/Archive? ?숈씪???⑦꽩)
        # 湲곗〈: ?ш린 ?쒗븳 ?놁씠 ?꾩껜 ?뚯씪??硫붾え由ъ뿉 濡쒕뱶 -> ??⑸웾 XML?먯꽌 OOM ?꾪뿕
        try:
            file_size = os.path.getsize(file_path)
            if file_size > Constants.MAX_JSON_DOM_SIZE:
                logger.warning(AppStrings.LOG_SCH_JSON_LIMIT.format(file_path))
                return (Constants.STATUS_SKIPPED, AppStrings.SKIP_REASON_TOO_LARGE.format(f"{file_size} bytes"))
        except (OSError, IOError):
            pass

        raw_content, encoding = read_text_file_with_encoding(file_path)
        processed_content = raw_content  # 二쇱꽍 ?쒓굅 湲곕뒫 ??젣
        matches = []
        count = 0
        search_string = normalize_unicode(search_string).casefold()
        # [Fix 2-A] stop_event 李몄“瑜??몃뱾???대?濡??꾨떖?섍린 ?꾪빐 ?대줈?濡?罹≪쿂
        _stop_event = stop_event

        class XMLSearcher:
            def __init__(self):
                self.parser = xml.parsers.expat.ParserCreate()
                self.parser.StartElementHandler = self.start_element
                self.parser.CharacterDataHandler = self.char_data
                self.current_tags = []

            def start_element(self, name, attrs):
                nonlocal count
                # [Fix 2-A] 以묐떒 ?좏샇 ?뺤씤 ???뚯꽌 媛뺤젣 醫낅즺
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
            # [Fix 2-A] stop_event濡??명븳 以묐떒?대㈃ 議곗슜??醫낅즺, 洹????뚯떛 ?ㅻ쪟???ㅽ궢 泥섎━
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
    Archive ?뱀닔 寃?됱쓣 ?섑뻾?⑸땲??
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
                    # [?? 硫붾え由?媛???좏샇 ?뺤씤
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
            logger.error(AppStrings.LOG_SCH_ARCHIVE_FAIL.format(e))
            logger.info(AppStrings.LOG_SYS_RUST_RUNTIME_FALLBACK)
    try:
        import json

        # [?덉젙?? .archive ?뚯씪?먮룄 JSON DOM 媛???곸슜 (OOM 諛⑹?)
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
            return search_in_xml_special(file_path, search_string_nfc, is_exact, use_complex_search, stop_event=stop_event)
        elif Constants.MODE_JSON in special_mode:
            return search_in_json_special(file_path, search_string_nfc, is_exact, use_complex_search, stop_event=stop_event)
        elif Constants.MODE_ARCHIVE in special_mode:
            return search_in_archive_special(file_path, search_string_nfc, is_exact, use_complex_search, stop_event=stop_event)
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
    # [?붿쭊 ?좏깮 ?뺤콉]
    # - 湲곕낯 寃?? Rust ?붿쭊(?쒓?/?곷Ц/?쇰컲 ?뱀닔臾몄옄) ?곗꽑 ?ъ슜 (怨좎냽 泥섎━)
    # - 援?젣臾몄옄(?낆씪?? ?고궎???? ?뺣? 寃?? '?밸퀎??臾몄옄??寃?? ?듭뀡 ?쒖꽦????Python ?대갚 寃쎈줈 ?ъ슜
    # - ??寃쎈줈 媛꾩쓽 ?좊땲肄붾뱶 媛怨?諛⑹떇 李⑥씠???깅뒫/?뺣???媛꾩쓽 ?뺤콉???몃젅?대뱶?ㅽ봽??(?몃? 由ы룷??沅뚭퀬)
    # [?섏젙] 蹂듯빀 寃??use_complex_search=True) ??Rust ?붿쭊??Simple CaseFolding ?뺤콉???섑븳 寃곌낵 ?꾨씫??諛⑹??섍린 ?꾪빐
    # Rust ?붿쭊 寃?됱쓣 嫄대꼫?곌퀬 諛붾줈 Python ?뺣? ?붿쭊?쇰줈 吏꾩엯?⑸땲?? (?몃? 由щ럭 ?쇰뱶諛?諛섏쁺)
    is_binary = is_binary_file(file_path) if not HAS_RUST_ENGINE or use_complex_search else False
    if HAS_RUST_ENGINE and not use_complex_search:
        try:
            mode_bits = get_rust_mode_bits(special_mode)
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

            # [臾닿껐???뺤콉] Rust媛 寃곌낵瑜?李얠? 紐삵뻽????Python ?대갚 ?щ?瑜?寃곗젙?⑸땲??
            # 蹂듯빀 寃???듭뀡??爰쇱졇 ?덈떎硫??깅뒫???꾪빐 利됱떆 醫낅즺?섏?留? ?좊땲肄붾뱶 ?뺣??꾧? ?꾩슂??
            # 蹂듯빀 寃???곹솴?먯꽌??臾댁“嫄?Python ?붿쭊源뚯? 寃?됲븯??寃곌낵瑜?蹂댁옣?⑸땲??
            # (?꾩옱 釉붾줉? !use_complex_search ?곹솴?대?濡?利됱떆 None 諛섑솚)
            return None
        except BaseException as e:
            logger.error(AppStrings.LOG_SCH_RUST_ENGINE_ERROR.format(file_path, e))
            logger.info(AppStrings.LOG_SYS_RUST_RUNTIME_FALLBACK)
    try:
        if file_size is None:
            file_size = os.path.getsize(file_path)
        encoding = None
        if file_size < 10 * 1024 * 1024:
            if not encoding:
                with open(file_path, "rb") as f_head:
                    head_data = f_head.read(65536)
                    encoding = detect_encoding_quickly(head_data)
            with open(file_path, "r", encoding=encoding, errors="replace") as f_text:
                content = f_text.read()
            processed_content = content  # 二쇱꽍 ?쒓굅 湲곕뒫 ??젣
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
    except BaseException as e:  # [Fix] Exception 踰붿쐞瑜?踰쀬뼱?섎뒗 PanicException ??紐⑤뱺 ?섏쐞 ?붿쭊 ?⑤땳 李⑤떒
        logger.error(AppStrings.LOG_SCH_UNEXPECTED_ERROR.format(AppStrings.HEADER_FILE, file_path, e), exc_info=True)
        return (Constants.STATUS_SKIPPED, AppStrings.ERROR_UNEXPECTED_FILE.format(file_path, e))
    return None


def search_in_files_batch(
    file_batch: List[FileInfo],
    search_string: str,
    special_mode: Optional[str] = None,
    use_complex_search: bool = False,
    stop_event=None,
) -> Dict[str, List]:
    """search_in_files_batch ?⑥닔."""
    results = []
    skipped = []
    for f_path, f_size in file_batch:
        if stop_event and stop_event.is_set():
            break
        res = search_in_file(
            f_path,
            search_string,
            f_size,
            special_mode,
            use_complex_search=use_complex_search,
            stop_event=stop_event,
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
            # [Integrity] ?붿쭊 ?⑥닔 誘몄〈????議곗슜???ㅽ뙣?섏? ?딄퀬 Python ?대갚???꾪빐 鍮?寃곌낵瑜?諛섑솚?섏? ?딆쓬
            # (?곸쐞?먯꽌 HAS_RUST_ENGINE 泥댄겕瑜??듦낵?덉쓬?먮룄 ?⑥닔媛 ?녿뒗 寃쎌슦?대?濡??쇱씠釉뚮윭由??먯긽 媛?μ꽦)
            raise AttributeError(AppStrings.LOG_SYS_SF_ENGINE_NOT_FOUND.format("sf_engine.search_dir"))
        mode_bits = get_rust_mode_bits(kwargs.get("special_mode"))
        raw_ret = search_dir_func(
            search_paths,
            rust_pattern,
            rust_exts,
            mode_bits,
            rust_fn_filters,
            exclude_hidden,
            stop_event,
            progress_callback,
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
        logger.error(AppStrings.LOG_SCH_RUST_DIR_SEARCH_ERROR.format(e))
        raise
    except BaseException as e:
        logger.critical(AppStrings.LOG_SCH_RUST_DIR_FATAL.format(e), exc_info=True)
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
            # [Fix] Rust API ?쇰? ?꾨씫 ???섏씠 ?섏? ?덉쇅 諛쒖깮 (鍮?寃곌낵 諛섑솚 諛⑹?)
            raise AttributeError(AppStrings.LOG_SYS_SF_ENGINE_NOT_FOUND.format("search_files_list"))
        exclude_hidden = bool(kwargs.get("exclude_hidden", False))
        mode_bits = get_rust_mode_bits(special_mode)
        raw_ret = search_func(file_list, rust_pattern, mode_bits, exclude_hidden, stop_event, progress_callback)
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
        logger.critical(AppStrings.LOG_SCH_RUST_FILELIST_FATAL.format(e), exc_info=True)
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
            # [Integrity] ?⑥닔 誘몄〈????寃??寃곌낵 0嫄?????먮윭瑜?諛쒖깮?쒖폒 Python ?대갚 ?좊룄
            raise AttributeError(AppStrings.LOG_SYS_SF_ENGINE_NOT_FOUND.format("sf_engine.find_files_with_keyword"))
        rust_mode_bits = 0
        if special_mode:
            if Constants.MODE_JSON in special_mode:
                rust_mode_bits |= Constants.RUST_MODE_JSON
            elif Constants.MODE_XML in special_mode:
                rust_mode_bits |= Constants.RUST_MODE_XML
            elif Constants.MODE_ARCHIVE in special_mode:
                rust_mode_bits |= Constants.RUST_MODE_ARCHIVE
            elif Constants.MODE_EXCEL in special_mode:
                rust_mode_bits |= Constants.RUST_MODE_EXCEL
            if Constants.MODE_EXACT in special_mode:
                rust_mode_bits |= Constants.RUST_MODE_EXACT
        try:
            found_ret = find_func(
                search_paths,
                rust_pattern,
                rust_exts,
                rust_mode_bits,
                rust_fn_filters,
                exclude_hidden,
                stop_event,
            )
        except TypeError:
            found_ret = find_func(
                search_paths,
                rust_pattern,
                rust_exts,
                rust_mode_bits,
                rust_fn_filters,
                exclude_hidden,
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
        logger.error(AppStrings.LOG_SCH_RUST_SMART_SCAN_ERROR.format(e))
        reason = format_skip_reason(_build_skip_reason(SKIP_CODE_CRITICAL, e))
        if return_skipped:
            return [], [(AppStrings.ERROR_TITLE, reason)]
        return []
    except BaseException as e:
        logger.error(AppStrings.LOG_SCH_RUST_SMART_SCAN_ERROR.format(e))
        reason = format_skip_reason(_build_skip_reason(SKIP_CODE_CRITICAL, e))
        if return_skipped:
            return [], [(AppStrings.ERROR_TITLE, reason)]
        return []

