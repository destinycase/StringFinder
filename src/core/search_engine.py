import os
import re
import mmap
import json
from os.path import splitext
import logging
import unicodedata
from typing import Optional, Tuple, List, Union, Dict

from utils.app_strings import AppStrings
from utils.constants import Constants

# 엑셀 파일 확장자 목록 (Rust 엔진에서 검색이 불가능하므로 별도 처리가 필요함)
EXCEL_EXTS = {".xlsx", ".xlsm", ".xls", ".xlsb"}

# 로거 설정
logger = logging.getLogger("StringFinder.SearchEngine")

# Rust 검색 엔진 라이브러리 연동 (설치되어 있고 호환되는 경우에만 활성화됨)
try:
    import sf_engine

    HAS_RUST_ENGINE = True
    logger.info(AppStrings.LOG_SYS_RUST_SUCCESS)
except ImportError:
    HAS_RUST_ENGINE = False
    logger.warning(AppStrings.LOG_SYS_RUST_FALLBACK)

# 검색 결과 및 보조 데이터 타입을 위한 별칭 (Type Alias)
SearchMatch = Tuple[int, str]  # (행 번호, 해당 행의 텍스트 내용)
SearchResult = Tuple[str, int, List[SearchMatch]]  # (절대 파일 경로, 총 매칭 개수, 상세 매칭 리스트)
SkippedResult = Tuple[str, str]  # ("SKIPPED", 스킵된 상세 사유)
FileInfo = Tuple[str, int]  # (파일 경로, 파일 크기 단위: Bytes)


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
    주로 UTF-8과 CP949(EUC-KR)를 구분하는 데 사용됩니다.

    Args:
        data (bytes): 인코딩을 판별할 원본 바이트 데이터

    Returns:
        str: 판별된 인코딩 이름 (예: 'utf-8', 'cp949', 'utf-16')
    """
    if not data:
        return Constants.ENC_UTF8

    # BOM(Byte Order Mark) 확인을 통한 인코딩 판별
    if data.startswith(b"\xef\xbb\xbf"):
        return Constants.ENC_UTF8_SIG
    if data.startswith(b"\xff\xfe"):
        return Constants.ENC_UTF16
    if data.startswith(b"\xfe\xff"):
        return Constants.ENC_UTF16_BE

    # UTF-8 디코딩 시도 (가장 일반적인 케이스)
    try:
        data.decode(Constants.ENC_UTF8)
        return Constants.ENC_UTF8
    except UnicodeDecodeError:
        pass

    # CP949(한국어 윈도우 기본 인코딩) 디코딩 시도
    try:
        data.decode(Constants.ENC_CP949)
        return Constants.ENC_CP949
    except UnicodeDecodeError:
        pass

    return Constants.ENC_UTF8  # 모든 시도 실패 시 기본값으로 UTF-8 반환


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
        logger.debug(f"Error reading file {file_path}: {e}")
        raise e


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
            filename_filter (str 또는 list): 파일 이름에 포함되어야 할 필터링 단어
            stop_check_callback (callable): 검색 중단 여부를 수시로 확인하는 콜백 함수 (True 반환 시 루프 탈출)
        """
        self.folders = folders
        # 확장자를 소문자로 정규화하고 마침표(.)를 포함하도록 보장합니다.
        self.extensions = [(e if e.startswith(".") else "." + e).lower() for e in extensions]

        # 파일 이름 필터는 항상 리스트 구조로 관리하여 다중 필터를 지원합니다.
        if not filename_filter:
            self.filename_filters = []
        elif isinstance(filename_filter, str):
            self.filename_filters = [filename_filter.lower()]
        else:
            self.filename_filters = [f.lower() for f in filename_filter]
        self.stop_check_callback = stop_check_callback

    def scan(self):
        """
        설정된 폴더들을 순회하며 조건에 맞는 파일 리스트를 수집합니다.
        os.scandir를 사용하여 파일 속성(크기) 접근 시 시스템 콜을 최소화합니다.
        """
        file_list = []
        visited = set()
        for folder in self.folders:
            if self.stop_check_callback and self.stop_check_callback():
                break

            if not os.path.exists(folder):
                continue

            # 정규화된 절대 경로 사용
            real_folder = os.path.realpath(folder)
            if real_folder in visited:
                continue
            visited.add(real_folder)

            self._scan_recursive(folder, file_list, visited)
        return file_list

    def _scan_recursive(self, folder, file_list, visited=None):
        """
        재귀적으로 폴더를 스캔하며 파일 정보를 수집합니다.
        visited: 심볼릭 링크 순환 방지를 위한 방문 기록 (realpath 기준)
        """
        if visited is None:
            visited = set()
        try:
            # os.scandir는 컨텍스트 매니저로 사용하여 리소스를 확실히 해제합니다.
            with os.scandir(folder) as entries:
                for entry in entries:
                    if self.stop_check_callback and self.stop_check_callback():
                        return

                    # [GIL] 대규모 스캔 중 UI 스레드에 틈을 주기 위해 아주 짧게 양보
                    # 1000개 정도마다 한 번씩만 양보하여 성능 저하 최소화
                    if hasattr(self, "_yield_counter"):
                        self._yield_counter += 1
                        if self._yield_counter % 1000 == 0:
                            import time

                            time.sleep(Constants.YIELD_SLEEP_TIME)
                    else:
                        self._yield_counter = 1

                    try:
                        # 심볼릭 링크는 보안 및 무한 루프 방지를 위해 기본적으로 따라가지 않거나 상황에 맞게 처리
                        # 여기서는 is_dir(), is_file() 호출 시 follow_symlinks=False를 명시하지 않으면 기본값(True)일 수 있음
                        # 성능을 위해 stat() 호출 최소화
                        if entry.is_dir():
                            # 심볼릭 링크 순환 방지
                            real_path = os.path.realpath(entry.path)
                            if real_path not in visited:
                                visited.add(real_path)
                                self._scan_recursive(entry.path, file_list, visited)
                        elif entry.is_file():
                            ext = splitext(entry.name)[1].lower()
                            if ext in self.extensions:
                                # 파일명 필터링 (OR 조건)
                                if self.filename_filters:
                                    import fnmatch

                                    is_matched = False
                                    fname_lower = entry.name.lower()
                                    for f_pattern in self.filename_filters:
                                        # 사용자가 입력한 단어가 포함되기만 하면 되므로 *word* 형태로 체크 (이미 와일드카드가 있으면 그대로 사용)
                                        pattern = f"*{f_pattern}*" if "*" not in f_pattern else f_pattern
                                        if fnmatch.fnmatch(fname_lower, pattern.lower()):
                                            is_matched = True
                                            break
                                    if not is_matched:
                                        continue

                                # os.scandir의 entry.stat()은 윈도우에서 추가 시스템 콜 없이 캐시된 정보를 반환함
                                file_list.append((entry.path, entry.stat().st_size))
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


def is_valid_excel_signature(file_path: str) -> bool:
    """
    파일 헤더를 확인하여 유효한 엑셀 파일(XLS, XLSX, ODS 등)인지 검사합니다.
    """
    try:
        with open(file_path, "rb") as f:
            header = f.read(8)
            # Legacy XLS (OLE2 Compound File)
            if header.startswith(b"\xd0\xcf\x11\xe0"):
                return True
            # ZIP-based (XLSX, XLSM, XLSB, ODS) - Starts with PK\x03\x04
            if header.startswith(b"PK\x03\x04"):
                return True
            return False
    except Exception:
        return False


def search_in_excel(file_path: str, search_string: str) -> Optional[Union[SearchResult, SkippedResult]]:
    """
    Excel 파일(.xlsx, .xlsm, .xls, .xlsb, .ods) 내의 모든 시트에서 문자열을 검색합니다.
    python-calamine(Rust 엔진 기반)을 사용하여 대용량 파일도 초고속으로 처리합니다.
    """
    # [Check] 파일 시그니처 검사 (확장자만 바꾼 텍스트 파일 등 방지)
    if not is_valid_excel_signature(file_path):
        return (Constants.STATUS_SKIPPED, AppStrings.ERROR_EXCEL_SIGNATURE)

    try:
        from python_calamine import CalamineWorkbook

        # Calamine은 별도의 read-only 모드 설정 없이 기본적으로 초고속 읽기 전용입니다.
        try:
            workbook = CalamineWorkbook.from_path(file_path)
        except (IOError, OSError) as e:
            # 파일 읽기 권한 등 물리적 이슈
            return (Constants.STATUS_SKIPPED, AppStrings.ERROR_EXCEL_ACCESS.format(e))
        except Exception as e:
            # 지원하지 않는 형식이거나 파일 구조 오류 시 SKIPPED 처리
            return (Constants.STATUS_SKIPPED, AppStrings.ERROR_EXCEL_PROCESS.format(e))

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
        return (Constants.STATUS_SKIPPED, AppStrings.ERROR_EXCEL_CALAMINE)
    except Exception as e:
        # python-calamine 라이브러리 부재 시 경고 메시지 출력
        if "calamine" in str(e).lower():
            logger.error(AppStrings.ERROR_EXCEL_CALAMINE)
            return (Constants.STATUS_SKIPPED, AppStrings.ERROR_EXCEL_CALAMINE)

        logger.debug(AppStrings.ERROR_EXCEL_EXCEPTION.format(e))
        return (Constants.STATUS_SKIPPED, AppStrings.ERROR_EXCEL_EXCEPTION.format(e))
    return None


def search_in_json_special(
    file_path: str, search_string: str, exact_match: bool = False
) -> Union[SearchResult, SkippedResult, List]:
    """
    JSON 파일을 파싱하여 값(Value)들 중에서만 검색합니다. 주석은 무시합니다.
    """
    try:
        import json

        raw_content, encoding = read_text_file_with_encoding(file_path)

        # JSON 파일은 표준적으로 주석을 지원하지 않으므로 strip_comments 건너뛰기
        # (일부 JSON 파서는 주석을 허용하지만, 주석 제거 정규식이 URL 등을 손상시킬 수 있음)
        processed_content = raw_content
        # 후행 쉼표 처리: 쉼표 바로 뒤에 닫는 괄호가 오는 경우만 처리 (개행/공백 허용)
        # 예: {"a": 1,} 또는 [1, 2,] 같은 경우만 쉼표 제거
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
            """
            JSON 구조를 재귀적으로 탐색하여 문자열을 검색합니다.

            Args:
                obj: 현재 탐색 중인 JSON 객체 (dict, list, str 등)
                path (str): 현재 객체의 키 경로
                depth (int): 현재 재귀 깊이

            Returns:
                int: 발견된 매칭 수
            """
            # 재귀 호출 전에 깊이 체크 (>= 사용으로 경계 조건 명확화)
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

                    # [Optimization check] val_raw가 매번 바뀌므로 루프 밖 컴파일은 불가.
                    # 대신 캐싱을 고려할 수 있으나 현재는 매번 컴파일 유지.
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
            logger.warning(AppStrings.LOG_SCH_JSON_RECURSION.format(file_path))
            return ("SKIPPED", AppStrings.ERROR_JSON_RECURSION)

        if search_state["limit_hit"]:
            return ("SKIPPED", AppStrings.ERROR_JSON_DEPTH_LIMIT)

        if total_count > 0:
            return (file_path, total_count, matches)
        return []  # 발견된 것 없음
    except (IOError, OSError) as e:
        logger.debug(AppStrings.LOG_SCH_ERROR_FILE.format(file_path, e))
        return (Constants.STATUS_SKIPPED, AppStrings.ERROR_FILE_PROCESSING.format(file_path, e))
    except Exception as e:
        logger.error(AppStrings.LOG_SCH_UNEXPECTED_ERROR.format("JSON", file_path, e), exc_info=True)
        return (Constants.STATUS_SKIPPED, AppStrings.ERROR_JSON_PARSE_EX.format(e))


def search_in_xml_special(file_path, search_string, exact_match=False):
    """
    XML 파일을 파싱하여 검색합니다. 주석은 무시합니다.
    """
    try:
        import xml.parsers.expat

        # 주석 제거를 위해 먼저 읽음

        # 주석 제거를 위해 먼저 읽음
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
            logger.debug(AppStrings.LOG_SCH_PARSE_ERROR.format("XML", file_path, e))
            return ("SKIPPED", AppStrings.ERROR_XML_PARSE.format(e))

        if count > 0:
            return (file_path, count, matches)
        return []
    except (IOError, OSError) as e:
        logger.debug(AppStrings.LOG_SCH_ERROR_FILE.format(file_path, e))
        return (Constants.STATUS_SKIPPED, AppStrings.ERROR_FILE_PROCESSING.format(file_path, e))
    except Exception as e:
        logger.error(AppStrings.LOG_SCH_UNEXPECTED_ERROR.format("XML", file_path, e), exc_info=True)
        return (Constants.STATUS_SKIPPED, AppStrings.ERROR_XML_PARSE_EX.format(e))


def search_in_archive_special(file_path, search_string, exact_match=False):
    """
    .archive 파일(JSON 구조)을 파싱하여 Namespace, Key, Source, Translation 정보를 검색합니다.
    """
    try:
        import bisect

        raw_content, encoding = read_text_file_with_encoding(file_path)
        data = json.loads(raw_content)

        processed_content = raw_content
        matches = []
        count = 0
        search_string = normalize_unicode(search_string).lower()

        search_state = {"last_pos": 0}
        line_offsets = [0]
        for match in re.finditer(r"\n", processed_content):
            line_offsets.append(match.end())

        def get_line_no(pos):
            return bisect.bisect_right(line_offsets, pos)

        # Archive 구조 탐색: Subnamespaces -> Namespace, Children -> Key, Source: {Text}, Translation: {Text}
        subnamespaces = data.get("Subnamespaces", [])
        for sn in subnamespaces:
            ns = sn.get("Namespace", "")
            children = sn.get("Children", [])
            for child in children:
                key = child.get("Key", "")
                source_obj = child.get("Source", {})
                trans_obj = child.get("Translation", {})

                source_text = source_obj.get("Text", "")
                trans_text = trans_obj.get("Text", "")

                s_norm = normalize_unicode(source_text)
                t_norm = normalize_unicode(trans_text)

                s_lower = s_norm.lower()
                t_lower = t_norm.lower()

                match_found = False
                if exact_match:
                    if s_lower == search_string or t_lower == search_string:
                        match_found = True
                else:
                    if search_string in s_lower or search_string in t_lower:
                        match_found = True

                if match_found:
                    count += 1
                    line_no = 1
                    try:
                        # Key를 패턴으로 사용하여 위치 추정
                        pattern_str = re.escape(f'"{key}"')
                        pattern = re.compile(pattern_str)
                        match = pattern.search(processed_content, search_state["last_pos"])
                        if match:
                            line_no = get_line_no(match.start())
                            search_state["last_pos"] = match.end()
                        else:
                            match_retry = pattern.search(processed_content)
                            if match_retry:
                                line_no = get_line_no(match_retry.start())
                                search_state["last_pos"] = match_retry.end()
                    except Exception:
                        pass

                    matches.append((line_no, ns, key, s_norm, t_norm))

        if count > 0:
            return (file_path, count, matches)
        return []

    except (IOError, OSError) as e:
        logger.debug(AppStrings.LOG_SCH_ERROR_FILE.format(file_path, e))
        return (Constants.STATUS_SKIPPED, AppStrings.ERROR_FILE_PROCESSING.format(file_path, e))
    except json.JSONDecodeError:
        return (Constants.STATUS_SKIPPED, AppStrings.ERROR_JSON_PARSE)
    except Exception as e:
        logger.error(AppStrings.LOG_SCH_UNEXPECTED_ERROR.format("Archive", file_path, e), exc_info=True)
        return (Constants.STATUS_SKIPPED, str(e))


def _quick_search_bytes(file_path: str, search_text: str) -> bool:
    """
    파일 전체를 디코딩하지 않고 바이트 패턴 매칭으로 검색어 존재 여부를 빠르게 확인합니다.
    (UTF-8, CP949, UTF-16LE 인코딩을 고려)
    True를 반환하면 파일 내에 검색어(의 바이트 표현)가 존재할 가능성이 높습니다.
    """
    patterns = []
    # 일반적인 인코딩들에 대해 검색어 바이트열 생성
    # 순서: UTF-8 (가장 흔함) -> CP949 (한글 윈도우) -> UTF-16LE (윈도우 시스템 파일) -> UTF-16BE (추가)
    encodings = ["utf-8", "cp949", "utf-16-le", "utf-16-be"]

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
        # 파일 크기가 0이면 메모리 맵(mmap) 생성이 불가능하므로 즉시 제외합니다.
        if os.path.getsize(file_path) == 0:
            return False

        with open(file_path, "rb") as f:
            # 메모리 맵(mmap)을 사용하여 실제 데이터를 메모리에 복사하지 않고 고속으로 검색합니다.
            with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                for pat in patterns:
                    if mm.find(pat) >= 0:
                        return True
    except (ValueError, OSError) as e:
        # 파일 접근 권한 문제나 기타 시스템 오류인 경우 예외를 상위로 전달하여 SKIPPED 처리합니다.
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
        try:
            return search_in_excel(file_path, search_string_nfc)
        except Exception as e:
            # [Fix] 라이브러리 부재 시 명확한 에러 메시지 반환
            error_msg = str(e)
            if "calamine" in error_msg.lower() or "import" in error_msg.lower():
                logger.error(f"Excel library error: {e}")
                raise RuntimeError("Excel 검색을 위해 python-calamine 라이브러리가 필요합니다.")
            logger.error(f"Error searching in excel {file_path}: {e}")
            return []

    # [Special Search Mode]
    # 사용자가 XML/JSON/Archive 특수 검색 모드를 선택한 경우 해당 모드로 분기합니다.
    # [Fix] 바이너리 체크보다 먼저 수행해야 함 (HTML/JSON 등은 바이너리로 오인될 수 있음)
    if special_mode:
        is_exact = Constants.MODE_EXACT in special_mode
        if Constants.MODE_XML in special_mode:
            return search_in_xml_special(file_path, search_string_nfc, is_exact)
        elif Constants.MODE_JSON in special_mode:
            return search_in_json_special(file_path, search_string_nfc, is_exact)
        elif Constants.MODE_ARCHIVE in special_mode:
            return search_in_archive_special(file_path, search_string_nfc, is_exact)

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
    except (IOError, OSError) as e:
        logger.debug(AppStrings.LOG_SCH_BINARY_CHECK_ERROR.format(file_path, e))
        return (Constants.STATUS_SKIPPED, f"파일 접근 오류 (바이너리 체크): {e}")
    except Exception as e:
        logger.debug(AppStrings.LOG_SCH_BINARY_CHECK_ERROR.format(file_path, e))
        return (Constants.STATUS_SKIPPED, AppStrings.ERROR_FILE_PROCESSING.format(file_path, e))

    # [Rust Engine Integration]
    # 특수 모드가 아니고 Rust 엔진이 사용 가능한 경우, Rust 모듈로 즉시 위임하여 극강의 성능 달성
    # (Rust Module delegates immediately for extreme performance)
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
                    return (file_path, count, [(1, AppStrings.MSG_BINARY_MATCH.format(count))])
                return (file_path, len(rust_results), rust_results)

            # Rust가 빈 결과를 반환한 경우, 인코딩 문제로 검색 누락 가능성이 있으므로
            # Python 폴백으로 재검색 (CP949 등 비 UTF-8 인코딩 대응)
            logger.debug(AppStrings.LOG_SCH_RETRY_PYTHON.format(file_path))
            # 아래 Python 로직으로 계속 진행
        except Exception as e:
            logger.error(AppStrings.LOG_SCH_RUST_ENGINE_ERROR.format(file_path, e))
            # 실패 시 아래 파이썬 로직으로 폴백 (Fallback)

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
                    return (file_path, count, [(1, AppStrings.MSG_BINARY_MATCH.format(count))])
                return (file_path, count, matches)
            return None

        # 10MB 이상 대용량 파일은 mmap 유지 (주석 처리가 까다로움 - 일단 mmap로 수행하되 주석이 많은 언어는 경고 처리 가능)
        with open(file_path, "rb") as f:
            # 파일 크기 재확인 (0바이트 파일 mmap 방지)
            file_size_actual = os.fstat(f.fileno()).st_size
            if file_size_actual == 0:
                return None  # 빈 파일은 검색 결과 없음

            # [Fix] 대용량 파일도 인코딩 감지 시도 (헤더 4KB 읽기)
            # UTF-8 무조건 디코딩으로 인한 한글 깨짐 방지
            head = f.read(4096)
            detected_enc = detect_encoding_quickly(head)
            if not detected_enc:
                detected_enc = "utf-8"

            matches = []
            # [Fix] mmap 검색 시 다중 인코딩 지원 (UTF-8, CP949 등)
            # 바이트 패턴을 각 인코딩별로 생성하여 검색 누락 방지
            search_patterns = []
            # 감지된 인코딩을 최우선으로 추가
            try:
                p = search_string_nfc.encode(detected_enc)
                if p:
                    search_patterns.append((p, detected_enc))
            except UnicodeEncodeError:
                pass

            # 보조 인코딩 (UTF-8, CP949, UTF-16LE) 추가
            for enc in [Constants.ENC_UTF8, Constants.ENC_CP949, Constants.ENC_UTF16_LE]:
                if enc == detected_enc:
                    continue
                try:
                    p = search_string_nfc.encode(enc)
                    if p and p not in [pat for pat, _ in search_patterns]:  # 중복 패턴 방지
                        search_patterns.append((p, enc))
                except UnicodeEncodeError:
                    continue

            if not search_patterns:
                return None  # 검색할 패턴이 없으면 종료

            with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                # 파일 전체에서 모든 패턴의 위치를 찾음
                all_positions = set()
                for pattern, _ in search_patterns:
                    pos = mm.find(pattern)
                    while pos != -1:
                        all_positions.add(pos)
                        pos = mm.find(pattern, pos + len(pattern))

                if not all_positions:
                    return None  # 일치하는 패턴이 없으면 None 반환

                # 정렬된 위치를 기반으로 라인 정보 추출
                sorted_positions = sorted(list(all_positions))

                # 메모리 효율을 위해 한 번만 순회하며 라인 번호 계산
                current_line = 1
                mm.seek(0)  # mmap 객체의 현재 위치를 0으로 초기화
                last_processed_pos = 0  # 마지막으로 처리된 mmap 위치

                # 미리 개행 문자 오프셋을 계산하여 bisect를 사용하는 것이 더 효율적일 수 있으나,
                # 여기서는 순차적으로 처리하는 방식으로 구현

                for pos in sorted_positions:
                    # 이전 처리 위치부터 현재 매치 위치까지의 개행 문자 수 계산
                    chunk = mm[last_processed_pos:pos]
                    current_line += chunk.count(b"\n")

                    # 해당 행의 시작과 끝 오프셋 찾기
                    # pos 이전의 마지막 개행 문자 찾기
                    line_start = mm.rfind(b"\n", 0, pos)
                    if line_start == -1:  # 파일 시작부터 개행 문자가 없는 경우
                        line_start = 0
                    else:
                        line_start += 1  # 개행 문자 다음이 라인 시작

                    # pos 이후의 첫 개행 문자 찾기
                    line_end = mm.find(b"\n", pos)
                    if line_end == -1:  # 파일 끝까지 개행 문자가 없는 경우
                        line_end = mm.size()

                    # 라인 내용 추출 및 디코딩
                    # [Fix] 감지된 인코딩 사용
                    line_content = mm[line_start:line_end].decode(detected_enc, errors="replace").strip()

                    # 중복 라인 추가 방지 (동일 라인에 여러 패턴이 매치될 수 있음)
                    if not matches or matches[-1][0] != current_line:
                        matches.append((current_line, line_content))

                    last_processed_pos = pos  # 다음 반복을 위해 현재 위치 업데이트

                count = len(matches)
                if count > 0:
                    # 바이너리 파일인 경우 텍스트 깨짐 방지를 위해 플레이스홀더 반환
                    # 바이너리 파일인 경우 텍스트 깨짐 방지를 위해 플레이스홀더 반환
                    if is_binary:
                        return (file_path, count, [(1, AppStrings.MSG_BINARY_MATCH.format(count))])
                    return (file_path, count, matches)
    except (IOError, OSError) as e:
        logger.debug(AppStrings.LOG_SCH_ERROR_FILE.format(file_path, e))
        return (Constants.STATUS_SKIPPED, AppStrings.ERROR_IO_DURING_SEARCH.format(e))
    except Exception as e:
        logger.error(AppStrings.LOG_SCH_UNEXPECTED_ERROR.format("파일", file_path, e), exc_info=True)
        return (Constants.STATUS_SKIPPED, f"예기치 못한 오류: {e}")
    return None


def search_in_files_batch(
    file_batch: List[FileInfo],
    search_string: str,
    special_mode: Optional[str] = None,

) -> Dict[str, List]:
    """
    배치 단위의 파일 검색을 수행하고 결과와 스킵된 파일 목록을 반환합니다.

    Args:
        file_batch (List[FileInfo]): 검색할 파일 정보(경로, 크기) 리스트
        search_string (str): 검색할 문자열
        special_mode (str, optional): 특수 검색 모드 (XML, JSON, Archive 등)
        compiled_patterns (dict, optional): 인코딩별(utf-8, cp949 등) 미리 컴파일된 정규식 패턴 사전

    Returns:
        Dict[str, List]: "results" 키에는 검색 결과 리스트, "skipped" 키에는 스킵된 파일 목록 포함
    """
    results = []
    skipped = []
    for f_path, f_size in file_batch:
        res = search_in_file(f_path, search_string, f_size, special_mode)
        if isinstance(res, tuple) and res[0] == "SKIPPED":
            skipped.append((f_path, res[1]))
        elif res == "SKIPPED":
            skipped.append((f_path, AppStrings.ERROR_UNKNOWN))
        elif res:
            results.append(res)
    return {"results": results, "skipped": skipped}


def search_directory_fast(
    search_paths: List[str], search_string: str, extensions: Optional[List[str]] = None
) -> Dict[str, List]:
    """
    [Rust Engine Phase 2]
    Rust로 구현된 고성능 병렬 디렉토리 스캔(sf_engine.search_dir)을 호출하여
    검색어와 일치하는 내용을 포함한 파일을 초고속으로 검색합니다.

    이 함수는 Python의 GIL(Global Interpreter Lock) 외부에서 병렬 스레드로 동작하므로
    대규모 데이터셋에서도 UI 프리징 없이 매우 빠른 성능을 보장합니다.

    Args:
        search_paths (List[str]): 검색을 시작할 최상위 루트 디렉토리 경로들의 리스트
        search_string (str): 검색할 대상 문자열 (대소문자 무시 검색 기본 적용)
        extensions (List[str], optional): 검색할 파일 확장자 리스트 (예: ['txt', 'py']). None일 경우 모든 파일 검색.

    Returns:
        Dict[str, List]:
            - "results": [(파일경로, 매칭수, [(라인번호, 내용)...]), ...] 형태의 검색 결과 리스트
            - "skipped": 스킵된 파일 목록 (Rust 엔진 특성상 현재는 빈 리스트 반환)
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
            logger.warning(AppStrings.LOG_SYS_SF_ENGINE_NOT_FOUND.format("sf_engine.search_dir"))
            return {"results": [], "skipped": []}

        # sf_engine.search_dir 호출
        # [Fix] Rust 엔진 업데이트 대비: 튜플(results, skipped) 또는 딕셔너리 반환 지원
        raw_ret = search_dir_func(search_paths, rust_pattern, rust_exts)

        raw_results = []
        raw_skipped = []

        if isinstance(raw_ret, dict):
            raw_results = raw_ret.get("results", [])
            raw_skipped = raw_ret.get("skipped", [])
        elif isinstance(raw_ret, tuple) and len(raw_ret) == 2:
            # (results, skipped)
            raw_results = raw_ret[0]
            raw_skipped = raw_ret[1]
        elif isinstance(raw_ret, list):
            # Old version: returns list of matches only
            raw_results = raw_ret

        # 결과 포맷팅 (기존 로직과 호환성 유지)
        formatted_results = []

        # Rust 결과는 플랫한 리스트이므로, 파일별로 그룹핑이 필요함
        # sf_engine.search_dir은 (path, line, content)의 리스트를 반환함 (순서는 보장 안됨)
        if raw_results:
            # import moved to top-level or inside function
            from itertools import groupby

            # path로 정렬 (그룹화를 위해 필수)
            raw_results.sort(key=lambda x: x[0])

            for path, group in groupby(raw_results, key=lambda x: x[0]):
                # 그룹의 모든 (line, content) 추출
                matches = [(line, content) for _, line, content in group]

                # [Fix] Rust 결과에서도 바이너리 파일 감지 및 보호 로직 적용
                if is_binary_file(path):
                    cnt = len(matches)
                    formatted_results.append((path, cnt, [(1, AppStrings.MSG_BINARY_MATCH.format(cnt))]))
                    continue

                # 라인 번호 순 정렬
                matches.sort(key=lambda x: x[0])
                formatted_results.append((path, len(matches), matches))

        return {"results": formatted_results, "skipped": raw_skipped}

    except (IOError, OSError, RuntimeError) as e:
        logger.error(AppStrings.LOG_SCH_RUST_DIR_SEARCH_ERROR.format(e))
        raise e
    except Exception as e:
        logger.critical(f"Rust 디렉토리 검색 중 치명적 오류: {e}", exc_info=True)
        raise e


def find_files_with_keyword_fast(
    search_paths: List[str], search_string: str, extensions: Optional[List[str]] = None
) -> List[FileInfo]:
    """
    [Rust Engine Phase 3: Smart Scan]
    Rust 엔진을 사용하여 지정된 키워드가 포함된 파일들의 목록만을 빠르게 추출합니다.

    이 함수는 상세한 매칭 위치(라인 번호 등)를 찾지 않고,
    단순히 '파일 내에 검색어가 존재하는지' 여부만 판단하여 파일 목록을 반환합니다.
    검색 전 파일 목록을 미리 필터링하는 스마트 스캔(Smart Scan) 단계에서 사용됩니다.

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
        # Rust 엔진은 Regex 기반이므로 escape 처리 및 유니코드 정규화
        rust_pattern = re.escape(normalize_unicode(search_string))

        rust_exts = None
        if extensions:
            rust_exts = [ext.lstrip(".").lower() for ext in extensions]

        # [Defensive] sf_engine.find_files_with_keyword 속성 확인
        find_func = getattr(sf_engine, "find_files_with_keyword", None)
        if not find_func:
            logger.warning(AppStrings.LOG_SYS_SF_ENGINE_NOT_FOUND.format("sf_engine.find_files_with_keyword"))
            return []

        # sf_engine.find_files_with_keyword(paths, pattern, extensions) -> [(path, size), ...]
        found_files = find_func(search_paths, rust_pattern, rust_exts)
        return found_files
    except (IOError, OSError, RuntimeError) as e:
        logger.error(AppStrings.LOG_SCH_RUST_SMART_SCAN_ERROR.format(e))
        return []
    except Exception as e:
        logger.critical(f"Rust Smart Scan 중 예기치 못한 치명적 오류: {e}", exc_info=True)
        return []
