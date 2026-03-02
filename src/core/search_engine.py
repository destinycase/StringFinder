import ctypes
import json
import logging
import mmap
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
    from rust_engine import sf_engine  # type: ignore
    import hashlib

    REQUIRED_API_VERSION = 5
    engine_version = getattr(sf_engine, "API_VERSION", 0)
    if engine_version < REQUIRED_API_VERSION:
        logger.error(AppStrings.LOG_SYS_SF_ENGINE_NOT_FOUND.format("Compatible API"))
        HAS_RUST_ENGINE = False
    else:
        HAS_RUST_ENGINE = True

        # 로드된 엔진의 물리 경로 및 해시 무결성 로깅
        engine_path = getattr(sf_engine, "__file__", "unknown")
        engine_hash = "unknown"
        if engine_path != "unknown" and os.path.exists(engine_path):
            try:
                with open(engine_path, "rb") as f:
                    file_hash = hashlib.sha256()
                    while chunk := f.read(8192):
                        file_hash.update(chunk)
                engine_hash = file_hash.hexdigest()
            except Exception as e:
                engine_hash = f"error: {e}"

        logger.info(AppStrings.LOG_SYS_RUST_PATH.format(engine_path))
        logger.info(AppStrings.LOG_SYS_RUST_HASH.format(engine_hash))
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
            if code == "ERR_MAP": # Rust 구버전/오타 호환성 유지
                code = "ERR_MMAP"
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
        except Exception as e:
            logger.debug(AppStrings.LOG_SCH_BINARY_COUNT_PARSE_FAIL.format(length, e))
    return 1


def _normalize_rust_matches(
    matches: List[Any], 
    special_mode: Optional[str] = None, 
    existence_only: bool = False
) -> Tuple[List[Any], int]:
    """Rust 엔진의 반환 결과를 정규화된 매니 튜플 리스트와 바이너리 매치 카운트로 변환합니다."""
    res: List[Any] = []
    bin_cnt = 0
    last_c = ""

    for m in matches:
        try:
            # Rust 엔진은 (라인, 내용, 오프셋, 길이)를 반환합니다.
            c = m[1]
        except (IndexError, TypeError):
            # 이전 객체 스타일 매치에 대한 폴백 처리입니다.
            c = getattr(m, "content", "")

        if c == "__SF_SAME_LINE__":
            c = last_c
        else:
            last_c = c

        # 내부 마커 처리 (LONG_LINE 포함)
        if c.startswith("__SF_"):
            if c.startswith("__SF_LONG_LINE__|"):
                c = AppStrings.MSG_LONG_LINE_PREVIEW.format(c[len("__SF_LONG_LINE__|"):])
            elif c.startswith(RUST_MATCH_MARKER_BINARY):
                try:
                    bin_cnt += int(c.split("|")[-1])
                except Exception:
                    bin_cnt += 1
                continue
            elif c.startswith("ERR_") and "|" in c:
                continue
            elif not (existence_only and c == "MATCH"):
                continue

        if existence_only:
            return [(m[0], AppStrings.BOOLEAN_SEARCH_MATCH_CONTENT, m[2] if len(m) > 2 else None, m[3] if len(m) > 3 else None)], 0

        try:
            line   = m[0]
            offset = m[2] if len(m) > 2 else None
            length = m[3] if len(m) > 3 else None

            # Rust 엔진은 특수 모드(XML, JSON, Archive)에서
            # content 필드에 모든 필드를 \t로 합쳐서 반환함.
            # search_directory_fast 경로에서는 search_in_xml_special 등이
            # 호출되지 않으므로 이 함수에서 직접 분리해야 함.
            if special_mode and "\t" in c:
                mode_up = str(special_mode).upper()

                if "XML" in mode_up:
                    # 예) "/typesystem/ns/@name\tQtVideo" -> (line, "typesystem > ns > @name", "QtVideo", offset, length)
                    pts = c.split("\t", 1)
                    tag_path = pts[0].lstrip("/").replace("/", " > ")
                    val = pts[1] if len(pts) > 1 else ""
                    res.append((line, tag_path, val, offset, length))
                    continue

                if "JSON" in mode_up:
                    # 예) "/root/key\tvalue" -> (line, "root.key", "value", offset, length)
                    pts = c.split("\t", 1)
                    json_path = pts[0].lstrip("/").replace("/", ".")
                    val = pts[1] if len(pts) > 1 else ""
                    res.append((line, json_path, val, offset, length))
                    continue

                if "ARCHIVE" in mode_up:
                    # 예) "NS: ns\tKey: key\tS: src\tT: trans" -> (line, ns, key, src, trans)
                    pts = c.split("\t")
                    ns    = pts[0].replace("NS: ", "")  if len(pts) > 0 else ""
                    key   = pts[1].replace("Key: ", "") if len(pts) > 1 else ""
                    src   = pts[2].replace("S: ", "")   if len(pts) > 2 else ""
                    trans = pts[3].replace("T: ", "")   if len(pts) > 3 else ""
                    res.append((line, ns, key, src, trans))
                    continue

                if "EXCEL" in mode_up:
                    # 예) "시트\t셀\t값" -> (line, sheet, cell, val, offset, length)
                    # set_matches Case1이 m[1]=sheet, m[2]=cell, m[3]=val로 매핑함
                    pts = c.split("\t", 2)
                    if len(pts) >= 3:
                        sheet, cell, val = pts[0], pts[1], pts[2]
                    elif len(pts) == 2:
                        sheet, cell, val = pts[0], pts[1], ""
                    else:
                        sheet, cell, val = pts[0], "", ""
                    res.append((line, sheet, cell, val, offset, length))
                    continue

            # 기본: (line, content, offset, length)
            res.append((line, c, offset, length))
        except (IndexError, TypeError):
            res.append((getattr(m, "line", 1), c, getattr(m, "offset", None), getattr(m, "length", None)))

    return res, bin_cnt


def _extract_marker_skip_reason(matches: Any) -> Optional[str]:
    """엑셀 마커 전용 결과뿐만 아니라, 모든 모드의 일반 오류를 스킵 사유로 추출합니다."""
    for m in matches:
        try:
            # m: (line, content, offset, length)
            content = str(m[1])
        except (IndexError, TypeError):
            content = str(getattr(m, "content", ""))
            
        if content.startswith(RUST_MATCH_MARKER_EXCEL_PANIC):
            panic_detail = content[len(RUST_MATCH_MARKER_EXCEL_PANIC) :].strip() or "unknown"
            return AppStrings.ERROR_EXCEL_PANIC.format(panic_detail)
        # 엑셀 시트 파싱 에러(sheet error)는 파일 전체를 건너뛰는 사유가 아닙니다.
        # 이 마커가 오더라도 다른 시트의 정상 매치 데이터가 존재할 수 있으므로 파일 전체를 스킵하지 않습니다.
        if content.startswith(RUST_MATCH_MARKER_EXCEL_SHEET_ERROR):
            continue
        # 일반 마커 처리
        if content.startswith("ERR_") and "|" in content:
            return format_skip_reason(content)
    return None


def normalize_unicode(text: Any) -> str:
    """입력 텍스트를 NFC 유니코드 정규화 형식으로 변환하여 반환합니다."""
    if text is None:
        return ""
    return unicodedata.normalize("NFC", str(text))


def is_hidden_windows(path: str) -> bool:
    """Windows 전용: 파일 또는 폴더의 숨김 속성 여부를 반환합니다."""
    if os.name != "nt":
        return False
    try:
        attrs = ctypes.windll.kernel32.GetFileAttributesW(path)
        return attrs != -1 and bool(attrs & 0x02)
    except Exception:
        return False


def get_rust_mode_bits(special_mode: Optional[str], exclude_binary: bool = False, existence_only: bool = False) -> int:
    """검색 옵션에 따른 Rust 엔진 모드 비트 플래그를 생성하여 반환합니다."""
    bits = Constants.RUST_MODE_NORMAL
    if exclude_binary:
        bits |= Constants.RUST_MODE_EXCLUDE_BINARY
    if existence_only:
        bits |= Constants.RUST_MODE_EXISTENCE_ONLY

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
    """파일의 처음 8KB를 읽어 NUL 바이트 포함 여부로 바이너리 여부를 판별합니다."""
    try:
        with open(file_path, "rb") as f:
            # 헤드 읽기 범위를 8KB로 확대하여 PDF 파일 등의 바이너리 판독 정확도를 높였습니다.
            chunk = f.read(8192)
            return b"\x00" in chunk
    except (IOError, OSError, PermissionError):
        return False
    except Exception as e:
        logger.debug(AppStrings.LOG_SCH_BINARY_CHECK_FAIL.format(file_path, e))
        return False


def detect_encoding_quickly(data: bytes) -> str:
    """바이트 데이터의 BOM 및 패턴 기반 빠른 인코딩 감지를 수행합니다."""
    if not data:
        return Constants.ENC_UTF8
    if data.startswith(b"\xef\xbb\xbf"):
        return Constants.ENC_UTF8_SIG
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        # utf-16 사용 시 BOM에 따라 LE/BE 자동 감지 및 BOM 제거 수행
        return "utf-16"
    
    # UTF-16 No-BOM 휴리스틱 감지
    # 1. 시그니처 기반 (JSON/XML 등)
    if len(data) >= 2:
        if data.startswith(b"\x00{") or data.startswith(b"\x00<") or data.startswith(b"\x00["):
            return "utf-16-be"
        if data[0:2] in [b"{\x00", b"<\x00", b"[\x00"]:
            return "utf-16-le"
            
    # 2. 분포 기반 (NUL 바이트가 한쪽 인덱스에 압도적으로 많은지 확인)
    # 한글 등 유니코드 포함 시 모든 ODD/EVEN이 NUL이 아닐 수 있으므로 비율(80% 이상)로 판정
    if len(data) >= 8:
        sample = data[:min(len(data), 2048)]
        even_nuls = sum(1 for i in range(0, len(sample), 2) if sample[i] == 0)
        odd_nuls = sum(1 for i in range(1, len(sample), 2) if sample[i] == 0)
        total_pairs = len(sample) // 2
        if total_pairs > 5:
            if even_nuls > total_pairs * 0.8 and odd_nuls < total_pairs * 0.2:
                return "utf-16-be"
            if odd_nuls > total_pairs * 0.8 and even_nuls < total_pairs * 0.2:
                return "utf-16-le"

    try:
        data.decode(Constants.ENC_UTF8)
        return Constants.ENC_UTF8
    except UnicodeDecodeError:
        pass
    # EUC-KR 오탐 방지: 단순 디코딩 성공 여부뿐만 아니라 실제 한글 범위(AC00-D7A3) 문자가 포함되었는지 확인합니다.
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

def _fast_existence_check(
    file_path: str, search_string: str, exact_match: bool = False, raw_data: Optional[bytes] = None, encoding: Optional[str] = None
) -> bool:
    """
    존재 여부만 확인(existence_only) 모드에서 DOM 파싱 전 고속 필터링(Negative Filter)을 수행합니다.
    인코딩 인자를 수용하고 유니코드 정규화를 보완하여 중복 감지를 방지합니다.
    """
    try:
        # 1. 인코딩 감지 및 데이터 준비
        if encoding is None:
            if raw_data is not None:
                head = raw_data[:65536]
                encoding = detect_encoding_quickly(head)
            else:
                with open(file_path, "rb") as f:
                    # mmap을 활용하여 고속으로 바이트 검색(Negative Filter)을 수행합니다.
                    f_size = os.fstat(f.fileno()).st_size
                    if f_size >= 1024 * 1024:  # 1MB 이상 시 mmap 사용
                        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
                        try:
                            # 1. 인코딩 감지를 위한 헤드 추출 (mmap 직접 슬라이싱은 복사 발생, 64KB 수준은 무방)
                            head = mm[:65536]
                            encoding = detect_encoding_quickly(head)
                            
                            # 2. 인코딩을 고려한 바이트 검색(Negative Filter)
                            # UTF-16인 경우 BOM 중복 포함으로 인한 매치 누락을 방지합니다.
                            try:
                                if encoding == "utf-16":
                                    # 파일 헤드의 BOM에 따라 명시적 LE/BE 선택 (BOM 접두사 제거)
                                    if head.startswith(b"\xff\xfe"):
                                        search_bytes = search_string.encode("utf-16-le", errors="ignore")
                                    else:
                                        search_bytes = search_string.encode("utf-16-be", errors="ignore")
                                else:
                                    search_bytes = search_string.encode(encoding or "utf-8", errors="ignore")
                            except (UnicodeEncodeError, LookupError):
                                search_bytes = search_string.encode("utf-8", errors="ignore")
                                
                            if mm.find(search_bytes) == -1:
                                return False
                        finally:
                            mm.close()
                    else:
                        head = f.read(65536)
                        encoding = detect_encoding_quickly(head)
                        
                        # 소형 파일에 대해서도 mmap 미사용 시 즉각적인 Negative Filter 수행
                        # search_bytes 생성 (중복 인코딩 방지 로직 공유)
                        search_bytes = None
                        try:
                            if encoding == "utf-16":
                                if head.startswith(b"\xff\xfe"):
                                    search_bytes = search_string.encode("utf-16-le", errors="ignore")
                                else:
                                    search_bytes = search_string.encode("utf-16-be", errors="ignore")
                            else:
                                search_bytes = search_string.encode(encoding or "utf-8", errors="ignore")
                        except (UnicodeEncodeError, LookupError):
                            search_bytes = search_string.encode("utf-8", errors="ignore")

                        # Negative Filter의 적용 범위를 보정합니다.
                        # head가 파일 전체(65KB 이하)인 경우에만 필터링 적용.
                        # 파일 크기 > 65KB 이면 head에 없어도 뒷부분에 검색어가 있을 수 있으므로 건너뜀.
                        file_size_actual = os.fstat(f.fileno()).st_size
                        if search_bytes and file_size_actual <= 65536:
                            if search_bytes not in head:
                                return False

        
        if not encoding:
            encoding = "utf-8"

        search_str_nfc = normalize_unicode(search_string)
        search_fold = search_str_nfc.casefold()

        # 2. 텍스트 기반 검색
        if raw_data is not None:
            # 메모리 내 데이터 기반 검색
            try:
                content = raw_data.decode(encoding, errors="ignore")
                content_nfc = normalize_unicode(content)
                content_fold = content_nfc.casefold()
                if search_fold in content_fold:
                    return True
                
                # 이스케이프 문자를 포함한 필터링을 최적화합니다.
                has_non_ascii = any(ord(c) > 127 for c in search_str_nfc)
                if has_non_ascii:
                    if "\\" in content_nfc and ("\\u" in content_nfc or "\\x" in content_nfc):
                        return True
                return False
            except Exception as e:
                logger.debug(AppStrings.LOG_SCH_EXISTENCE_DECODE_FAIL.format(e))
                return True
        else:
            # 스트리밍 기반 검색 (대용량 일반 파일 등)
            with open(file_path, "r", encoding=encoding, errors="ignore") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    
                    chunk_nfc = normalize_unicode(chunk)
                    chunk_fold = chunk_nfc.casefold()
                    if search_fold in chunk_fold:
                        return True
                    
                    if "\\" in chunk_nfc:
                        has_non_ascii = any(ord(c) > 127 for c in search_str_nfc)
                        if has_non_ascii and ("\\u" in chunk_nfc or "\\x" in chunk_nfc):
                            return True
        return False
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        logger.debug(AppStrings.LOG_SCH_EXISTENCE_JSON_FALLBACK.format(file_path, e))
        return True
    except Exception as e:
        logger.debug(AppStrings.LOG_SCH_EXISTENCE_UNEXPECTED.format(file_path, e))
        return True


def read_text_file_with_encoding(file_path: str) -> Tuple[str, str]:
    """파일 경로를 받아 자동으로 인코딩을 감지한 뒤 텍스트를 반환합니다."""
    try:
        # 샘플링 범위를 64 KB로 확대하여 EUC-KR/CP949 인코딩 오탐을 방지합니다.
        with open(file_path, "rb") as f:
            head = f.read(65536)
            encoding = detect_encoding_quickly(head)
        with open(file_path, "r", encoding=encoding, errors="replace") as f:
            content = f.read()
        return content, encoding
    except (IOError, OSError) as e:
        logger.debug(AppStrings.ERROR_READ_FILE.format(file_path, e))
        raise


class FileScanner:
    """지정된 폴더 및 확장자에 맞는 파일을 찾아 목록화하는 클래스입니다."""

    def __init__(
        self,
        folders: List[str],
        extensions: List[str],
        filename_filter: Optional[Union[str, List[str]]] = None,
        stop_check_callback: Optional[Callable[[], bool]] = None,
        **kwargs,
    ):
        """객체를 초기화합니다."""
        self.folders = folders
        self.extensions = [(e if e.startswith(".") else "." + e).lower() for e in extensions]
        if not filename_filter:
            self.filename_filters: List[str] = []
        elif isinstance(filename_filter, str):
            self.filename_filters = [filename_filter.lower()]
        else:
            self.filename_filters = [f.lower() for f in filename_filter]
        # 파일 패턴 필터를 __init__에서 한 번만 처리
        # _scan_recursive 루프에서 파일마다 리스트 컴프리헨션을 실행하던 부하를 제거하고
        # 생성 시점에 한 번 계산 후 멤버 변수로 재사용합니다.
        import fnmatch as _fnmatch

        self._fnmatch = _fnmatch
        self.processed_filename_filters: List[str] = [
            (f"*{f}*" if "*" not in f else f).lower() for f in self.filename_filters
        ]
        self.exclude_hidden = bool(kwargs.get("exclude_hidden", False))
        self.stop_check_callback = stop_check_callback
        self._yield_counter: int = 0

    def scan(self) -> List[FileInfo]:
        """지정된 폴더들을 재귀적으로 스캔하여 파일 목록을 반환합니다."""
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
        """폴더를 재귀적으로 탐색하며 대상 파일을 수집합니다."""
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
    """엑셀 파일의 이진 시그니처가 올바른지 확인합니다."""
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
    """주어진 파일이 유효한 엑셀 시그니처를 가졌는지 여부를 반환합니다."""
    is_valid, _ = _check_excel_signature(file_path)
    return is_valid


def search_in_excel_special(
    file_path: str,
    search_string: str,
    exact_match: bool = False,
    use_complex_search: bool = False,
    stop_event=None,
    existence_only: bool = False,
) -> Optional[Union[SearchResult, SkippedResult]]:
    """엑셀 파일 내의 텍스트를 검색하는 특수 엔진을 실행합니다."""
    signature_ok, signature_error = _check_excel_signature(file_path)
    if not signature_ok:
        return (Constants.STATUS_SKIPPED, signature_error or AppStrings.ERROR_EXCEL_SIGNATURE)
    if HAS_RUST_ENGINE and not use_complex_search:
        try:
            mode_bits = Constants.RUST_MODE_NORMAL | Constants.RUST_MODE_EXCEL
            if exact_match:
                mode_bits |= Constants.RUST_MODE_EXACT
            if existence_only:
                mode_bits |= Constants.RUST_MODE_EXISTENCE_ONLY
            results = sf_engine.search_file(str(file_path), search_string, mode_bits)
            if results:
                if existence_only:
                    # [Boolean] 일치 항목 발견 시 즉시 반환
                    return (file_path, 1, [(1, AppStrings.BOOLEAN_SEARCH_MATCH_CONTENT, None, None)])
                processed = []
                sheet_errors: List[str] = []
                for m in results:
                    content = str(m[1])
                    if content.startswith(RUST_MATCH_MARKER_EXCEL_PANIC):
                        panic_detail = content[len(RUST_MATCH_MARKER_EXCEL_PANIC) :].strip() or "unknown"
                        return (Constants.STATUS_SKIPPED, AppStrings.ERROR_EXCEL_PANIC.format(panic_detail))
                    if content.startswith("ERR_") and "|" in content:
                        return (Constants.STATUS_SKIPPED, format_skip_reason(content))
                    if content.startswith(RUST_MATCH_MARKER_EXCEL_SHEET_ERROR):
                        payload = content[len(RUST_MATCH_MARKER_EXCEL_SHEET_ERROR) :]
                        if "|" in payload:
                            sheet_name, detail = payload.split("|", 1)
                        else:
                            sheet_name, detail = "unknown", payload
                        sheet_error = AppStrings.ERROR_SEARCH_EXCEL_SHEET.format(sheet_name, detail)
                        logger.warning(f"[{file_path}] {sheet_error}")
                        sheet_errors.append(sheet_error)
                        continue
                    # [H-02] Rust 엔진 결과가 '\t' 또는 ' | ' 구분자로 올 수 있으므로 모두 지원
                    if "\t" in content:
                        parts = content.split("\t", 2)
                    else:
                        parts = content.split(" | ", 2)

                    if len(parts) >= 3:
                        processed.append((m[0], parts[0], parts[1], parts[2]))
                    else:
                        processed.append((m[0], content, "", ""))
                if processed:
                    return (file_path, len(processed), processed)
                if sheet_errors:
                    return (Constants.STATUS_SKIPPED, sheet_errors[0])
            # [방어 정책] Rust 엔진에서 결과가 없으면 즉시 종료합니다.
            # 이는 중복 파싱과 다른 성능 비용을 방지하기 위한 의도적인 설계입니다.
            # 복합 검색(대소문자 비교) 상황에서는 Python 폴백을 사용하여 무결성을 보장합니다.
            return None
        except Exception as e:
            logger.error(AppStrings.LOG_SCH_RUST_EXCEL_FAIL.format(file_path, e))
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
                                if existence_only:
                                    # [Boolean] 일치 항목 발견 시 즉시 반환
                                    return (file_path, 1, [(1, AppStrings.BOOLEAN_SEARCH_MATCH_CONTENT, None, None)])
                                
                                # [상] Python 경로 매치 상한 적용
                                if count <= Constants.MAX_PER_FILE_MATCHES:
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
                                elif count == Constants.MAX_PER_FILE_MATCHES + 1:
                                    matches.append((-1, AppStrings.MSG_MATCH_LIMIT_PER_FILE.format(Constants.MAX_PER_FILE_MATCHES), "", ""))
            except BaseException as e:  # 특정 시트에서 패닉 발생 시 해당 시트만 스킵
                sheet_err_msg = AppStrings.ERROR_SEARCH_EXCEL_SHEET.format(sheet_name, e)
                logger.warning(f"[{file_path}] {sheet_err_msg}")
                continue

        if count > 0:
            final_count = min(count, Constants.MAX_PER_FILE_MATCHES + 1)
            return (file_path, final_count, matches)
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
    existence_only: bool = False,
) -> Optional[Union[SearchResult, SkippedResult]]:
    """
    JSON 특수 검색을 수행합니다.
    """
    if HAS_RUST_ENGINE and not use_complex_search:
        try:
            mode_bits = Constants.RUST_MODE_JSON
            if exact_match:
                mode_bits |= Constants.RUST_MODE_EXACT
            if existence_only:
                mode_bits |= Constants.RUST_MODE_EXISTENCE_ONLY
            results = sf_engine.search_file(str(file_path), search_string, mode_bits)
            if results:
                if existence_only:
                    # [Boolean] 일치 항목 발견 시 즉시 반환
                    return (file_path, 1, [(1, AppStrings.BOOLEAN_SEARCH_MATCH_CONTENT, None, None)])
                skip_reason = _extract_marker_skip_reason(results)
                if skip_reason:
                    if "ERR_MEMORY_GUARD" in skip_reason:
                        logger.warning(AppStrings.LOG_SRCH_RUST_MEM_GUARD_WARN.format(file_path))
                    return (Constants.STATUS_SKIPPED, skip_reason)
                processed = []
                for m in results:
                    parts = m[1].split("\t", 1)
                    # JSON 경로 정규화: /를 .으로 변경하고 시작 / 문자를 제거합니다.
                    json_path = parts[0].lstrip("/").replace("/", ".")
                    val = parts[1] if len(parts) > 1 else ""
                    processed.append((m[0], json_path, val, m[2], m[3]))
                return (file_path, len(processed), processed)
            
            # 탐지 누락 방지: Rust 엔진이 결과를 찾지 못한 경우 Python 폴백으로 이어지도록 합니다.
            pass
        except Exception as e:
            logger.error(AppStrings.LOG_SCH_RUST_JSON_FAIL.format(file_path, e))
            # [Policy] 자동 폴백 중단
            return (Constants.STATUS_SKIPPED, AppStrings.ERROR_JSON_PARSE + f" (Eng: {e})")
    try:
        import json

        file_size = 0
        try:
            file_size = os.path.getsize(file_path)
            if file_size > Constants.MAX_JSON_DOM_SIZE:
                logger.warning(AppStrings.LOG_SCH_JSON_LIMIT.format(file_path))
                return (Constants.STATUS_SKIPPED, AppStrings.SKIP_REASON_TOO_LARGE.format(f"{file_size} bytes"))
        except (OSError, IOError):
            # 파일 크기 측정 실패 시 안전을 위해 필터링을 건너뜁니다.
            pass

        # [v4.63.2] Integrity Fix: existence_only 시에도 무결성 체크(loads)는 수행해야 함
        # 조기 return None 대신 traversal 건너뛰기 힌트로만 활용
        skip_traversal = False
        if existence_only:
            if not _fast_existence_check(file_path, search_string, exact_match):
                skip_traversal = True
        
        # 대용량 파일도 무결성(loads) 체크를 수행하도록 처리합니다.
        # 단, 미매치 힌트가 명확하면 loads 통과 후 None을 반환함 (아래 loads 이후로 이동)

        # [v4.63.9 Defensive] 변수 초기화 강제 (UnboundLocalError 원천 차단)
        raw_bytes = None
        encoding = None
        processed_content = None

        try:
            f_size = os.path.getsize(file_path)
            
            with open(file_path, "rb") as f_raw:
                # 성능 최적화를 위해 일정 크기 이상의 파일은 mmap(메모리 매핑)을 사용합니다.
                if f_size >= Constants.JSON_MMAP_THRESHOLD:
                    mm = mmap.mmap(f_raw.fileno(), 0, access=mmap.ACCESS_READ)
                    try:
                        # 1. 인코딩 감지 (최대 64KB 참조)
                        sample_len = min(f_size, 65536)
                        encoding = detect_encoding_quickly(mm[:sample_len])
                        
                        # [v4.63.9 Performance Fix] mm[:] 전체 복사 대신 mmap 객체를 직접 디코딩에 활용
                        # mmap 인터페이스는 bytes-like이므로 decode가 직접 가능함
                        try:
                            # JSON loads를 위해 전체 텍스트 변환 (DOM 방식의 한계)
                            processed_content = mm.read().decode(encoding, errors="strict")
                            # 디코딩 성공 시 메모리 절감을 위해 raw_bytes를 유지하지 않습니다.
                        except UnicodeDecodeError:
                            processed_content = None
                            # 디코딩 실패 시 폴백 처리를 위한 데이터를 확보합니다.
                            # mm.read() 이후이므로 seek(0) 후 전체 읽기
                            mm.seek(0)
                            raw_bytes = mm.read()
                    finally:
                        mm.close()
                else:
                    # 소형 파일은 기존 방식 유지
                    raw_bytes = f_raw.read()
                    encoding = detect_encoding_quickly(raw_bytes[:65536])
        except (IOError, OSError) as e:
            logger.debug(AppStrings.ERROR_READ_FILE.format(file_path, e))
            raise

        try:
            # 손상된 바이트가 무음 치환되는 것을 방지하기 위해 strict 모드로 디코딩합니다.
            # mmap 분기에서 이미 디코딩을 시도했을 수 있으므로 가드 추가
            if 'processed_content' not in locals() or processed_content is None:
                # raw_bytes가 None인 경우를 대비한 가드 로직입니다.
                if raw_bytes is not None:
                    try:
                        processed_content = raw_bytes.decode(encoding, errors="strict")
                    except UnicodeDecodeError:
                        # 인코딩 감지가 틀렸거나 데이터가 손상됨. UTF-16 재시도 게이트
                        processed_content = None
                else:
                    processed_content = None
            data = None
            if processed_content:
                try:
                    data = json.loads(processed_content, strict=True)
                except (json.JSONDecodeError, ValueError):
                    pass

            # JSON 파싱이나 디코딩 실패 시 No-BOM UTF-16으로 재시도를 수행합니다.
            if data is None:
                # 데이터가 확보된 경우에만 재시도를 수행하여 무결성을 유지합니다.
                # mmap 디코딩 성공 후 파싱이 실패한 경우(raw_bytes=None)는 메시지 왜곡 방지를 위해 즉시 실패 처리
                if raw_bytes is not None and encoding not in ["utf-16-le", "utf-16-be", "utf-16"]:
                    for alt_enc in ["utf-16-le", "utf-16-be"]:
                        try:
                            alt_content = raw_bytes.decode(alt_enc, errors="strict")
                            data = json.loads(alt_content, strict=True)
                            encoding = alt_enc
                            processed_content = alt_content
                            break
                        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                            continue
                
                if data is None:
                    # 모든 재시도 실패 시 SKIPPED 보고 (None 반환 방지)
                    return (Constants.STATUS_SKIPPED, AppStrings.ERROR_JSON_PARSE + " (Integrity check failed)")

            # 무결성 통과 후 매치가 없는 것이 확실하면 여기서 조기 종료
            if skip_traversal:
                # 모든 파일 규모에 대해 최적화 로직을 적용합니다.
                return None

        except Exception as e:
            return (Constants.STATUS_SKIPPED, AppStrings.ERROR_JSON_PARSE + f" ({e})")
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
            # 중단 이벤트를 확인하기 위해 별도의 반복 카운터를 사용합니다.
            # 매치가 없을 때 불필요한 IPC 호출이 발생하는 것을 방지합니다.
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
                    if existence_only:
                        # [Boolean] 일치 항목 발견 시 즉시 반환
                        return (file_path, 1, [(1, AppStrings.BOOLEAN_SEARCH_MATCH_CONTENT, None, None)])
                    
                    # [상] Python 경로 매치 상한 적용
                    if total_count <= Constants.MAX_PER_FILE_MATCHES:
                        matches.append((1, path or "root", val_raw))
                    elif total_count == Constants.MAX_PER_FILE_MATCHES + 1:
                        matches.append((-1, AppStrings.MSG_MATCH_LIMIT_PER_FILE.format(Constants.MAX_PER_FILE_MATCHES), ""))

        if total_count > 0:
            final_count = min(total_count, Constants.MAX_PER_FILE_MATCHES + 1)
            return (file_path, final_count, matches)
        return None
    except (json.JSONDecodeError, ValueError) as e:
        return (Constants.STATUS_SKIPPED, AppStrings.ERROR_JSON_PARSE + f" ({e})")
    except Exception as e:
        logger.error(AppStrings.LOG_SCH_JSON_FAIL.format(file_path, e))
        return (Constants.STATUS_SKIPPED, f"JSON Search Error: {e}")


def search_in_xml_special(
    file_path: str,
    search_string: str,
    exact_match: bool = False,
    use_complex_search: bool = False,
    stop_event=None,
    existence_only: bool = False,
) -> Optional[Union[SearchResult, SkippedResult]]:
    """
    XML 특수 검색을 수행합니다.
    """
    if HAS_RUST_ENGINE and not use_complex_search:
        try:
            mode_bits = Constants.RUST_MODE_XML
            if exact_match:
                mode_bits |= Constants.RUST_MODE_EXACT
            if existence_only:
                mode_bits |= Constants.RUST_MODE_EXISTENCE_ONLY
            results = sf_engine.search_file(str(file_path), search_string, mode_bits)
            if results:
                if existence_only:
                    # [Boolean] 일치 항목 발견 시 즉시 반환
                    return (file_path, 1, [(1, AppStrings.BOOLEAN_SEARCH_MATCH_CONTENT, None, None)])
                skip_reason = _extract_marker_skip_reason(results)
                if skip_reason:
                    return (Constants.STATUS_SKIPPED, skip_reason)
                processed = []
                for m in results:
                    parts = m[1].split("\t", 1)
                    tag_path = parts[0].lstrip("/").replace("/", " > ")
                    val = parts[1] if len(parts) > 1 else ""
                    processed.append((m[0], tag_path, val, m[2], m[3]))
                return (file_path, len(processed), processed)
            
            # XML 모드에서 탐지 누락을 방지합니다.
            pass
        except Exception as e:
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
                self.parser.EndElementHandler = self.end_element
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
                        if existence_only:
                            # 일치 항목 발견 시 예외를 발생시켜 파싱을 즉시 중단합니다.
                            raise StopIteration("BOOLEAN_MATCH")
                        
                        # [상] Python 경로 매치 상한 적용
                        if count <= Constants.MAX_PER_FILE_MATCHES:
                            matches.append((self.parser.CurrentLineNumber, str(k), val))
                        elif count == Constants.MAX_PER_FILE_MATCHES + 1:
                            matches.append((-1, AppStrings.MSG_MATCH_LIMIT_PER_FILE.format(Constants.MAX_PER_FILE_MATCHES), ""))

            def end_element(self, name):
                if self.current_tags:
                    self.current_tags.pop()

            def char_data(self, data):
                nonlocal count
                text = normalize_unicode(data).strip()
                if text:
                    text_comp = text.casefold()
                    if (search_string in text_comp) if not exact_match else (search_string == text_comp):
                        count += 1
                        if existence_only:
                            # 일치 항목 발견 시 파싱을 중단합니다.
                            raise StopIteration("BOOLEAN_MATCH")
                        
                        # Python 검색 경로에서 매치 결과 개수 상한을 적용합니다.
                        if count <= Constants.MAX_PER_FILE_MATCHES:
                            matches.append(
                                (
                                    self.parser.CurrentLineNumber,
                                    self.current_tags[-1] if self.current_tags else "root",
                                    text,
                                )
                            )
                        elif count == Constants.MAX_PER_FILE_MATCHES + 1:
                            matches.append((-1, AppStrings.MSG_MATCH_LIMIT_PER_FILE.format(Constants.MAX_PER_FILE_MATCHES), ""))

        searcher = XMLSearcher()
        try:
            searcher.parser.Parse(processed_content, True)
        except (xml.parsers.expat.ExpatError, StopIteration) as xe:
            # StopIteration은 Boolean 매칭 시 의도적인 중단임
            if isinstance(xe, StopIteration) and str(xe) == "BOOLEAN_MATCH":
                return (file_path, 1, [(1, AppStrings.BOOLEAN_SEARCH_MATCH_CONTENT, None, None)])
            
            if _stop_event and _stop_event.is_set():
                return (Constants.STATUS_SKIPPED, AppStrings.LOG_SCH_STOPPED_BY_USER)
            logger.warning(AppStrings.LOG_SCH_XML_FAIL.format(xe))
            return (Constants.STATUS_SKIPPED, str(xe))
        if count > 0:
            final_count = min(count, Constants.MAX_PER_FILE_MATCHES + 1)
            return (file_path, final_count, matches)
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
    existence_only: bool = False,
) -> Optional[Union[SearchResult, SkippedResult]]:
    """
    Archive 특수 검색을 수행합니다.
    """
    if HAS_RUST_ENGINE and not use_complex_search:
        try:
            mode_bits = Constants.RUST_MODE_ARCHIVE
            if exact_match:
                mode_bits |= Constants.RUST_MODE_EXACT
            if existence_only:
                mode_bits |= Constants.RUST_MODE_EXISTENCE_ONLY
            results = sf_engine.search_file(str(file_path), search_string, mode_bits)
            if results:
                if existence_only:
                    # [Boolean] 일치 항목 발견 시 즉시 반환
                    return (file_path, 1, [(1, AppStrings.BOOLEAN_SEARCH_MATCH_CONTENT, None, None)])
                skip_reason = _extract_marker_skip_reason(results)
                if skip_reason:
                    return (Constants.STATUS_SKIPPED, skip_reason)
                processed = []
                for m in results:
                    parts = m[1].split("\t")
                    ns = parts[0].replace("NS: ", "") if len(parts) > 0 else ""
                    key = parts[1].replace("Key: ", "") if len(parts) > 1 else ""
                    src = parts[2].replace("S: ", "") if len(parts) > 2 else ""
                    trans = parts[3].replace("T: ", "") if len(parts) > 3 else ""
                    processed.append((m[0], ns, key, src, trans))
                return (file_path, len(processed), processed)
            
            # 탐지 누락 방지: Rust 엔진 실패 시 Python 엔진으로 폴백되도록 처리합니다.
            pass
        except Exception as e:
            logger.error(AppStrings.LOG_SCH_RUST_ARCHIVE_FAIL.format(file_path, e))
            # 자동 폴백을 중단하는 정책을 적용합니다.
            return (Constants.STATUS_SKIPPED, AppStrings.ERROR_IO_DURING_SEARCH.format(e))
    try:
        import json

        # [v4.63.9 Defensive] 변수 초기화 강제 (UnboundLocalError 원천 차단)
        raw_bytes = None
        encoding = None
        processed_content = None

        try:
            f_size = os.path.getsize(file_path)
            if f_size > Constants.MAX_JSON_DOM_SIZE:
                logger.warning(AppStrings.LOG_SCH_JSON_LIMIT.format(file_path))
                return (Constants.STATUS_SKIPPED, AppStrings.SKIP_REASON_MEMORY_GUARD.format(f"{f_size} bytes"))
        except (OSError, IOError):
            pass

        # [v4.63.2] Integrity Fix: existence_only 시에도 무결성 체크(loads)는 수행해야 함
        skip_traversal = False
        if existence_only:
            if not _fast_existence_check(file_path, search_string, exact_match):
                skip_traversal = True

        # 정합성 보장을 위해 대용량 파일에서도 무결성 체크를 수행합니다.
        # 미매치 힌트(skip_traversal)는 loads 통과 후에만 적용함 (아래로 이동)

        try:
            import mmap
            f_size = os.path.getsize(file_path)
            
            with open(file_path, "rb") as f_raw:
                # 성능 최적화를 위해 mmap을 사용합니다.
                if f_size >= Constants.JSON_MMAP_THRESHOLD:
                    mm = mmap.mmap(f_raw.fileno(), 0, access=mmap.ACCESS_READ)
                    try:
                        sample_len = min(f_size, 65536)
                        encoding = detect_encoding_quickly(mm[:sample_len])
                        
                        # [v4.63.9 Performance Fix] mm[:] 전체 복사 대신 mmap 객체를 직접 디코딩에 활용
                        try:
                            # Archive loads를 위해 전체 텍스트 변환
                            processed_content = mm.read().decode(encoding, errors="strict")
                        except UnicodeDecodeError:
                            processed_content = None
                            # [v4.63.10 Integrity Fix] 디코딩 실패 시 폴백 게이트용 데이터 확보
                            mm.seek(0)
                            raw_bytes = mm.read()
                    finally:
                        mm.close()
                else:
                    raw_bytes = f_raw.read()
                    encoding = detect_encoding_quickly(raw_bytes[:65536])
        except (IOError, OSError) as e:
            logger.debug(AppStrings.ERROR_READ_FILE.format(file_path, e))
            raise

        try:
            # Archive 데이터 무 결성 강화: 손상된 바이트가 무음 치환되는 것을 방지합니다.
            # mmap 분기에서 이미 디코딩을 시도했을 수 있음
            if 'processed_content' not in locals() or processed_content is None:
                if raw_bytes is not None:
                    try:
                        processed_content = raw_bytes.decode(encoding, errors="strict")
                    except UnicodeDecodeError:
                        processed_content = None
                else:
                    processed_content = None
            
            data = None
            if processed_content:
                try:
                    data = json.loads(processed_content, strict=True)
                except (json.JSONDecodeError, ValueError):
                    pass

            # Archive 파일도 No-BOM UTF-16 재시도 지원
            if data is None:
                # 데이터가 확보된 경우에만 재시도를 수행하여 무결성을 유지합니다.
                if raw_bytes is not None and encoding not in ["utf-16-le", "utf-16-be", "utf-16"]:
                    for alt_enc in ["utf-16-le", "utf-16-be"]:
                        try:
                            alt_content = raw_bytes.decode(alt_enc, errors="strict")
                            data = json.loads(alt_content, strict=True)
                            encoding = alt_enc
                            processed_content = alt_content
                            break
                        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                            continue
                
                if data is None:
                    return (Constants.STATUS_SKIPPED, AppStrings.ERROR_JSON_PARSE + " (Archive Integrity Error)")

            # existence_only 모드 최적화: 무결성 통과 후 매치가 없는 것이 확실하면 조기 종료합니다.
            if skip_traversal:
                return None

        except Exception as e:
            return (Constants.STATUS_SKIPPED, AppStrings.ERROR_JSON_PARSE + f" (Archive Error: {e})")
        if data is None or not isinstance(data, dict):
            return (Constants.STATUS_SKIPPED, AppStrings.ERROR_JSON_PARSE + " (Invalid Archive Format)")
            
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
                    if existence_only:
                        # [Boolean] 일치 항목 발견 시 즉시 반환
                        return (file_path, 1, [(1, AppStrings.BOOLEAN_SEARCH_MATCH_CONTENT, None, None)])
                    
                    # [상] Python 경로 매치 상한 적용
                    if count <= Constants.MAX_PER_FILE_MATCHES:
                        matches.append((1, ns, child.get("Key", ""), s, t))
                    elif count == Constants.MAX_PER_FILE_MATCHES + 1:
                        matches.append((-1, AppStrings.MSG_MATCH_LIMIT_PER_FILE.format(Constants.MAX_PER_FILE_MATCHES), "", "", ""))
        if count > 0:
            final_count = min(count, Constants.MAX_PER_FILE_MATCHES + 1)
            return (file_path, final_count, matches)
        return None
    except Exception as e:
        logger.error(AppStrings.LOG_SCH_ARCHIVE_FAIL.format(file_path, e))
        return (Constants.STATUS_SKIPPED, f"Archive Search Error: {e}")


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
    """파일의 성격에 따라 적절한 검색 엔진을 선택하여 검색을 수행합니다."""
    search_string_nfc = normalize_unicode(search_string)
    ext = splitext(file_path)[1].lower()
    if ext in [".xlsx", ".xlsm", ".xls", ".xlsb"]:
        is_exact = False
        if special_mode and Constants.MODE_EXACT in special_mode:
            is_exact = True
        try:
            return search_in_excel_special(
                file_path, 
                search_string_nfc, 
                is_exact, 
                use_complex_search=use_complex_search, 
                stop_event=stop_event,
                existence_only=bool(kwargs.get(Constants.PAYLOAD_EXISTENCE_ONLY, False))
            )
        except ImportError as e:
            logger.error(AppStrings.ERROR_EXCEL_LIB.format(e))
            raise RuntimeError(AppStrings.ERROR_EXCEL_CALAMINE_REQ)
        except Exception as e:
            logger.error(AppStrings.ERROR_SEARCH_EXCEL.format(file_path, e), exc_info=True)
            return (Constants.STATUS_SKIPPED, AppStrings.ERROR_SEARCH_EXCEL.format(file_path, e))
    if special_mode:
        is_exact = Constants.MODE_EXACT in special_mode
        existence_only = bool(kwargs.get(Constants.PAYLOAD_EXISTENCE_ONLY, False))
        if Constants.MODE_XML in special_mode:
            return search_in_xml_special(
                file_path, search_string_nfc, is_exact, use_complex_search, stop_event=stop_event, existence_only=existence_only
            )
        elif Constants.MODE_JSON in special_mode:
            return search_in_json_special(
                file_path, search_string_nfc, is_exact, use_complex_search, stop_event=stop_event, existence_only=existence_only
            )
        elif Constants.MODE_ARCHIVE in special_mode:
            return search_in_archive_special(
                file_path, search_string_nfc, is_exact, use_complex_search, stop_event=stop_event, existence_only=existence_only
            )
        elif Constants.MODE_EXCEL in special_mode:
            return search_in_excel_special(
                file_path,
                search_string_nfc,
                is_exact,
                use_complex_search=use_complex_search,
                stop_event=stop_event,
                existence_only=existence_only,
            )
    try:
        if file_size is None:
            file_size = os.path.getsize(file_path)
        if file_size == 0:
            return (Constants.STATUS_SKIPPED, AppStrings.SKIP_EMPTY_FILE)
    except (OSError, IOError) as e:
        logger.debug(AppStrings.LOG_SCH_BINARY_CHECK_ERROR.format(file_path, e))
        return (Constants.STATUS_SKIPPED, AppStrings.ERROR_FILE_ACCESS_BINARY.format(e))
    # 인코딩 조기 감지 (Rust 결과 신뢰도 판단 및 폴백 결정용)
    head = b""
    detected_enc_quick = Constants.ENC_UTF8
    try:
        with open(file_path, "rb") as f_chk:
            head = f_chk.read(4096)
            detected_enc_quick = detect_encoding_quickly(head)
    except Exception:
        pass

    t_rust = 0.0
    t_norm = 0.0
    rust_results = None
    # [엔진 선택 정책]
    # - 기본 검색: Rust 엔진(정규식/패턴/일반 특수자) 우선 사용 (빠른 처리)
    # - 복합검색(대소문자): '특별한 유니코드 문자' 옵션 활성화 시 Python 폴백으로
    # - 별도로 정의된 유니코드 검색 방식 차이의 성능/무결성 정책을 스레드에서 처리 (어디 포함되었는지)
    # 복합 검색(use_complex_search=True) 시 Rust 엔진의 Simple CaseFolding 정책
    # Rust 엔진 검색을 건너뛰고 바로 Python 폴백 엔진으로 진입합니다. (코드 리뷰 코드에 미반영)
    # 바이너리 여부를 변수에 저장하여 재사용함으로써 중복 호출 오버헤드를 줄입니다.
    is_binary = is_binary_file(file_path) if not HAS_RUST_ENGINE or use_complex_search else False
    if HAS_RUST_ENGINE and not use_complex_search and not force_python:
        try:
            exclude_binary = bool(kwargs.get("exclude_binary", False))
            existence_only = bool(kwargs.get(Constants.PAYLOAD_EXISTENCE_ONLY, False))
            mode_bits = get_rust_mode_bits(special_mode, exclude_binary=exclude_binary, existence_only=existence_only)
            # Rust 단일 파일 검색 시 중단 이벤트(stop_event) 체크를 포함합니다.
            import time
            t_start = time.time()
            # Rust 엔진 검색어에서 BOM이나 제어 문자를 제거하여 매칭 확률을 높입니다.
            clean_pattern = search_string_nfc.replace("\ufeff", "")
            rust_results = sf_engine.search_file(str(file_path), clean_pattern, mode_bits, stop_event)
            t_rust = time.time() - t_start
            if not rust_results:
                # 인코딩 신뢰도 판단: UTF-8(및 SIG)인 경우 Rust 결과를 신뢰합니다.
                if detected_enc_quick in [Constants.ENC_UTF8, Constants.ENC_UTF8_SIG]:
                    if b"\x00" not in head:
                        return None
                
                # 비-UTF8(UTF-16 등): Rust가 지원하지 않는 특이 케이스일 수 있으므로 Python 폴백을 허용합니다.
                pass
            
            if rust_results:
                # 방어 로직: 정상 결과 확인 전 에러 마커가 있는지 먼저 체크합니다.
                skip_reason = _extract_marker_skip_reason(rust_results)
                if skip_reason:
                    return (Constants.STATUS_SKIPPED, skip_reason)

                t_n_start = time.time()
                normalized, binary_count = _normalize_rust_matches(
                    rust_results, special_mode, existence_only=existence_only
                )
                t_norm = time.time() - t_n_start
                # 프로덕션 디버그 로그 출력
                if len(rust_results) > 100 or t_rust > 0.1:
                    logger.debug(AppStrings.LOG_SCH_RUST_PERF_STATS.format(len(rust_results), t_rust, t_norm))
                if binary_count > 0:
                    return (
                        file_path,
                        binary_count,
                        [(1, AppStrings.MSG_BINARY_MATCH.format(binary_count), None, None)],
                    )

                if normalized:
                    return (file_path, len(normalized), normalized)


            
            # Rust 엔진 결과가 비어있는 경우, 일반적인 텍스트 파일이면 즉시 종료합니다.
            if not use_complex_search:
                if detected_enc_quick in [Constants.ENC_UTF8, Constants.ENC_UTF8_SIG] and b"\x00" not in head:
                    return None

            # [엔진 고립 정책] Rust 엔진 부재 시 일반 검색에서의 Python 자동 폴백을 엄격히 금지합니다.
            # 사용자는 '특별한 문자열 검색' 옵션을 켜야만 Python 엔진을 사용할 수 있습니다.
            if not use_complex_search and not force_python:
                if not HAS_RUST_ENGINE:
                    return None
                
                try:
                    with open(file_path, "rb") as f_chk:
                        chk_data = f_chk.read(65536)
                        # UTF-16 no-BOM 폴백 조건 완화 (BOM이 없더라도 가능성 있으면 허용)
                        if not chk_data.startswith((b"\xff\xfe", b"\xfe\xff", b"\xef\xbb\xbf")):
                            # NUL 바이트가 포함되어 있으면 UTF-16일 가능성이 높으므로 폴백 허용
                            if b"\x00" not in chk_data:
                                return None
                except Exception:
                    logger.debug(AppStrings.LOG_SCH_ENGINE_ISOLATION_BAIL.format("(Pre-check fail)", file_path))
                    return None
        except Exception as e:
            logger.error(AppStrings.LOG_SCH_RUST_ENGINE_ERROR.format(file_path, e), exc_info=True)
            return (Constants.STATUS_SKIPPED, AppStrings.ERROR_UNEXPECTED_FILE.format(file_path, e))

    # 여기서부터는 Python 폴백 엔진입니다.
    # use_complex_search가 False이고 force_python도 False인 경우,
    # 성능과 안정성을 위해 불필요한 Python 엔진 구동을 차단합니다(고립 정책).
    # 단, Rust 엔진이 없거나(HAS_RUST_ENGINE=False) 특정 인코딩인 경우 결과 누락 방지를 위해 허용합니다.
    # [엔진 고립 정책] 사용자가 '특별한 문자열 검색' 옵션을 켜지 않은 경우,
    # Python 엔진으로의 자동 폴백을 엄격히 차단합니다. (v2.0 고립 정책)
    exclude_binary = bool(kwargs.get("exclude_binary", False))
    if exclude_binary and is_binary:
        # 옵션 계약 준수: 바이너리 파일 제외 시 Python 경로에서도 즉시 반환
        return None

    if not use_complex_search and not force_python:
        try:
            # JSON, Archive, Excel 등 Python 전용 파서가 필요한 파일은 폴백 허용
            ext = os.path.splitext(file_path)[1].lower()
            if ext in [".json", ".archive", ".xlsx", ".xls"]:
                pass
            else:
                with open(file_path, "rb") as f_chk:
                    chk_data = f_chk.read(65536)
                    # UTF-16/32 등 Rust 지원 외 인코딩이 아닌 일반 텍스트는 Rust의 영역입니다.
                    if not chk_data.startswith((b"\xff\xfe", b"\xfe\xff", b"\xef\xbb\xbf")):
                        # BOM이 없더라도 NUL 바이트 분포가 UTF-16LE/BE 휴리스틱에 맞으면 폴백 허용
                        detected = detect_encoding_quickly(chk_data)
                        if detected not in ["utf-16-le", "utf-16-be"]:
                            return None
        except Exception:
            logger.debug(AppStrings.LOG_SCH_ENGINE_ISOLATION_BAIL.format("(Python Fallback fail)", file_path))
            return None

    existence_only = bool(kwargs.get(Constants.PAYLOAD_EXISTENCE_ONLY, False))

    try:
        if file_size is None:
            file_size = os.path.getsize(file_path)
        encoding = None
        if file_size < Constants.MAX_SMALL_FILE_SIZE:  # 10MB 상수 참조
            if not encoding:
                with open(file_path, "rb") as f_head:
                    head_data = f_head.read(65536)
                    encoding = detect_encoding_quickly(head_data)
                    if not encoding and len(head_data) > 0:
                        # 4바이트 미만 파일에 대한 방어
                        try:
                            encoding = detect_encoding_quickly(head_data[:min(len(head_data), 4)])
                        except Exception:
                            encoding = Constants.ENC_UTF8
            
            # [Extern] 바이너리 판정 전 인코딩 확인 (UTF-16 텍스트 보호)
            is_actually_text = encoding and ("utf-16" in encoding.lower() or "utf-32" in encoding.lower())
            if is_actually_text:
                is_binary = False
            
            # [Batch 3 Optimization] Tiny Files (<128KB) skipping pre-search
            skip_presearch = file_size < 128 * 1024
            
            if not use_complex_search and not skip_presearch:
                # [Optimization] 전체 로드 전 간단한 바이너리 검색으로 필터링 시도
                # 중복 감지 제거를 위해 기 감지된 인코딩을 재사용합니다.
                is_exact = bool(special_mode and Constants.MODE_EXACT in special_mode)
                if not _fast_existence_check(file_path, search_string, is_exact, encoding=encoding):
                    return None
                
            with open(file_path, "r", encoding=encoding, errors="replace") as f_text:
                # 스트리밍 처리로 변경하여 메모리 절약
                is_exact = bool(special_mode and Constants.MODE_EXACT in special_mode)
                search_fold = search_string_nfc.casefold()
                matches = []
                count = 0
                for i, line in enumerate(f_text):
                    if i % 1000 == 0 and stop_event and stop_event.is_set():
                        return Constants.STATUS_SKIPPED, AppStrings.LOG_SCH_STOPPED_BY_USER
                    
                    matched = False
                    line_nfc = normalize_unicode(line)
                    if is_exact:
                        # 공백 제거 후 비교 (NFC 정규화 적용)
                        if line_nfc.casefold().strip() == search_fold:
                            matched = True
                    elif search_fold in line_nfc.casefold():
                        matched = True
                    
                    if matched:
                        count += 1
                        if existence_only:
                            return (file_path, 1, [(i + 1, AppStrings.BOOLEAN_SEARCH_MATCH_CONTENT, None, None)])
                        
                        # Python 검색 경로에서 매치 결과 개수 상한을 적용합니다.
                        if count <= Constants.MAX_PER_FILE_MATCHES:
                            matches.append((i + 1, line.strip()))
                        elif count == Constants.MAX_PER_FILE_MATCHES + 1:
                            matches.append((-1, AppStrings.MSG_MATCH_LIMIT_PER_FILE.format(Constants.MAX_PER_FILE_MATCHES)))
                        else:
                            # 상한 초과 시 더 이상 리스트에 추가하지 않음
                            pass
                
                if count > 0:
                    # UI 일관성을 위해 반환 카운트도 상한으로 캡핑
                    final_count = min(count, Constants.MAX_PER_FILE_MATCHES + 1)
                    if is_binary:
                        return (file_path, final_count, [(1, AppStrings.MSG_BINARY_MATCH.format(final_count), None, None)])
                    return (file_path, final_count, matches)
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
                                if existence_only:
                                    return (
                                        file_path,
                                        1,
                                        [(current_line, AppStrings.BOOLEAN_SEARCH_MATCH_CONTENT, None, None)],
                                    )
                            
                            # Python 검색 경로에서 매치 결과 개수 상한을 적용합니다.
                            if count <= Constants.MAX_PER_FILE_MATCHES:
                                matches.append((current_line, line_trimmed))
                            elif count == Constants.MAX_PER_FILE_MATCHES + 1:
                                matches.append((-1, AppStrings.MSG_MATCH_LIMIT_PER_FILE.format(Constants.MAX_PER_FILE_MATCHES)))
                        elif search_fold in line.casefold():
                            count += 1
                            if existence_only:
                                return (
                                    file_path,
                                    1,
                                    [(current_line, AppStrings.BOOLEAN_SEARCH_MATCH_CONTENT, None, None)],
                                )
                            
                            # [상] Python 경로 매치 상한 적용
                            if count <= Constants.MAX_PER_FILE_MATCHES:
                                matches.append((current_line, line_trimmed))
                            elif count == Constants.MAX_PER_FILE_MATCHES + 1:
                                matches.append((-1, AppStrings.MSG_MATCH_LIMIT_PER_FILE.format(Constants.MAX_PER_FILE_MATCHES)))
            except Exception as e:
                logger.debug(AppStrings.LOG_SCH_STREAM_ERROR.format(e))
                return (Constants.STATUS_SKIPPED, AppStrings.ERROR_IO_DURING_SEARCH.format(e))

            if count > 0:
                if existence_only:
                    return (file_path, 1, [(1, AppStrings.BOOLEAN_SEARCH_MATCH_CONTENT, None, None)])
                if is_binary:
                    return (file_path, count, [(1, AppStrings.MSG_BINARY_MATCH.format(count), None, None)])
                
                # 카운트와 matches 길이 불일치를 보정합니다. (최대 상한 + 마커 1개로 제한)
                # UI 데이터 일관성을 위해 튜플 구조를 정규화합니다.
                final_count = min(count, Constants.MAX_PER_FILE_MATCHES + 1)
                normalized_matches = []
                for m in matches:
                    if len(m) == 2:
                        # (line, content) -> (line, content, offset, length)
                        normalized_matches.append((m[0], m[1], None, None))
                    else:
                        normalized_matches.append(m)
                return (file_path, final_count, normalized_matches)
            return None
    except (IOError, OSError) as e:
        logger.debug(AppStrings.LOG_SCH_ERROR_FILE.format(file_path, e))
        return (Constants.STATUS_SKIPPED, AppStrings.ERROR_IO_DURING_SEARCH.format(e))
    except Exception as e:  # BaseException 대신 구체적인 Exception 포착
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
    """파일 배치를 순회하며 검색을 수행합니다."""
    results = []
    skipped = []
    for f_path, f_size in file_batch:
        # 부모 프로세스의 상태를 확인하여 좀비 프로세스가 되는 것을 방지합니다.
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
    existence_only: bool = False,
    **kwargs,
) -> Dict[str, List]:
    """Rust 엔진을 사용하여 디렉토리를 고속으로 검색합니다."""
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
            # 엔진 사용 중 예외 발생 시 Python 폴백을 통해 재검색을 시도합니다.
            # (위에서 HAS_RUST_ENGINE 체크가 통과했음에도 엔진이 없는 경우여서 이러한 처리)
            raise AttributeError(AppStrings.LOG_SYS_SF_ENGINE_NOT_FOUND.format("sf_engine.search_dir"))
        exclude_binary = bool(kwargs.get("exclude_binary", True))
        mode_bits = get_rust_mode_bits(kwargs.get("special_mode"), exclude_binary=exclude_binary, existence_only=existence_only)
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
            kwargs.get("batch_size", Constants.RUST_RESULT_BATCH_SIZE),
            kwargs.get("flush_ms", Constants.RUST_RESULT_FLUSH_MS),
        )
        formatted_results = []
        skipped_results = []
        if raw_ret:
            matches_list, skipped_list = raw_ret
            for path, matches in matches_list:
                match_tuples, marker_binary_count = _normalize_rust_matches(
                    matches, kwargs.get("special_mode"), existence_only=existence_only
                )
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
                    skip_reason = _extract_marker_skip_reason(matches)
                    if skip_reason:
                        skipped_results.append((path, skip_reason))
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
    existence_only: bool = False,
    **kwargs,
) -> Dict[str, List]:
    """Rust 엔진을 사용하여 파일 목록을 고속으로 검색합니다."""
    if not HAS_RUST_ENGINE or not file_list:
        return {"results": [], "skipped": []}
    try:
        rust_pattern = normalize_unicode(search_string)
        search_func = getattr(sf_engine, "search_files_list", None)
        if not search_func:
            # Rust API에서 인수 부락 시 타입 예외 발생 (없는 결과 반환 등)
            raise AttributeError(AppStrings.LOG_SYS_SF_ENGINE_NOT_FOUND.format("search_files_list"))
        exclude_hidden = bool(kwargs.get("exclude_hidden", False))
        # 파일 리스트 검색 시에도 바이너리 제외 옵션을 존중하도록 처리합니다.
        exclude_binary = bool(kwargs.get("exclude_binary", True))
        mode_bits = get_rust_mode_bits(special_mode, exclude_binary=exclude_binary, existence_only=existence_only)
        raw_ret = search_func(
            file_list,
            rust_pattern,
            mode_bits,
            exclude_hidden,
            stop_event,
            progress_callback,
            kwargs.get("results_callback"),
            batch_size=kwargs.get("batch_size", Constants.RUST_RESULT_BATCH_SIZE),
            flush_ms=kwargs.get("flush_ms", Constants.RUST_RESULT_FLUSH_MS),
        )
        formatted_results = []
        skipped_results = []
        if raw_ret:
            matches_list, skipped_list = raw_ret
            for path, matches in matches_list:
                match_tuples, marker_binary_count = _normalize_rust_matches(
                    matches, special_mode, existence_only=existence_only
                )
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
                    skip_reason = _extract_marker_skip_reason(matches)
                    if skip_reason:
                        skipped_results.append((path, skip_reason))
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
    existence_only: bool = False,
    **kwargs,
) -> Union[List[FileInfo], Tuple[List[FileInfo], List[SkippedResult]]]:
    """키워드가 포함된 파일 목록을 고속으로 찾아 반환합니다."""
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
            # 엔진에서 오류가 발생한 경우 무결성을 위해 Python 폴백을 시도합니다.
            raise AttributeError(AppStrings.LOG_SYS_SF_ENGINE_NOT_FOUND.format("sf_engine.find_files_with_keyword"))
        exclude_binary = bool(kwargs.get("exclude_binary", False))
        rust_mode_bits = get_rust_mode_bits(special_mode, exclude_binary=exclude_binary, existence_only=existence_only)
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
        # 예외 처리 순서를 조정하여 시스템 중단 신호가 올바르게 전파되도록 합니다.
        # KeyboardInterrupt, SystemExit 등 시스템 중단 신호가 올바르게 전파되도록 보장
        if isinstance(e, Exception):
            logger.error(AppStrings.LOG_SCH_RUST_SMART_SCAN_ERROR.format("SearchEngine.SmartScan", e), exc_info=True)
            reason = format_skip_reason(_build_skip_reason(SKIP_CODE_CRITICAL, e))
            if return_skipped:
                return [], [(AppStrings.ERROR_TITLE, reason)]
            return []
        # KeyboardInterrupt, SystemExit 등 시스템 중단 신호는 재전파
        logger.critical(AppStrings.LOG_SCH_RUST_SMART_SCAN_FATAL.format(e), exc_info=True)
        raise
