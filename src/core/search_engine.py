import os
import mmap
import multiprocessing
import threading
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
import chardet
from utils.logger import logger
from utils.app_strings import AppStrings
import fnmatch
import unicodedata
import re
import bisect


def normalize_unicode(text):
    """유니코드 자소 분리 방지를 위한 NFC 정규화"""
    if isinstance(text, str):
        return unicodedata.normalize("NFC", text)
    return text


class FileScanner:
    """
    지정된 폴더들을 순회하며 확장자 및 파일명 필터 조건에 맞는 파일 목록을 수집하는 클래스입니다.
    성능 최적화 버전:
    1. os.path.abspath 오버헤드 최소화
    2. 불필요한 유니코드 정규화 감소
    3. 스레드 풀 오버헤드를 줄이기 위해 최상위 폴더 및 1차 하위 폴더까지만 병렬화
    4. 리스트 대신 로컬 리스트 취합 후 병합 (Lock 경합 감소)
    """

    def __init__(self, folders, extensions, filename_filter=""):
        # 초기화 단계에서 미리 최적화된 필터 정보 준비
        self.folders = [os.path.abspath(f) for f in folders if os.path.exists(f)]
        self.extensions = tuple(f".{ext.lower().strip('.')}" for ext in extensions)
        self.filename_filter = normalize_unicode(filename_filter.lower())
        self.is_glob = "*" in self.filename_filter or "?" in self.filename_filter

        self.found_files = []
        self._lock = threading.Lock()

    def scan(self):
        """
        설정된 폴더 및 필터 조건으로 전체 스캔을 수행하고 파일 경로 리스트를 반환합니다.
        """
        # 1. 상위/하위 폴더 관계 정리 (중복 스캔 방지)
        # 문자열 비교를 통해 relpath 시스템 콜 호출 최소화
        sorted_folders = sorted(self.folders, key=len)
        unique_folders = []
        for i, folder in enumerate(sorted_folders):
            is_sub = False
            for parent in unique_folders:
                # parent가 folder의 상위 경로인지 문자열로 우선 확인
                if folder.startswith(parent) and (len(folder) == len(parent) or folder[len(parent)] in [os.sep, "/"]):
                    is_sub = True
                    break
            if not is_sub:
                unique_folders.append(folder)

        if not unique_folders:
            return []

        logger.info(AppStrings.LOG_SCANNING_FOLDERS.format(unique_folders, self.extensions, self.filename_filter))

        # 2. 하이브리드 병렬 스캔
        # 최상위 폴더들과 그 직계 하위 폴더들만 스레드 풀에 등록하여 오버헤드 억제
        max_workers = min(multiprocessing.cpu_count() * 4, 32)
        tasks = []

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for root in unique_folders:
                try:
                    # 각 루트 폴더의 1단계 하위까지만 스레드로 분산
                    with os.scandir(root) as it:
                        sub_folders = []
                        for entry in it:
                            if entry.is_dir():
                                sub_folders.append(entry.path)
                            else:
                                # 루트에 바로 있는 파일은 직접 체크 (스케줄링 오버헤드 방지)
                                if self._check_filter(entry):
                                    self.found_files.append(entry.path)

                        if sub_folders:
                            for sf in sub_folders:
                                tasks.append(executor.submit(self._scan_recursive_sequential, sf))
                        else:
                            # 하위 폴더가 없으면 이미 파일 체크 끝
                            pass
                except (PermissionError, OSError):
                    continue

            # 모든 작업 완료 대기
            for future in tasks:
                try:
                    result = future.result()
                    if result:
                        with self._lock:
                            self.found_files.extend(result)
                except Exception as e:
                    logger.debug(f"Scan task failed: {e}")

        # 3. 결과 반환 (이미 abspath로 저장됨)
        # set 호출을 최소화하기 위해 단순히 반환 (unique_folders에서 이미 중복 방지됨)
        return list(set(self.found_files))

    def _check_filter(self, entry):
        """파일이 필터 조건에 부합하는지 확인합니다."""
        # 확장자 체크
        if self.extensions:
            name_lower = entry.name.lower()
            if not name_lower.endswith(self.extensions):
                return False
        else:
            name_lower = entry.name.lower()

        # 파일명 필터 체크
        if not self.filename_filter:
            return True

        name_nfc = normalize_unicode(name_lower)
        if self.is_glob:
            return fnmatch.fnmatch(name_nfc, self.filename_filter)
        else:
            return self.filename_filter in name_nfc

    def _scan_recursive_sequential(self, folder):
        """하위 폴더 트리를 순차적으로 재귀 스캔합니다. (스레드 내부에서 실행)"""
        local_matches = []
        stack = [folder]

        while stack:
            curr = stack.pop()
            try:
                with os.scandir(curr) as it:
                    for entry in it:
                        try:
                            if entry.is_file():
                                if self._check_filter(entry):
                                    local_matches.append(entry.path)
                            elif entry.is_dir():
                                stack.append(entry.path)
                        except (PermissionError, OSError):
                            continue
            except (PermissionError, OSError):
                continue
        return local_matches


def search_in_excel_streaming(file_path, search_string, case_sensitive=True):
    """
    openpyxl의 read_only=True 모드를 사용하여 대용량 엑셀 파일을 한 줄씩 스트리밍 검색합니다.
    이 방식은 속도는 Calamine보다 느리지만, 메모리 사용량을 매우 낮게 유지할 수 있습니다.
    """
    try:
        from openpyxl import load_workbook
        from openpyxl.utils import get_column_letter

        search_string = normalize_unicode(search_string)
        if not case_sensitive:
            search_string = search_string.lower()

        # read_only=True: 파일 전체를 메모리에 올리지 않고 필요할 때만 읽음
        # data_only=True: 수식 결과값만 읽어옴
        wb = load_workbook(file_path, read_only=True, data_only=True)
        matches = []
        count = 0

        for sheet_name in wb.sheetnames:
            sheet = wb[sheet_name]
            # iter_rows()를 사용하여 행 단위로 순회
            for row_idx, row in enumerate(sheet.iter_rows(values_only=True), 1):
                if not row:
                    continue
                for col_idx, value in enumerate(row, 1):
                    if value is not None:
                        val_str = str(value)
                        if not case_sensitive:
                            val_str = val_str.lower()

                        if search_string in val_str:
                            count += 1
                            cell_addr = f"{get_column_letter(col_idx)}{row_idx}"
                            matches.append((f"{sheet_name}!{cell_addr}", str(value)))

        wb.close()

        if count > 0:
            return (file_path, count, matches)
    except Exception as e:
        logger.debug(AppStrings.ERROR_EXCEL_SEARCH.format(file_path, AppStrings.ERROR_STREAMING.format(e)))
    return None


def search_in_excel(file_path, search_string, case_sensitive=True):
    """
    파일 크기에 따라 최적의 엔진을 선택하여 Excel 파일을 검색합니다.
    """
    try:
        # 파일 크기 임계값 (200MB)
        file_size = os.path.getsize(file_path)
        LARGE_FILE_THRESHOLD = 200 * 1024 * 1024  # 200MB

        # 200MB를 초과하는 대용량인 경우 메모리 안전을 위해 스트리밍 모드(openpyxl) 사용
        if file_size > LARGE_FILE_THRESHOLD:
            logger.info(AppStrings.LOG_LARGE_EXCEL_DETECTED.format(file_size / 1024 / 1024))
            return search_in_excel_streaming(file_path, search_string, case_sensitive)

        # 일반적인 크기인 경우 초고속 Calamine 엔진 사용
        from python_calamine import CalamineWorkbook
        from openpyxl.utils import get_column_letter

        search_string = normalize_unicode(search_string)
        if not case_sensitive:
            search_string = search_string.lower()

        workbook = CalamineWorkbook.from_path(file_path)
        matches = []
        count = 0

        for sheet_name in workbook.sheet_names:
            sheet = workbook.get_sheet_by_name(sheet_name)
            for row_idx, row in enumerate(sheet.to_python(), 1):
                for col_idx, value in enumerate(row, 1):
                    if value is not None:
                        val_str = str(value)
                        if not case_sensitive:
                            val_str = val_str.lower()

                        if search_string in val_str:
                            count += 1
                            cell_addr = f"{get_column_letter(col_idx)}{row_idx}"
                            matches.append((f"{sheet_name}!{cell_addr}", str(value)))

        if count > 0:
            return (file_path, count, matches)
    except (IOError, OSError) as e:
        logger.debug(AppStrings.ERROR_EXCEL_SEARCH.format(file_path, str(e)))
    except Exception as e:
        logger.error(f"Unexpected error in Excel search ({file_path}): {e}", exc_info=True)
    return None


def detect_encoding_quickly(content_bytes):
    """
    chardet을 사용하기 전, 가장 빈번하게 발생하는 인코딩들을 휴리스틱하게 탐지하여 성능을 높입니다.
    BOM(Byte Order Mark)을 우선적으로 확인하고, 그 외에는 UTF-8, CP949 순으로 시도합니다.

    Args:
        content_bytes (bytes): 탐지할 파일의 샘플 바이트 데이터

    Returns:
        str: 탐지된 인코딩 이름 (기본값 "utf-8")
    """
    # 1. BOM(Byte Order Mark) 확인
    if content_bytes.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if content_bytes.startswith(b"\xff\xfe"):
        return "utf-16-le"
    if content_bytes.startswith(b"\xfe\xff"):
        return "utf-16-be"

    # 2. 현대적인 UTF-8 우선 시도 (파일 크기가 클 수 있으므로 앞부분 10KB만 샘플링)
    try:
        content_bytes[:10000].decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass

    # 3. 한국어 윈도우 환경에서 흔히 쓰이는 CP949 (EUC-KR 확장) 시도
    try:
        content_bytes[:10000].decode("cp949")
        return "cp949"
    except UnicodeDecodeError:
        pass

    # 4. 위 방식들로 실패 시, chardet 라이브러리를 통해 통계적 인코딩 분석 수행
    detected = chardet.detect(content_bytes[:10000])
    return detected["encoding"] or "utf-8"


def search_in_file(file_path, search_string, case_sensitive=True):
    """
    단일 텍스트 또는 엑셀 파일 내에서 지정된 문자열을 검색합니다.
    성능 최적화:
    1. mmap을 사용하여 파일을 메모리에 직접 매핑
    2. 바이트 레벨에서 정규표현식(re.finditer)으로 검색어 위치(offset) 탐지
    3. 발견된 위치 주변만 선택적으로 디코딩하여 라인 내용 추출 (Lazy Decoding)
    """
    search_string_nfc = normalize_unicode(search_string)
    ext = os.path.splitext(file_path)[1].lower()

    if ext in [".xlsx", ".xlsm", ".xls", ".xlsb"]:
        return search_in_excel(file_path, search_string_nfc, case_sensitive)

    matches = []
    count = 0
    try:
        if not os.path.exists(file_path):
            return None

        file_size = os.path.getsize(file_path)
        if file_size == 0 or file_size > 1024 * 1024 * 2000:  # 2GB 제한
            return None

        with open(file_path, "rb") as f:
            with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                # 1. 인코딩 감지
                sample = mm.read(min(file_size, 10000))
                mm.seek(0)
                encoding = detect_encoding_quickly(sample)

                # 2. 검색어 바이트 패턴 준비
                # UTF-16 계열은 바이트 검색이 복잡하므로 기존 방식(전체 디코딩) 사용 혹은 특수 처리
                if "utf-16" in encoding.lower():
                    content = mm.read().decode(encoding, errors="ignore")
                    lines = content.splitlines()
                    for i, line in enumerate(lines):
                        line_to_check = line if case_sensitive else line.lower()
                        if (search_string_nfc.lower() if not case_sensitive else search_string_nfc) in line_to_check:
                            count += 1
                            clean_line = line.replace("\r", "").replace("\n", "")
                            if len(clean_line) > 1000:
                                clean_line = clean_line[:1000] + "..."
                            matches.append((i + 1, clean_line))
                    return (file_path, count, matches) if count > 0 else None

                # UTF-8, CP949 등 멀티바이트/싱글바이트 인코딩 최적화 검색
                # BOM이 포함된 인코딩 이름(예: utf-8-sig)에서 BOM을 제거한 순수 인코딩 이름 사용
                clean_encoding = encoding
                if encoding.lower() == "utf-8-sig":
                    clean_encoding = "utf-8"

                try:
                    search_pattern_bytes = search_string_nfc.encode(clean_encoding)
                except UnicodeEncodeError:
                    # 해당 인코딩으로 표현 불가능한 검색어인 경우
                    return None

                flags = 0
                if not case_sensitive:
                    flags |= re.IGNORECASE

                # 바이트 패턴으로 정규식 검색 (mmap 상에서 직접 실행)
                # 특수 문자 에스케이프 처리
                pattern = re.escape(search_pattern_bytes)

                # 3. 모든 줄바꿈 위치 파악 (라인 번호 계산용)
                # 전체를 한 번 스캔하여 \n 위치 저장
                newline_offsets = [m.start() for m in re.finditer(b"\n", mm)]

                # 4. 결과 탐색 및 라인 추출
                mm.seek(0)
                for match in re.finditer(pattern, mm, flags):
                    count += 1
                    start_pos = match.start()

                    # 라인 번호 계산 (이진 탐색)
                    line_no = bisect.bisect_right(newline_offsets, start_pos) + 1

                    # 라인 시작과 끝 찾기
                    # 현재 위치 이전의 가장 가까운 \n 찾기
                    line_start = 0
                    if line_no > 1:
                        line_start = newline_offsets[line_no - 2] + 1

                    # 현재 위치 이후의 가장 가까운 \n 찾기
                    line_end = file_size
                    if line_no <= len(newline_offsets):
                        line_end = newline_offsets[line_no - 1]

                    # 해당 라인만 추출하여 디코딩 (Lazy Decoding)
                    line_bytes = mm[line_start:line_end]
                    line_content = line_bytes.decode(encoding, errors="ignore").replace("\r", "")

                    if len(line_content) > 1000:
                        line_content = line_content[:1000] + "..."

                    matches.append((line_no, line_content))

        if count > 0:
            return (file_path, count, matches)
    except Exception as e:
        logger.debug(f"Search error in {file_path}: {e}")
    return None


class SearchEngine:
    def __init__(self):
        self.num_cores = multiprocessing.cpu_count()

    def search_parallel(self, file_list, search_string, case_sensitive=True, progress_callback=None):
        """파일 리스트에 대해 병렬 검색을 수행합니다."""
        results = []
        total = len(file_list)
        logger.info(AppStrings.LOG_WORKER_STARTED.format(search_string))

        # 대용량 파일 기준 (100MB 이상)
        LARGE_FILE_SIZE = 100 * 1024 * 1024
        large_files_count = sum(1 for f in file_list if os.path.getsize(f) > LARGE_FILE_SIZE)

        # 대용량 파일이 있으면 메모리 보호를 위해 병렬 워커 수를 줄임
        actual_workers = self.num_cores
        if large_files_count > 0:
            # 대용량 파일 검색 시에는 코어 수의 절반 또는 최대 4개로 제한하여 RAM 폭주 방지
            actual_workers = max(1, min(self.num_cores // 2, 4))
            logger.info(AppStrings.LOG_ADAPTIVE_WORKERS.format(large_files_count, actual_workers))

        with ProcessPoolExecutor(max_workers=actual_workers) as executor:
            # 파일을 배치로 나누기보다 하나씩 처리하여 실시간성 확보
            future_to_file = {executor.submit(search_in_file, f, search_string, case_sensitive): f for f in file_list}

            completed = 0
            for future in future_to_file:
                res = future.result()
                if res:
                    results.append(res)

                completed += 1
                if progress_callback:
                    progress_callback(completed, total)

        return results
