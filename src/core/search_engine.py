import os
import re
import mmap
import bisect
from os.path import splitext
import logging
import unicodedata
from typing import Optional, Tuple, List, Union, Dict, Pattern

from utils.app_strings import AppStrings

# 엑셀 파일 확장자 목록 (Rust 엔진에서 제외하고 별도 처리하기 위함)
EXCEL_EXTS = {".xlsx", ".xlsm", ".xls", ".xlsb"}

# 로거 설정
logger = logging.getLogger("StringFinder.SearchEngine")

# Rust 검색 엔진 연동 시도
try:
    import sf_engine

    HAS_RUST_ENGINE = True
    logger.info(AppStrings.LOG_RUST_SUCCESS)
except ImportError:
    HAS_RUST_ENGINE = False
    logger.warning(AppStrings.LOG_RUST_FALLBACK)

# 타입 별칭 정의
SearchMatch = Tuple[int, str]  # (라인번호, 내용)
SearchResult = Tuple[str, int, List[SearchMatch]]  # (파일경로, 매칭수, 매칭목록)
SkippedResult = Tuple[str, str]  # ("SKIPPED", 사유)
FileInfo = Tuple[str, int]  # (파일경로, 파일크기)


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
    except Exception:
        return False


def detect_encoding_quickly(data: bytes) -> str:
    """
    바이트 데이터의 인코딩을 빠르게 판별합니다.
    주로 UTF-8과 CP949(EUC-KR)를 구분하는 데 사용됩니다.

    Args:
        data (bytes): 인코딩을 판별할 원본 바이트 데이터

    Returns:
        str: 판별된 인코딩 이름 (예: 'utf-8', 'cp949', 'utf-16')
    """
    if not data:
        return AppStrings.ENC_UTF8

    # BOM(Byte Order Mark) 확인을 통한 인코딩 판별
    if data.startswith(b"\xef\xbb\xbf"):
        return AppStrings.ENC_UTF8_SIG
    if data.startswith(b"\xff\xfe"):
        return AppStrings.ENC_UTF16
    if data.startswith(b"\xfe\xff"):
        return AppStrings.ENC_UTF16_BE

    # UTF-8 디코딩 시도 (가장 일반적인 케이스)
    try:
        data.decode(AppStrings.ENC_UTF8)
        return AppStrings.ENC_UTF8
    except UnicodeDecodeError:
        pass

    # CP949(한국어 윈도우 기본 인코딩) 디코딩 시도
    try:
        data.decode(AppStrings.ENC_CP949)
        return AppStrings.ENC_CP949
    except UnicodeDecodeError:
        pass

    return AppStrings.ENC_UTF8  # 모든 시도 실패 시 기본값으로 UTF-8 반환


class SearchEngine:
    """
    검색 환경 및 전반적인 설정을 관리하는 마스터 엔진 클래스입니다.
    현재는 주요 검색 로직이 최적화를 위해 전역 함수로 분리되어 있으나,
    향후 확장성을 위해 인스턴스 형태로 유지합니다.
    """

    def __init__(self):
        pass


class FileScanner:
    """
    검색 대상이 되는 파일 리스트를 효율적으로 스캔하고 필터링하는 클래스입니다.
    """

    def __init__(self, folders, extensions, filename_filter=None, stop_check_callback=None):
        """
        스캐너를 초기화합니다.

        Args:
            folders (list): 검색할 폴더 경로 리스트
            extensions (list): 검색할 확장자 리스트 (예: ['.txt', '.py'])
            filename_filter (str): 파일명에 포함되어야 할 필터링 문자열
            stop_check_callback (callable): 중단 여부를 반환하는 함수 (True 반환 시 중단)
        """
        self.folders = folders
        self.extensions = [(e if e.startswith(".") else "." + e).lower() for e in extensions]
        self.filename_filter = filename_filter.lower() if filename_filter else None
        self.stop_check_callback = stop_check_callback

    def scan(self):
        """
        설정된 폴더들을 순회하며 조건에 맞는 파일 리스트를 수집합니다.
        os.scandir를 사용하여 파일 속성(크기) 접근 시 시스템 콜을 최소화합니다.
        """
        file_list = []
        for folder in self.folders:
            if self.stop_check_callback and self.stop_check_callback():
                break

            if not os.path.exists(folder):
                continue

            self._scan_recursive(folder, file_list)
        return file_list

    def _scan_recursive(self, folder, file_list):
        """
        재귀적으로 폴더를 스캔하며 파일 정보를 수집합니다.
        """
        try:
            # os.scandir는 컨텍스트 매니저로 사용하여 리소스를 확실히 해제합니다.
            with os.scandir(folder) as entries:
                for entry in entries:
                    if self.stop_check_callback and self.stop_check_callback():
                        return

                    try:
                        # 심볼릭 링크는 보안 및 무한 루프 방지를 위해 기본적으로 따라가지 않거나 상황에 맞게 처리
                        # 여기서는 is_dir(), is_file() 호출 시 follow_symlinks=False를 명시하지 않으면 기본값(True)일 수 있음
                        # 성능을 위해 stat() 호출 최소화
                        if entry.is_dir():
                            self._scan_recursive(entry.path, file_list)
                        elif entry.is_file():
                            ext = splitext(entry.name)[1].lower()
                            if ext in self.extensions:
                                if self.filename_filter and self.filename_filter not in entry.name.lower():
                                    continue

                                # os.scandir의 entry.stat()은 윈도우에서 추가 시스템 콜 없이 캐시된 정보를 반환함
                                size = entry.stat().st_size
                                file_list.append((entry.path, size))
                    except (OSError, PermissionError):
                        continue
        except (OSError, PermissionError):
            # 폴더 접근 권한이 없는 경우 무시
            pass


def strip_comments(content: str, ext: str) -> str:
    """
    파일 확장자에 따라 주석을 제거하고 공백으로 대체합니다 (줄 번호 유지).
    """

    def replacer(match):
        # 개행 문자를 제외한 모든 문자를 공백으로 치환하여 위치와 줄 번호를 보존함
        return re.sub(r"[^\n\r]", " ", match.group())

    if ext in [".json", ".js", ".c", ".cpp", ".cs", ".java"]:
        # // 또는 /* */ 주석 제거
        return re.sub(r"//.*?\n|/\*.*?\*/", replacer, content, flags=re.S)
    elif ext in [".xml", ".html"]:
        # <!-- --> 주석 제거
        return re.sub(r"<!--.*?-->", replacer, content, flags=re.S)
    elif ext in [".py", ".rb", ".sh", ".yaml", ".yml"]:
        # # 주석 제거
        return re.sub(r"#.*?\n", replacer, content)
    elif ext in [".sql"]:
        # -- 주석 제거
        return re.sub(r"--.*?\n", replacer, content)
    return content


def search_in_excel(file_path: str, search_string: str) -> Optional[Union[SearchResult, SkippedResult]]:
    """
    Excel 파일(.xlsx, .xlsm, .xls, .xlsb, .ods) 내의 모든 시트에서 문자열을 검색합니다.
    python-calamine(Rust 엔진 기반)을 사용하여 대용량 파일도 초고속으로 처리합니다.
    """
    try:
        from python_calamine import CalamineWorkbook

        # Calamine은 별도의 read-only 모드 설정 없이 기본적으로 초고속 읽기 전용입니다.
        try:
            workbook = CalamineWorkbook.from_path(file_path)
        except Exception as e:
            # 지원하지 않는 형식이거나 파일 오류 시 SKIPPED 처리
            return ("SKIPPED", f"Excel 파일 로드 오류: {e}")

        count = 0
        matches = []
        search_string = normalize_unicode(search_string).lower()

        for sheet_name in workbook.sheet_names:
            sheet = workbook.get_sheet_by_name(sheet_name)
            # sheet.to_python()은 모든 데이터를 리스트의 리스트로 반환하며 데이터가 없을 때까지 고속 스캔합니다.
            for row_idx, row in enumerate(sheet.to_python()):
                for col_idx, cell_value in enumerate(row):
                    if cell_value is not None:
                        val_str = normalize_unicode(str(cell_value)).lower()
                        if search_string in val_str:
                            count += 1
                            # 엑셀 좌표 형식 (예: Sheet1!A1) 계산
                            col_letter = ""
                            temp_col = col_idx
                            while temp_col >= 0:
                                col_letter = chr(65 + (temp_col % 26)) + col_letter
                                temp_col = (temp_col // 26) - 1

                            pos = f"{sheet_name}!{col_letter}{row_idx + 1}"
                            matches.append((pos, str(cell_value)))

        if count > 0:
            return (file_path, count, matches)
    except ImportError:
        logger.error(AppStrings.ERROR_EXCEL_CALAMINE)
        return (AppStrings.STATUS_SKIPPED, AppStrings.ERROR_EXCEL_CALAMINE)
    except Exception as e:
        # python-calamine 라이브러리 부재 시 경고 메시지 출력
        if "calamine" in str(e).lower():
            logger.error(AppStrings.ERROR_EXCEL_CALAMINE)
            return (AppStrings.STATUS_SKIPPED, AppStrings.ERROR_EXCEL_CALAMINE)

        logger.debug(AppStrings.ERROR_EXCEL_EXCEPTION.format(e))
        return (AppStrings.STATUS_SKIPPED, AppStrings.ERROR_EXCEL_EXCEPTION.format(e))
    return None


def search_in_json_special(
    file_path: str, search_string: str, exact_match: bool = False
) -> Union[SearchResult, SkippedResult, List]:
    """
    JSON 파일을 파싱하여 값(Value)들 중에서만 검색합니다. 주석은 무시합니다.
    """
    try:
        import json

        with open(file_path, "rb") as f:
            head = f.read(1024)
            encoding = detect_encoding_quickly(head)

        with open(file_path, "r", encoding=encoding, errors="replace") as f:
            raw_content = f.read()

        # JSON 파일은 표준적으로 주석을 지원하지 않으므로 strip_comments 건너뛰기
        # (일부 JSON 파서는 주석을 허용하지만, 주석 제거 정규식이 URL 등을 손상시킬 수 있음)
        processed_content = raw_content
        # 후행 쉼표 처리: 쉼표 바로 뒤에 닫는 괄호가 오는 경우만 처리 (개행/공백 허용)
        # 예: {"a": 1,} 또는 [1, 2,] 같은 경우만 쉼표 제거
        processed_content = re.sub(r",(\s*)([}\]])", r"\1\2", processed_content)

        try:
            data = json.loads(processed_content, strict=False)
        except json.JSONDecodeError:
            return (AppStrings.STATUS_SKIPPED, AppStrings.ERROR_JSON_PARSE)

        matches = []
        search_string = normalize_unicode(search_string).lower()
        newline_indices = [i for i, char in enumerate(raw_content) if char == "\n"]

        def get_line_no(pos):
            import bisect

            return bisect.bisect_left(newline_indices, pos) + 1

        search_state = {"last_pos": 0, "limit_hit": False}
        MAX_JSON_DEPTH = 1000

        def _recursive_search(obj, path="", depth=0):
            """
            JSON 구조를 재귀적으로 탐색하여 문자열을 검색합니다.

            Args:
                obj: 현재 탐색 중인 JSON 객체 (dict, list, str 등)
                path (str): 현재 객체의 키 경로
                depth (int): 현재 재귀 깊이

            Returns:
                int: 발견된 매칭 수
            """
            if depth > MAX_JSON_DEPTH:
                if not search_state["limit_hit"]:
                    logger.warning(AppStrings.LOG_JSON_LIMIT.format(file_path))
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

                is_match = False
                if exact_match:
                    is_match = val_lower == search_string
                else:
                    is_match = search_string in val_lower

                if is_match:
                    count += 1
                    # 위치 탐색 시 오탐 방지: 단어 경계(\b)를 활용하여 정확한 값의 위치를 찾음
                    # 값의 앞뒤가 영문/숫자가 아닌 경우에도 대응하기 위해 \b 사용

                    pattern_str = None
                    if isinstance(obj, str):
                        # JSON 문자열은 내부에 이스케이프(")가 있을 수 있으므로 dumps로 원형 복원 시도
                        # ensure_ascii=False여야 한글이 \uXXXX로 변환되지 않고 그대로 검색됨
                        try:
                            json_dumped = json.dumps(obj, ensure_ascii=False)
                            if json_dumped.startswith('"') and json_dumped.endswith('"'):
                                # 앞뒤 따옴표 제거 (내부 이스케이프 문자는 유지됨)
                                content_body = json_dumped[1:-1]
                                pattern_str = re.escape(content_body)
                        except Exception:
                            pass

                    if not pattern_str:
                        pattern_str = re.escape(val_raw)

                    if val_raw.isalnum() or (val_raw.startswith('"') and val_raw.endswith('"')):
                        pattern_str = rf"\b{pattern_str}\b"

                    pattern = re.compile(pattern_str, re.IGNORECASE)
                    match = pattern.search(processed_content, search_state["last_pos"])

                    if match:
                        line_no = get_line_no(match.start())
                        search_state["last_pos"] = match.end()
                    else:
                        match_retry = pattern.search(processed_content)
                        if match_retry:
                            line_no = get_line_no(match_retry.start())
                            search_state["last_pos"] = match_retry.end()
                        else:
                            line_no = 1

                    matches.append((line_no, path or "root", val_raw))
            return count

        try:
            total_count = _recursive_search(data)
        except RecursionError:
            logger.warning(AppStrings.LOG_JSON_DEPTH_RECURSION.format(file_path))
            return ("SKIPPED", "JSON 재귀 깊이 제한 초과 (RecursionError)")

        if search_state["limit_hit"]:
            return ("SKIPPED", "JSON 재귀 깊이 제한 초과 (1000+)")

        if total_count > 0:
            return (file_path, total_count, matches)
        return []  # 발견된 것 없음
    except Exception as e:
        logger.debug(AppStrings.ERROR_SEARCH_ERROR_IN_FILE.format(file_path, e))
        return (AppStrings.STATUS_SKIPPED, AppStrings.ERROR_JSON_PARSE_EX.format(e))


def search_in_xml_special(file_path, search_string, exact_match=False):
    """
    XML 파일을 파싱하여 검색합니다. 주석은 무시합니다.
    """
    try:
        import xml.parsers.expat

        # 주석 제거를 위해 먼저 읽음
        with open(file_path, "rb") as f:
            head = f.read(1024)
            encoding = detect_encoding_quickly(head)

        with open(file_path, "r", encoding=encoding, errors="replace") as f:
            raw_content = f.read()
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
                    val_str = normalize_unicode(str(v))
                    val_lower = val_str.lower()

                    is_match = (val_lower == search_string) if exact_match else (search_string in val_lower)
                    if is_match:
                        count += 1
                        line = self.parser.CurrentLineNumber
                        matches.append((line, str(k), val_str))

            def char_data(self, data):
                nonlocal count
                text = normalize_unicode(data).strip()
                if text:
                    text_lower = text.lower()
                    is_match = (text_lower == search_string) if exact_match else (search_string in text_lower)
                    if is_match:
                        count += 1
                        line = self.parser.CurrentLineNumber
                        name = self.current_tags[-1] if self.current_tags else "root"
                        matches.append((line, name, text))

            def parse_content(self, content):
                self.parser.Parse(content, True)

        searcher = XMLSearcher()
        try:
            searcher.parse_content(processed_content)
        except Exception as e:
            return ("SKIPPED", f"XML 파싱 오류: {e}")

        if count > 0:
            return (file_path, count, matches)
        return []
    except Exception as e:
        logger.debug(AppStrings.ERROR_SEARCH_ERROR_IN_FILE.format(file_path, e))
        return (AppStrings.STATUS_SKIPPED, AppStrings.ERROR_XML_PARSE_EX.format(e))


def _quick_search_bytes(file_path: str, search_text: str) -> bool:
    """
    파일 전체를 디코딩하지 않고 바이트 패턴 매칭으로 검색어 존재 여부를 빠르게 확인합니다.
    (UTF-8, CP949, UTF-16LE 인코딩을 고려)
    True를 반환하면 파일 내에 검색어(의 바이트 표현)가 존재할 가능성이 높습니다.
    """
    patterns = []
    # 일반적인 인코딩들에 대해 검색어 바이트열 생성
    # 순서: UTF-8 (가장 흔함) -> CP949 (한글 윈도우) -> UTF-16LE (윈도우 시스템 파일)
    encodings = ["utf-8", "cp949", "utf-16-le"]

    for enc in encodings:
        try:
            pat = search_text.encode(enc)
            if pat:
                patterns.append(pat)
        except UnicodeEncodeError:
            pass

    if not patterns:
        return False

    try:
        # 파일 크기 확인 (여기서는 os.path.getsize를 쓰지만, 호출처에서 넘겨준다면 더 좋음)
        # 0바이트 파일은 패스
        if os.path.getsize(file_path) == 0:
            return False

        with open(file_path, "rb") as f:
            # mmap을 사용하여 메모리 복사 없이 고속 검색
            with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                for pat in patterns:
                    if mm.find(pat) >= 0:
                        return True
    except (ValueError, OSError) as e:
        # 파일에 접근조차 못하는 경우는 상위에서 SKIPPED 처리하도록 예외 다시 던짐
        raise e

    return False


def search_in_file(
    file_path: str,
    search_string: str,
    file_size: Optional[int] = None,
    special_mode: Optional[str] = None,
    compiled_pattern: Optional[Pattern] = None,
) -> Optional[Union[SearchResult, SkippedResult]]:
    """
    고성능 검색 엔진: mmap과 바이트 레벨 정규표현식을 조합하여 파일을 검색합니다.
    - 대량의 파일 검색 시 메모리 사용량을 최소화합니다.
    - 주석 제외 검색 및 XML/JSON 특수 모드를 지원합니다.
    - 파일의 인코딩을 자동으로 판별하여 처리합니다.

    Args:
        compiled_pattern (re.Pattern, optional): 미리 컴파일된 정규식 패턴 (성능 최적화용)
    """
    search_string_nfc = normalize_unicode(search_string)
    ext = splitext(file_path)[1].lower()

    # Excel 검색 (Binary Pre-check 제외: Zip 압축 파일이므로 바이트 매칭 불가)
    if ext in [".xlsx", ".xlsm", ".xls", ".xlsb"]:
        return search_in_excel(file_path, search_string_nfc)

    # [Binary Detection]
    # 명시적인 엑셀 파일이 아닌 경우, 일반 바이너리 파일인지 먼저 확인합니다.
    # 바이너리 파일일 경우 텍스트 디코딩 시 깨짐 현상이 발생하므로 별도 처리합니다.
    is_binary = is_binary_file(file_path)

    # [Optimization] Binary Pre-check
    # 파일을 디코딩하거나 파싱하기 전에, 바이트 단위로 검색어가 있는지 먼저 확인합니다.
    try:
        found_in_binary = _quick_search_bytes(file_path, search_string_nfc)
        if not found_in_binary:
            return None
    except Exception as e:
        logger.debug(AppStrings.LOG_BINARY_CHECK_ERROR.format(file_path, e))
        return (AppStrings.STATUS_SKIPPED, AppStrings.ERROR_FILE_PROCESSING.format(file_path, e))

    # [Rust Engine Integration]
    # 특수 모드가 아니고 Rust 엔진이 사용 가능한 경우, Rust 모듈로 즉시 위임하여 극강의 성능 달성
    if HAS_RUST_ENGINE and not special_mode:
        try:
            # Rust 모듈은 (라인번호, 라인내용) 튜플 리스트를 반환함
            # 인코딩은 Rust 내부에서 UTF-8 -> EUC-KR 순으로 시도함
            # 기존 로직(Literal Search)과 동일하게 작동하도록 패턴 이스케이프 처리
            rust_pattern = re.escape(search_string_nfc)
            rust_results = sf_engine.search_file(str(file_path), str(rust_pattern))
            if rust_results:
                # 바이너리 파일인 경우 텍스트 깨짐 방지를 위해 플레이스홀더 반환
                if is_binary:
                    count = len(rust_results)
                    return (file_path, count, [(1, f"[바이너리 파일 내 {count}개 일치 항목 존재]")])
                return (file_path, len(rust_results), rust_results)

            # Rust가 빈 결과를 반환한 경우, 인코딩 문제로 검색 누락 가능성이 있으므로
            # Python 폴백으로 재검색 (CP949 등 비 UTF-8 인코딩 대응)
            logger.debug(AppStrings.LOG_RUST_RETRY_PYTHON.format(file_path))
            # 아래 Python 로직으로 계속 진행
        except Exception as e:
            logger.error(AppStrings.LOG_RUST_ENGINE_ERROR.format(file_path, e))
            # 실패 시 아래 파이썬 로직으로 폴백 (Fallback)

    # 특수 검색 모드 (XML/JSON)
    if special_mode:
        is_xml = "XML" in special_mode
        is_json = "JSON" in special_mode
        is_exact = "전체 일치" in special_mode

        res = None
        if is_xml and ext == ".xml":
            res = search_in_xml_special(file_path, search_string_nfc, exact_match=is_exact)
        elif is_json and ext == ".json":
            res = search_in_json_special(file_path, search_string_nfc, exact_match=is_exact)

        if res == []:
            return None  # 매치 없음

        # 결과 반환 (이미 (SKIPPED, reason) 튜플이거나 정상 결과 튜플임)
        if res:
            return res

    # 일반 텍스트 검색 (주석 제외 요구사항 반영을 위해 전체 읽기 방식 병행)
    try:
        if file_size is None:
            file_size = os.path.getsize(file_path)

        if file_size == 0:
            return None

        # 대용량 파일이 아닐 경우(주석 제거를 위해 전체 읽기)
        if file_size < 10 * 1024 * 1024:
            with open(file_path, "rb") as f:
                head = f.read(1024)
                encoding = detect_encoding_quickly(head)

            with open(file_path, "r", encoding=encoding, errors="replace") as f:
                content = f.read()
            processed_content = strip_comments(content, ext)

            matches = []
            count = 0
            lines = processed_content.splitlines()
            search_low = search_string_nfc.lower()

            for i, line in enumerate(lines):
                if search_low in line.lower():
                    count += 1
                    matches.append((i + 1, line.strip()))

            if count > 0:
                # 바이너리 파일인 경우 텍스트 깨짐 방지를 위해 플레이스홀더 반환
                if is_binary:
                    # 바이너리 파일은 위치 정보를 정확히 알기 어렵고 텍스트 노출 시 깨짐이 심하므로 요약만 제공
                    return (file_path, count, [(1, f"[바이너리 파일 내 {count}개 일치 항목 존재]")])
                return (file_path, count, matches)
            return None

        # 10MB 이상 대용량 파일은 mmap 유지 (주석 처리가 까다로움 - 일단 mmap으로 수행하되 주석이 많은 언어는 경고 처리 가능)
        with open(file_path, "rb") as f:
            with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                search_pattern_bytes = search_string_nfc.encode("utf-8", errors="ignore")
                if not search_pattern_bytes:
                    return None

                flags = re.IGNORECASE
                pattern = re.escape(search_pattern_bytes)
                newline_offsets = [m.start() for m in re.finditer(b"\n", mm)]
                mm.seek(0)

                matches = []
                count = 0
                for match in re.finditer(pattern, mm, flags):
                    count += 1
                    start_pos = match.start()
                    line_no = bisect.bisect_right(newline_offsets, start_pos) + 1
                    line_start = 0 if line_no == 1 else newline_offsets[line_no - 2] + 1
                    line_end = newline_offsets[line_no - 1] if line_no <= len(newline_offsets) else file_size
                    # mmap은 바이트이므로 디코딩 필요. 큰 파일도 인코딩 감지를 해야 하지만,
                    # 성능상 UTF-8 우선 시도 후 실패시 replace 처리
                    line_content = mm[line_start:line_end].decode("utf-8", errors="replace").strip()
                    matches.append((line_no, line_content))

                if count > 0:
                    # 바이너리 파일인 경우 텍스트 깨짐 방지를 위해 플레이스홀더 반환
                    if is_binary:
                        return (file_path, count, [(1, f"[바이너리 파일 내 {count}개 일치 항목 존재]")])
                    return (file_path, count, matches)
    except Exception as e:
        logger.debug(AppStrings.LOG_SEARCH_ERROR_FILE.format(file_path, e))
        return (AppStrings.STATUS_SKIPPED, AppStrings.ERROR_IO_DURING_SEARCH.format(e))
    return None


def search_in_files_batch(
    file_batch: List[FileInfo],
    search_string: str,
    special_mode: Optional[str] = None,
    compiled_patterns: Optional[Dict[str, Pattern]] = None,
) -> Dict[str, List]:
    """
    배치 단위 검색을 수행하고 결과와 스킵된 목록을 반환합니다.

    Args:
        compiled_patterns (dict, optional): 인코딩별 미리 컴파일된 정규식 패턴 딕셔너리
    """
    results = []
    skipped = []
    for f_path, f_size in file_batch:
        # 인코딩별 패턴 선택 (현재는 utf-8 기본 사용, 향후 파일별 인코딩 감지 가능)
        compiled_pattern = compiled_patterns.get("utf-8") if compiled_patterns else None
        res = search_in_file(f_path, search_string, f_size, special_mode, compiled_pattern)
        if isinstance(res, tuple) and res[0] == "SKIPPED":
            skipped.append((f_path, res[1]))
        elif res == "SKIPPED":
            skipped.append((f_path, "알 수 없는 오류"))
        elif res:
            results.append(res)
    return {"results": results, "skipped": skipped}


def search_directory_fast(
    search_paths: List[str], search_string: str, extensions: Optional[List[str]] = None
) -> Dict[str, List]:
    """
    [Rust Engine Phase 2]
    Rust의 병렬 디렉토리 스캔(sf_engine.search_dir)을 사용하여 극강의 속도로 검색합니다.

    Args:
        search_paths (list): 검색할 루트 디렉토리 경로 리스트
        search_string (str): 검색할 문자열
        extensions (list, optional): 필터링할 확장자 리스트 (예: ['txt', 'py'])

    Returns:
        dict: {"results": [(path, line, content), ...], "skipped": []}
    """
    if not HAS_RUST_ENGINE:
        # Fallback to Python logic (Should be handled by caller, but safety check)
        return {"results": [], "skipped": []}

    try:
        # Rust 엔진은 Literal Search가 기본이 아니므로, Python과 동일하게 동작하도록 escape 처리
        # 단, search_dir 내부에서 Regex를 사용하므로 escape 필수
        rust_pattern = re.escape(normalize_unicode(search_string))

        # extensions 전처리: 점(.) 제거 및 소문자 변환
        # [Fix] 엑셀 파일은 Rust 엔진에서 검색 시 바이너리(Zip)로 처리되어 깨짐이 발생하므로 제외
        rust_exts = None
        if extensions:
            rust_exts = [
                ext.lstrip(".").lower()
                for ext in extensions
                if (ext if ext.startswith(".") else "." + ext).lower() not in EXCEL_EXTS
            ]

        # [Defensive] sf_engine.search_dir 속성 확인
        search_dir_func = getattr(sf_engine, "search_dir", None)
        if not search_dir_func:
            logger.warning(AppStrings.LOG_SF_ENGINE_NOT_FOUND.format("sf_engine.search_dir"))
            return {"results": [], "skipped": []}

        # sf_engine.search_dir 호출 (리스트 반환: [(path, line, content), ...])
        raw_results = search_dir_func(search_paths, rust_pattern, rust_exts)

        # 결과 포맷팅 (기존 로직과 호환성 유지)
        # Rust 반환: (path, line, content) -> Python 기존: (path, count, matches=[(line, content)])
        # 하지만 여기서 바로 변환하기보다, Worker에서 처리하기 쉽게 Raw Data 구조로 넘기거나 변환.
        # 기존 search_in_files_batch는 "results" 리스트에 (file_path, count, matches) 튜플을 담음.

        formatted_results = []

        # Rust 결과는 플랫한 리스트이므로, 파일별로 그룹핑이 필요함
        # sf_engine.search_dir은 (path, line, content)의 리스트를 반환함 (순서는 보장 안됨)
        # Python 측에서 Grouping 수행
        from collections import defaultdict

        grouped = defaultdict(list)

        for path, line, content in raw_results:
            grouped[path].append((line, content))

        for path, matches in grouped.items():
            # [Fix] Rust 결과에서도 바이너리 파일 감지 및 보호 로직 적용
            if is_binary_file(path):
                cnt = len(matches)
                formatted_results.append((path, cnt, [(1, f"[바이너리 파일 내 {cnt}개 일치 항목 존재]")]))
                continue

            # 라인 번호 순 정렬
            matches.sort(key=lambda x: x[0])
            formatted_results.append((path, len(matches), matches))

        return {"results": formatted_results, "skipped": []}  # Rust 엔진은 현재 Skip 사유를 반환하지 않음 (빈 리스트)

    except Exception as e:
        logger.error(AppStrings.LOG_RUST_DIR_SEARCH_ERROR.format(e))
        raise e


def find_files_with_keyword_fast(
    search_paths: List[str], search_string: str, extensions: Optional[List[str]] = None
) -> List[FileInfo]:
    """
    [Rust Engine Phase 3: Smart Scan]
    Rust 엔진을 사용하여 '검색어가 포함된 파일' 목록만 빠르게 수집합니다.
    내용 검색(Line identification)은 하지 않고, 바이너리 포함 여부만 확인합니다.

    Args:
        search_paths (list): 검색할 루트 경로 리스트
        search_string (str): 확인할 키워드
        extensions (list): 확장자 필터 (옵션)

    Returns:
        list: 검색어가 포함된 (파일경로, 파일크기) 튜플 리스트
    """
    if not HAS_RUST_ENGINE:
        return []

    try:
        # Rust 엔진은 Regex 기반이므로 escape 처리 및 유니코드 정규화
        rust_pattern = re.escape(normalize_unicode(search_string))

        rust_exts = None
        if extensions:
            rust_exts = [ext.lstrip(".").lower() for ext in extensions]

        # [Defensive] sf_engine.find_files_with_keyword 속성 확인
        find_func = getattr(sf_engine, "find_files_with_keyword", None)
        if not find_func:
            logger.warning(AppStrings.LOG_SF_ENGINE_NOT_FOUND.format("sf_engine.find_files_with_keyword"))
            return []

        # sf_engine.find_files_with_keyword(paths, pattern, extensions) -> [(path, size), ...]
        found_files = find_func(search_paths, rust_pattern, rust_exts)
        return found_files
    except Exception as e:
        logger.error(AppStrings.LOG_RUST_SMART_SCAN_ERROR.format(e))
        return []
