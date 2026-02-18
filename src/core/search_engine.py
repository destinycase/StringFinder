import os
import re
import json
from os.path import splitext
import logging
import unicodedata
from typing import List, Tuple, Optional, Union, Dict, Callable, Any
import mmap

from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants

# 지원하는 엑셀 확장자 목록
EXCEL_EXTS = {".xlsx", ".xlsm", ".xls", ".xlsb"}

logger = logging.getLogger("StringFinder.SearchEngine")

try:
    import sf_engine

    HAS_RUST_ENGINE = True
    logger.info(AppStrings.LOG_SYS_RUST_SUCCESS)
except ImportError:
    HAS_RUST_ENGINE = False
    logger.warning(AppStrings.LOG_SYS_RUST_FALLBACK)

SearchMatch = Tuple[Any, ...]
SearchResult = Tuple[str, int, List[SearchMatch]]
SkippedResult = Tuple[str, str]
FileInfo = Tuple[str, int]


def normalize_unicode(text: Optional[str]) -> str:
    """문자열을 NFC 방식으로 정규화합니다."""
    if text is None:
        return ""
    return unicodedata.normalize("NFC", str(text))


def is_binary_file(file_path: str) -> bool:
    """
    파일의 첫 1024바이트를 읽어 NULL 바이트 존재 여부로 바이너리 파일인지 판별합니다.
    """
    try:
        if not os.path.exists(file_path):
            return False
        with open(file_path, "rb") as f:
            chunk = f.read(1024)
            return b"\x00" in chunk
    except (IOError, OSError):
        return False
    except Exception as e:
        logger.debug(AppStrings.LOG_SCH_BINARY_CHECK_FAIL.format(file_path, e))
        return False


def detect_encoding_quickly(data: bytes) -> str:
    """
    바이트 데이터의 인코딩을 빠르게 판별합니다.
    주로 UTF-8과 CP949(EUC-KR)를 구분하는 데 사용합니다.

    Args:
        data (bytes): 인코딩을 판별할 원본 바이트 데이터

    Returns:
        str: 판별된 인코딩 이름 (예: 'utf-8', 'cp949', 'utf-16')
    """
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

    try:
        data.decode(Constants.ENC_CP949)
        return Constants.ENC_CP949
    except UnicodeDecodeError:
        pass

    return Constants.ENC_UTF8


def read_text_file_with_encoding(file_path: str) -> Tuple[str, str]:
    """
    파일의 인코딩을 감지하여 텍스트 내용을 읽어옵니다.

    Args:
        file_path (str): 읽을 파일 경로

    Returns:
        Tuple[str, str]: (파일 내용, 감지된 인코딩)
    """
    try:
        with open(file_path, "rb") as f:
            head = f.read(1024)
            encoding = detect_encoding_quickly(head)

        with open(file_path, "r", encoding=encoding, errors="replace") as f:
            content = f.read()

        return content, encoding
    except (IOError, OSError) as e:
        logger.debug(AppStrings.ERROR_READ_FILE.format(file_path, e))
        raise e


class FileScanner:
    """
    검색 대상이 되는 파일 리스트를 효율적으로 스캔하고 필터링하는 클래스입니다.
    """

    def __init__(
        self,
        folders: List[str],
        extensions: List[str],
        filename_filter: Optional[Union[str, List[str]]] = None,
        stop_check_callback: Optional[Callable[[], bool]] = None,
    ):
        """
        스캐너를 초기화합니다.

        Args:
            folders (List[str]): 검색할 폴더 경로 리스트
            extensions (List[str]): 검색할 확장자 리스트 (예: ['.txt', '.py'])
            filename_filter (Optional[Union[str, List[str]]]): 파일 이름에 포함되어야 할 필터링 단어
            stop_check_callback (Optional[Callable[[], bool]]): 검색 중단 여부를 수시로 확인하는 콜백 함수 (True 반환 시 루프 탈출)
        """
        self.folders = folders
        self.extensions = [(e if e.startswith(".") else "." + e).lower() for e in extensions]

        if not filename_filter:
            self.filename_filters: List[str] = []
        elif isinstance(filename_filter, str):
            self.filename_filters = [filename_filter.lower()]
        else:
            self.filename_filters = [f.lower() for f in filename_filter]
        self.stop_check_callback = stop_check_callback
        self._yield_counter: int = 0

    def scan(self) -> List[FileInfo]:
        """
        설정된 폴더들을 순회하며 조건에 맞는 파일 리스트를 수집합니다.
        os.scandir를 사용하여 파일 속성(크기) 접근 등 시스템 콜을 최소화합니다.
        """
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
        """
        재귀적으로 폴더를 스캔하며 파일 정보를 수집합니다.
        visited: 심볼릭 링크 순환 방지를 위한 방문 기록 (realpath 기준)
        """
        if visited is None:
            visited = set()
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    if self.stop_check_callback and self.stop_check_callback():
                        return

                    if hasattr(self, "_yield_counter"):
                        self._yield_counter += 1
                        if self._yield_counter % 1000 == 0:
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
                                if self.filename_filters:
                                    import fnmatch

                                    is_matched = False
                                    fname_lower = entry.name.lower()
                                    for f_pattern in self.filename_filters:
                                        pattern = f"*{f_pattern}*" if "*" not in f_pattern else f_pattern
                                        if fnmatch.fnmatch(fname_lower, pattern.lower()):
                                            is_matched = True
                                            break
                                    if not is_matched:
                                        continue

                                file_list.append((entry.path, entry.stat().st_size))
                    except (OSError, PermissionError):
                        continue
        except (OSError, PermissionError):
            pass


def strip_comments(content: str, ext: str) -> str:
    """
    파일 확장자에 따라 주석을 제거하고 공백으로 대체합니다 (줄 번호 유지).
    """

    def replacer(match):
        return re.sub(r"[^\n\r]", " ", match.group())

    if ext in [".json", ".js", ".c", ".cpp", ".cs", ".java"]:
        return re.sub(r"//.*?\n|/\*.*?\*/", replacer, content, flags=re.S)
    elif ext in [".xml", ".html"]:
        return re.sub(r"<!--.*?-->", replacer, content, flags=re.S)
    elif ext in [".py", ".rb", ".sh", ".yaml", ".yml"]:
        return re.sub(r"#.*?\n", replacer, content)
    elif ext in [".sql"]:
        return re.sub(r"--.*?\n", replacer, content)
    return content


def is_valid_excel_signature(file_path: str) -> bool:
    """
    파일 헤더를 확인하여 유효한 엑셀 파일(XLS, XLSX, ODS 등)인지 검사합니다.
    """
    try:
        with open(file_path, "rb") as f:
            header = f.read(8)
            if header.startswith(b"\xd0\xcf\x11\xe0"):
                return True
            if header.startswith(b"PK\x03\x04"):
                return True
            return False
    except Exception:
        return False


def search_in_excel_special(
    file_path: str, search_string: str, exact_match: bool = False
) -> Optional[Union[SearchResult, SkippedResult]]:
    """
    Excel 파일 전용 특수 검색 모드입니다. (시트 | 위치 | 값)
    결과 튜플 형식: (0, location, value) - 0은 더미 라인 번호
    """
    if HAS_RUST_ENGINE:
        try:
            mode_str = AppStrings.SPECIAL_SEARCH_ITEMS[8] if exact_match else AppStrings.SPECIAL_SEARCH_ITEMS[7]
            results = sf_engine.search_file(str(file_path), search_string, mode_str)
            if results:
                # SearchMatch(line, content, offset, length) -> (0, location, value) 변환
                # Rust는 content에 "Sheet | R1,C1 | Value" 형식으로 넣어줌
                processed = []
                for m in results:
                    parts = m.content.split(" | ", 2)
                    if len(parts) >= 3:
                        # 하지만 Rust가 이미 성실하게 포맷팅해서 주므로 그대로 사용 가능성 높음
                        processed.append((0, parts[0] + "!" + parts[1], parts[2]))
                    else:
                        processed.append((0, m.content, ""))
                return (file_path, len(processed), processed)
            return None
        except Exception as e:
            logger.error(f"Rust Excel 검색 실패: {e}")

    if not is_valid_excel_signature(file_path):
        return (Constants.STATUS_SKIPPED, AppStrings.ERROR_EXCEL_SIGNATURE)

    try:
        from python_calamine import CalamineWorkbook

        try:
            workbook = CalamineWorkbook.from_path(file_path)
        except (IOError, OSError) as e:
            return (Constants.STATUS_SKIPPED, AppStrings.ERROR_EXCEL_ACCESS.format(e))
        except Exception as e:
            return (Constants.STATUS_SKIPPED, AppStrings.ERROR_EXCEL_PROCESS.format(e))

        count = 0
        matches = []
        search_string_norm = re.sub(r"\s+", " ", search_string).strip()
        search_string_lower = search_string.lower()

        for sheet_name in workbook.sheet_names:
            try:
                sheet = workbook.get_sheet_by_name(sheet_name)
                for row_idx, row in enumerate(sheet.to_python()):
                    for col_idx, cell_value in enumerate(row):
                        if cell_value is not None:
                            val_str = normalize_unicode(str(cell_value))
                            val_norm = re.sub(r"\s+", " ", val_str).lower().strip()
                            val_lower = val_str.lower()

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

                                pos = f"{sheet_name}!{col_letter}{row_idx + 1}"

                                matches.append((0, pos, val_str))
            except Exception:
                continue

        if count > 0:
            return (file_path, count, matches)

    except ImportError:
        return (Constants.STATUS_SKIPPED, AppStrings.ERROR_EXCEL_CALAMINE)
    except Exception as e:
        return (Constants.STATUS_SKIPPED, AppStrings.ERROR_SEARCH_EXCEL.format(file_path, e))
    except BaseException as e:
        logger.critical(AppStrings.ERROR_EXCEL_PANIC.format(e), exc_info=True)
        return (Constants.STATUS_SKIPPED, AppStrings.ERROR_PROCESS_CRITICAL.format(e))

    return None


def search_in_json_special(
    file_path: str, search_string: str, exact_match: bool = False
) -> Optional[Union[SearchResult, SkippedResult]]:
    """
    JSON 파일을 파싱하여 값(Value)들 중에서만 검색합니다. 주석은 무시합니다. (Rust 가속 지원)
    """
    if HAS_RUST_ENGINE:
        try:
            mode_str = AppStrings.SPECIAL_SEARCH_ITEMS[4] if exact_match else AppStrings.SPECIAL_SEARCH_ITEMS[3]
            results = sf_engine.search_file(str(file_path), search_string, mode_str)
            if results:
                # SearchMatch(line, content, offset, length) -> (line, path, value) 추출
                processed = []
                for m in results:
                    parts = m.content.split(" | ", 1)
                    json_path = parts[0]
                    val = parts[1] if len(parts) > 1 else ""
                    processed.append((m.line, json_path, val, m.offset, m.length))
                return (file_path, len(processed), processed)
            return None
        except Exception as e:
            logger.error(f"Rust JSON 검색 실패: {e}")

    # Python Fallback
    try:
        import json

        raw_content, encoding = read_text_file_with_encoding(file_path)

        processed_content = raw_content
        processed_content = re.sub(r",(\s*)([}\]])", r"\1\2", processed_content)

        try:
            data = json.loads(processed_content, strict=False)
        except json.JSONDecodeError:
            return (Constants.STATUS_SKIPPED, AppStrings.ERROR_JSON_PARSE)

        matches = []
        search_string = normalize_unicode(search_string).lower()
        newline_indices = [i for i, char in enumerate(raw_content) if char == "\n"]

        def get_line_no(pos):
            import bisect

            return bisect.bisect_left(newline_indices, pos) + 1

        search_state = {"last_pos": 0, "limit_hit": False}
        MAX_JSON_DEPTH = 1000

        def _recursive_search(obj, path="", depth=0):
            if depth >= MAX_JSON_DEPTH:
                logger.warning(AppStrings.LOG_SCH_JSON_LIMIT.format(file_path))
                search_state["limit_hit"] = True
                return 0

            count = 0
            if isinstance(obj, dict):
                for k, v in obj.items():
                    new_path = f"{path}.{k}" if path else str(k)
                    count += _recursive_search(v, new_path, depth + 1)
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                    new_path = f"{path}[{i}]" if path else f"[{i}]"
                    count += _recursive_search(v, new_path, depth + 1)
            else:
                val_raw = normalize_unicode(str(obj))
                val_lower = val_raw.lower()

                is_match = (val_lower == search_string) if exact_match else (search_string in val_lower)
                if is_match:
                    count += 1
                    matches.append((1, path or "root", val_raw))
            return count

        total_count = _recursive_search(data)
        if total_count > 0:
            return (file_path, total_count, matches)
        return None
    except Exception as e:
        logger.error(f"JSON 검색 실패: {e}")
        return (Constants.STATUS_SKIPPED, str(e))


def search_in_xml_special(
    file_path: str, search_string: str, exact_match: bool = False
) -> Optional[Union[SearchResult, SkippedResult]]:
    """
    XML 파일을 파싱하여 검색합니다. 주석은 무시합니다. (Rust 가속 지원)
    """
    if HAS_RUST_ENGINE:
        try:
            mode_str = AppStrings.SPECIAL_SEARCH_ITEMS[2] if exact_match else AppStrings.SPECIAL_SEARCH_ITEMS[1]
            results = sf_engine.search_file(str(file_path), search_string, mode_str)
            if results:
                processed = []
                for m in results:
                    parts = m.content.split(" | ", 1)
                    tag_info = parts[0]
                    content_val = parts[1] if len(parts) > 1 else ""
                    processed.append((m.line, tag_info, content_val, m.offset, m.length))
                return (file_path, len(processed), processed)
            return None
        except Exception as e:
            logger.error(f"Rust XML 검색 실패: {e}")

    # Python Fallback
    try:
        import xml.parsers.expat

        raw_content, encoding = read_text_file_with_encoding(file_path)
        processed_content = strip_comments(raw_content, ".xml")
        matches = []
        count = 0
        search_string = normalize_unicode(search_string).lower()

        class XMLSearcher:
            def __init__(self):
                self.parser = xml.parsers.expat.ParserCreate()
                self.parser.StartElementHandler = self.start_element
                self.parser.CharacterDataHandler = self.char_data
                self.current_tags = []

            def start_element(self, name, attrs):
                nonlocal count
                self.current_tags.append(name)
                for k, v in attrs.items():
                    val = normalize_unicode(str(v))
                    if (search_string in val.lower()) if not exact_match else (search_string == val.lower()):
                        count += 1
                        matches.append((self.parser.CurrentLineNumber, str(k), val))

            def char_data(self, data):
                nonlocal count
                text = normalize_unicode(data).strip()
                if text and ((search_string in text.lower()) if not exact_match else (search_string == text.lower())):
                    count += 1
                    matches.append(
                        (self.parser.CurrentLineNumber, self.current_tags[-1] if self.current_tags else "root", text)
                    )

        searcher = XMLSearcher()
        searcher.parser.Parse(processed_content, True)
        if count > 0:
            return (file_path, count, matches)
        return None
    except Exception as e:
        logger.error(f"XML 검색 실패: {e}")
        return (Constants.STATUS_SKIPPED, str(e))


def search_in_archive_special(
    file_path: str, search_string: str, exact_match: bool = False
) -> Optional[Union[SearchResult, SkippedResult]]:
    """
    .archive 파일(JSON 구조)을 파싱하여 Namespace, Key, Source, Translation 정보를 검색합니다. (Rust 가속 지원)
    """
    if HAS_RUST_ENGINE:
        try:
            mode_str = AppStrings.SPECIAL_SEARCH_ITEMS[6] if exact_match else AppStrings.SPECIAL_SEARCH_ITEMS[5]
            results = sf_engine.search_file(str(file_path), search_string, mode_str)
            if results:
                processed = []
                for m in results:
                    parts = m.content.split(" | ")
                    ns = parts[0].replace("NS: ", "") if len(parts) > 0 else ""
                    key = parts[1].replace("Key: ", "") if len(parts) > 1 else ""
                    src = parts[2].replace("S: ", "") if len(parts) > 2 else ""
                    trans = parts[3].replace("T: ", "") if len(parts) > 3 else ""
                    processed.append((m.line, ns, key, src, trans))
                return (file_path, len(processed), processed)
            return None
        except Exception as e:
            logger.error(f"Rust Archive 검색 실패: {e}")

    # Python Fallback
    try:
        raw_content, encoding = read_text_file_with_encoding(file_path)
        data = json.loads(raw_content)
        matches = []
        count = 0
        search_string = normalize_unicode(search_string).lower()

        for sn in data.get("Subnamespaces", []):
            ns = sn.get("Namespace", "")
            for child in sn.get("Children", []):
                s = normalize_unicode(child.get("Source", {}).get("Text", ""))
                t = normalize_unicode(child.get("Translation", {}).get("Text", ""))
                if (
                    (search_string in s.lower() or search_string in t.lower())
                    if not exact_match
                    else (search_string == s.lower() or search_string == t.lower())
                ):
                    count += 1
                    matches.append((1, ns, child.get("Key", ""), s, t))
        if count > 0:
            return (file_path, count, matches)
        return None
    except Exception as e:
        logger.error(f"Archive 검색 실패: {e}")
        return (Constants.STATUS_SKIPPED, str(e))


def _quick_search_bytes(file_path: str, search_text: str) -> bool:
    """
    파일 전체를 디코딩하지 않고 바이트 패턴 매칭으로 검색어 존재 여부를 빠르게 확인합니다.
    (UTF-8, CP949, UTF-16LE 인코딩을 고려)
    True를 반환하면 파일 내에 검색어(의 바이트 표현)가 존재할 가능성이 높습니다.
    """
    patterns = []
    encodings = ["utf-8", "cp949", "utf-16-le", "utf-16-be"]
    search_lower = search_text.lower()
    search_upper = search_text.upper()

    for enc in encodings:
        try:
            # 원본 패턴 추가
            pat = search_text.encode(enc)
            if pat and pat not in patterns:
                patterns.append(pat)

            # 소문자/대문자 패턴 추가 (대소문자 무시 검색 대응력 강화)
            # 양방향 매칭 보강
            for variant in [search_lower, search_upper]:
                if variant != search_text:
                    pat_v = variant.encode(enc)
                    if pat_v and pat_v not in patterns:
                        patterns.append(pat_v)
        except UnicodeEncodeError:
            pass

    if not patterns:
        return False

    try:
        if os.path.getsize(file_path) == 0:
            return False

        with open(file_path, "rb") as f:
            with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                for pat in patterns:
                    if mm.find(pat) >= 0:
                        return True
    except (ValueError, OSError) as e:
        raise e

    return False


def search_in_file(
    file_path: str,
    search_string: str,
    file_size: Optional[int] = None,
    special_mode: Optional[str] = None,
) -> Optional[Union[SearchResult, SkippedResult]]:
    """
    고성능 검색 엔진: mmap과 바이트 레벨 정규표현식을 조합하여 파일을 검색합니다.
    - 대용량 파일 검색 시 메모리 사용량을 최소화합니다.
    - 주석 제외 검색 및 XML/JSON 특수 모드를 지원합니다.
    - 파일의 인코딩을 자동으로 판별하여 처리합니다.

    Args:
        file_path (str): 검색할 파일 경로
        search_string (str): 검색할 문자열
        file_size (int, optional): 파일 크기 (최적화용)
        special_mode (str, optional): 특수 검색 모드 (XML, JSON 등)

    Returns:
        Optional[Union[SearchResult, SkippedResult]]: 검색 결과 또는 스킵 정보
    """
    search_string_nfc = normalize_unicode(search_string)
    ext = splitext(file_path)[1].lower()

    if ext in [".xlsx", ".xlsm", ".xls", ".xlsb"]:
        is_exact = False
        if special_mode and Constants.MODE_EXACT in special_mode:
            is_exact = True

        try:
            return search_in_excel_special(file_path, search_string_nfc, is_exact)
        except Exception as e:
            error_msg = str(e)
            if "calamine" in error_msg.lower() or "import" in error_msg.lower():
                logger.error(AppStrings.ERROR_EXCEL_LIB.format(e))
                raise RuntimeError(AppStrings.ERROR_EXCEL_CALAMINE_REQ)
            logger.error(AppStrings.ERROR_SEARCH_EXCEL.format(file_path, e))
            return (Constants.STATUS_SKIPPED, AppStrings.ERROR_SEARCH_EXCEL.format(file_path, e))

    # 특수 검색 모드 처리 (XML, JSON, Archive, Excel)
    if special_mode:
        is_exact = Constants.MODE_EXACT in special_mode
        if Constants.MODE_XML in special_mode:
            return search_in_xml_special(file_path, search_string_nfc, is_exact)
        elif Constants.MODE_JSON in special_mode:
            return search_in_json_special(file_path, search_string_nfc, is_exact)
        elif Constants.MODE_ARCHIVE in special_mode:
            return search_in_archive_special(file_path, search_string_nfc, is_exact)
        elif Constants.MODE_EXCEL in special_mode:
            return search_in_excel_special(file_path, search_string_nfc, is_exact)

    # [v4.31.2 Fix] 빈 파일 체크를 최상단으로 이동 (게이트 로직에 막히지 않도록)
    try:
        if file_size is None:
            file_size = os.path.getsize(file_path)
        if file_size == 0:
            return (Constants.STATUS_SKIPPED, AppStrings.SKIP_EMPTY_FILE)
    except (OSError, IOError) as e:
        logger.debug(AppStrings.LOG_SCH_BINARY_CHECK_ERROR.format(file_path, e))
        return (Constants.STATUS_SKIPPED, AppStrings.ERROR_FILE_ACCESS_BINARY.format(e))

    # [v4.33.2 Fix] Truly complex unicode check: characters that change length when caseflipped.
    # ASCII와 한글은 안전하므로 제외하여 Rust 성능을 보존합니다.
    def _is_complex_unicode(s: str) -> bool:
        for c in s:
            if ord(c) < 128:
                continue  # ASCII is safe
            if 0xAC00 <= ord(c) <= 0xD7A3:
                continue  # Hangul Syllables are safe
            if 0x1100 <= ord(c) <= 0x11FF:
                continue  # Hangul Jamo are safe
            if len(c.casefold()) != len(c):
                return True
            if c.lower() != c.upper():
                return True  # Case-variant non-ASCII (Latin-1, etc.)
        return False

    has_complex_unicode = _is_complex_unicode(search_string_nfc)

    # 바이너리 파일 여부 확인
    is_binary = is_binary_file(file_path)

    if not special_mode or Constants.MODE_EXACT in special_mode:
        try:
            found_in_binary = _quick_search_bytes(file_path, search_string_nfc)
            if not found_in_binary:
                # [v4.31.7 Fix]
                # Exact Mode라도 '대소문자 무시(Case-insensitive)' 정책을 유지해야 함.
                # hElLo 같은 혼합 대소문자는 사전 바이트 검사를 통과하지 못할 수 있으므로,
                # 알파벳이 포함된 경우 게이트를 닫지 않고 반드시 Python 본전차(casefold)를 거치도록 함.
                is_exact = bool(special_mode and Constants.MODE_EXACT in special_mode)
                if not has_complex_unicode:
                    if not any(c.isalpha() for c in search_string_nfc):
                        return None
        except Exception as e:
            logger.debug(AppStrings.LOG_SCH_BINARY_CHECK_ERROR.format(file_path, e))
            pass

    # 인코딩 감지 (Rust 엔진 결과 신뢰성 판단 및 Python 폴백 대비)
    encoding = None
    try:
        if file_size is None:
            file_size = os.path.getsize(file_path)
    except Exception as e:
        logger.debug(AppStrings.LOG_SCH_ENCODING_ERROR.format(file_path, e))

    # [v4.33.3 Update] Rust 엔진 상시 호출 (용량 분기 제거)
    if HAS_RUST_ENGINE and (not special_mode or (special_mode and Constants.MODE_EXACT in special_mode)):
        try:
            # 리터럴 매칭을 위해 원본 문자열 전달 (re.escape 제거)
            # special_mode가 있을 경우 Rust에 전달
            rust_results = sf_engine.search_file(str(file_path), search_string_nfc, special_mode)

            if rust_results:
                # SearchMatch 객체를 (line, content, offset, length) 튜플로 변환하여 메타데이터 유지
                processed_matches = [(m.line, m.content, m.offset, m.length) for m in rust_results]

                if is_binary:
                    count = len(processed_matches)
                    return (file_path, count, [(1, AppStrings.MSG_BINARY_MATCH.format(count), None, None)])
                return (file_path, len(processed_matches), processed_matches)

            # Rust 결과가 0인 경우에도 유니코드 정합성(Case-folding)을 위해
            # 텍스트 파일에 한해 파이썬 엔진 폴백을 허용합니다 (기준 상향 조정 가능).
            if not is_binary:
                pass
            else:
                return None
        except Exception as e:
            logger.error(AppStrings.LOG_SCH_RUST_ENGINE_ERROR.format(file_path, e))

    try:
        # Python 검색 단계 (Rust가 실패했거나, 폴백이 결정된 경우)
        if file_size is None:
            file_size = os.path.getsize(file_path)

        if file_size < 10 * 1024 * 1024:
            with open(file_path, "r", encoding=encoding, errors="replace") as f_text:
                content = f_text.read()
            processed_content = strip_comments(content, ext)

            matches = []
            count = 0
            lines = processed_content.splitlines()
            search_fold = search_string_nfc.casefold()

            for i, line in enumerate(lines):
                if search_fold in line.casefold():
                    count += 1
                    matches.append((i + 1, line.strip()))

            if count > 0:
                if is_binary:
                    return (file_path, count, [(1, AppStrings.MSG_BINARY_MATCH.format(count))])
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

            # 혼합 대소문자(Mixed-Case) 무결성 보장을 위한 스트리밍 검색
            # 사용자 요청(v4.29.1)에 따라 Exact Mode도 대소문자 무시(casefold)로 통일
            # 따라서 is_exact 분기 없이 단일 로직 사용

            # 스트리밍 방식 (메모리 절약 + 대소문자 완벽 지원)
            current_line = 0
            matches = []
            count = 0

            # 파일 인코딩 감지를 위해 헤더만 먼저 읽기
            with open(file_path, "rb") as f_head:
                head = f_head.read(4096)
                detected_enc = detect_encoding_quickly(head)

            try:
                with open(file_path, "r", encoding=detected_enc, errors="replace") as f_text:
                    search_fold = search_string_nfc.casefold()
                    for line in f_text:
                        current_line += 1
                        if search_fold in line.casefold():
                            count += 1
                            matches.append((current_line, line.strip()))
            except Exception as e:
                logger.debug(AppStrings.LOG_SCH_STREAM_ERROR.format(e))
                return (Constants.STATUS_SKIPPED, AppStrings.ERROR_IO_DURING_SEARCH.format(e))

            if count > 0:
                if is_binary:
                    return (file_path, count, [(1, AppStrings.MSG_BINARY_MATCH.format(count))])
                return (file_path, count, matches)
            return None
    except (IOError, OSError) as e:
        logger.debug(AppStrings.LOG_SCH_ERROR_FILE.format(file_path, e))
        return (Constants.STATUS_SKIPPED, AppStrings.ERROR_IO_DURING_SEARCH.format(e))
    except Exception as e:
        logger.error(AppStrings.LOG_SCH_UNEXPECTED_ERROR.format(AppStrings.HEADER_FILE, file_path, e), exc_info=True)
        return (Constants.STATUS_SKIPPED, AppStrings.ERROR_UNEXPECTED_FILE.format(file_path, e))
    return None


def search_in_files_batch(
    file_batch: List[FileInfo],
    search_string: str,
    special_mode: Optional[str] = None,
    stop_event=None,
) -> Dict[str, List]:
    """
    배치 단위로 파일 검색을 수행하고 결과와 스킵된 파일 목록을 반환합니다.

    Args:
        file_batch (List[FileInfo]): 검색할 파일 정보(경로, 크기) 리스트
        search_string (str): 검색할 문자열
        special_mode (str, optional): 특수 검색 모드 (XML, JSON, Archive 등)
        stop_event: 중단 요청을 확인하기 위한 multiprocessing.Event 객체

    Returns:
        Dict[str, List]: "results" 키에는 검색 결과 리스트, "skipped" 키에는 스킵된 파일 목록 포함
    """
    results = []
    skipped = []
    for f_path, f_size in file_batch:
        if stop_event and stop_event.is_set():
            break
        res = search_in_file(f_path, search_string, f_size, special_mode)
        if isinstance(res, tuple) and res[0] == Constants.STATUS_SKIPPED:
            skipped.append((f_path, res[1]))
        elif res == Constants.STATUS_SKIPPED:
            skipped.append((f_path, AppStrings.ERROR_UNKNOWN))
        elif res:
            results.append(res)
    return {"results": results, "skipped": skipped}


def search_directory_fast(
    search_paths: List[str], search_string: str, extensions: Optional[List[str]] = None, **kwargs
) -> Dict[str, List]:
    """
    [Rust Engine Phase 2]
    Rust로 구현된 고성능 병렬 디렉토리 스캔(sf_engine.search_dir)을 호출하여
    검색어와 일치하는 내용을 포함하는 파일을 초고속으로 검색합니다.

    이 함수는 Python의 GIL(Global Interpreter Lock) 외부에서 병렬 스레드로 동작하므로
    대규모 데이터셋에서도 UI 프리징 없이 매우 빠른 성능을 보장합니다.

        extensions (List[str], optional): 검색할 파일 확장자 리스트 (예: ['txt', 'py']). None일 경우 모든 파일 검색
        special_mode (str, optional): JSON/XML 등 특수 검색 모드 및 정확히 일치 옵션
    """
    if not HAS_RUST_ENGINE:
        # Fallback to Python logic (Should be handled by caller, but safety check)
        return {"results": [], "skipped": []}

    try:
        rust_pattern = normalize_unicode(search_string)

        rust_exts = None
        if extensions:
            rust_exts = [ext.lstrip(".").lower() for ext in extensions]

        search_dir_func = getattr(sf_engine, "search_dir", None)
        if not search_dir_func:
            logger.warning(AppStrings.LOG_SYS_SF_ENGINE_NOT_FOUND.format("sf_engine.search_dir"))
            return {"results": [], "skipped": []}

        # sf_engine.search_dir(paths, pattern, extensions, special_mode) -> [(path, [SearchMatch, ...]), ...]
        raw_ret = search_dir_func(search_paths, rust_pattern, rust_exts, kwargs.get("special_mode"))

        formatted_results = []
        if raw_ret:
            for path, matches in raw_ret:
                # SearchMatch 객체 리스트를 (line, content, offset, length) 리스트로 변환
                match_tuples = [(m.line, m.content, m.offset, m.length) for m in matches]

                if is_binary_file(path):
                    cnt = len(match_tuples)
                    formatted_results.append((path, cnt, [(1, AppStrings.MSG_BINARY_MATCH.format(cnt), None, None)]))
                    continue

                formatted_results.append((path, len(match_tuples), match_tuples))

        return {"results": formatted_results, "skipped": []}

    except (IOError, OSError, RuntimeError) as e:
        logger.error(AppStrings.LOG_SCH_RUST_DIR_SEARCH_ERROR.format(e))
        raise e
    except Exception as e:
        logger.critical(f"Rust 디렉토리 검색 중 치명적 오류: {e}", exc_info=True)
        raise e


def find_files_with_keyword_fast(
    search_paths: List[str], search_string: str, extensions: Optional[List[str]] = None, **kwargs
) -> List[FileInfo]:
    """
    [Rust Engine Phase 3: Smart Scan]
    Rust 엔진을 사용하여 지정된 키워드가 포함된 파일들의 목록만을 빠르게 추출합니다.

    이 함수는 상세한 매치 위치(라인 번호 등)를 찾지 않고,
    단순히 '파일 내에 검색어가 존재하는지' 여부만 판단하여 파일 목록을 반환합니다.
    검색 대상 파일 목록을 미리 필터링하는 스마트 스캔(Smart Scan) 단계에서 사용됩니다.

    Args:
        search_paths (List[str]): 검색할 루트 디렉토리 경로 리스트
        search_string (str): 파일 내 존재 여부를 확인할 키워드
        extensions (List[str], optional): 검색할 파일 확장자 리스트

    Returns:
        List[FileInfo]: 검색어가 포함된 파일들의 (파일경로, 파일크기) 튜플 리스트
    """
    if not HAS_RUST_ENGINE:
        return []

    try:
        rust_pattern = normalize_unicode(search_string)

        rust_exts = None
        if extensions:
            rust_exts = [ext.lstrip(".").lower() for ext in extensions]

        find_func = getattr(sf_engine, "find_files_with_keyword", None)
        if not find_func:
            logger.warning(AppStrings.LOG_SYS_SF_ENGINE_NOT_FOUND.format("sf_engine.find_files_with_keyword"))
            return []

        # [v4.33.12 Fix] special_mode를 Rust 엔진에 전달하여 스마트 스캔 정밀도 확보
        special_mode = kwargs.get("special_mode")

        # sf_engine.find_files_with_keyword(paths, pattern, extensions, special_mode) -> [(path, size), ...]
        found_files = find_func(search_paths, rust_pattern, rust_exts, special_mode)
        return list(found_files)  # type: ignore
    except (IOError, OSError, RuntimeError) as e:
        logger.error(AppStrings.LOG_SCH_RUST_SMART_SCAN_ERROR.format(e))
        return []
    except Exception as e:
        logger.critical(f"Rust Smart Scan 중 예기치 못한 치명적 오류: {e}", exc_info=True)
        return []
