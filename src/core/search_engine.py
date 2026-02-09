import os
from os.path import getsize, exists, abspath, splitext
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
    """
    유니코드 자소 분리 현상(Mac 등)을 방지하기 위해 텍스트를 NFC 방식으로 정규화합니다.
    
    Args:
        text (str): 정규화할 문자열
    Returns:
        str: 정규화된 문자열
    """
    if isinstance(text, str):
        return unicodedata.normalize("NFC", text)
    return text


class FileScanner:
    """
    지정된 폴더를 탐색하며 확장자 및 파일명 필터에 맞는 파일 목록을 수집하는 클래스입니다.
    
    성능 최적화 포인트:
    1. os.path.abspath 호출 최소화
    2. 중복되는 하위 폴더 스캔 방지 로직 포함
    3. 최상위 및 1차 하위 폴더까지만 병렬화하여 스레드 오버헤드 억제
    4. 로컬 리스트 취합 후 병합을 통해 Lock 경합 감소
    """

    def __init__(self, folders, extensions, filename_filter=""):
        """
        FileScanner를 초기화합니다.
        
        Args:
            folders (list): 탐색할 기본 폴더 리스트
            extensions (list): 검색 대상 확장자 리스트
            filename_filter (str): 파일명 필터 (콤마로 다중 필터 지원, Glob 패턴 가능)
        """
        # 초기화 단계에서 미리 최적화된 필터 정보 준비
        self.folders = [abspath(f) for f in folders if exists(f)]
        self.extensions = tuple(f".{ext.lower().strip('.')}" for ext in extensions)
        
        # 다중 필터 처리: 콤마로 구분하여 리스트업
        raw_filters = [f.strip() for f in filename_filter.split(",") if f.strip()]
        self.filename_filters = []
        for f in raw_filters:
            normalized_f = normalize_unicode(f.lower())
            is_glob = "*" in normalized_f or "?" in normalized_f
            self.filename_filters.append((normalized_f, is_glob))

        self.found_files = []
        self._lock = threading.Lock()

    def scan(self):
        """
        설정된 폴더 및 필터 조건으로 시스템의 파일들을 스캔하고 경로 리스트를 반환합니다.
        
        Returns:
            list: 필터 조건에 부합하는 전체 파일 경로 리스트
        """
        # 1. 상위/하위 폴더 관계를 정리하여 불필요한 중복 스캔을 방지합니다.
        sorted_folders = sorted(self.folders, key=len)
        unique_folders = []
        for i, folder in enumerate(sorted_folders):
            is_sub = False
            for parent in unique_folders:
                # parent가 folder의 상위 경로인지 문자열 레벨에서 우선 확인합니다.
                if folder.startswith(parent) and (len(folder) == len(parent) or folder[len(parent)] in [os.sep, "/"]):
                    is_sub = True
                    break
            if not is_sub:
                unique_folders.append(folder)

        if not unique_folders:
            return []

        logger.info(AppStrings.LOG_SCANNING_FOLDERS.format(unique_folders, self.extensions, self.filename_filters))

        # 2. 하이브리드 병렬 스캔을 실행합니다.
        # 최상위 폴더들과 그 직계 하위 폴더들만 개별 작업으로 분산하여 스레드 관리 오버헤드를 제어합니다.
        max_workers = min(multiprocessing.cpu_count() * 4, 32)
        tasks = []

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for root in unique_folders:
                try:
                    # 각 루트 폴더의 1단계 하위까지만 스레드 풀에 등록합니다.
                    with os.scandir(root) as it:
                        sub_folders = []
                        for entry in it:
                            if entry.is_dir():
                                sub_folders.append(entry.path)
                            else:
                                # 루트 직계 파일은 오버헤드 방지를 위해 현재 스레드에서 즉시 체크합니다.
                                if self._check_filter(entry):
                                    self.found_files.append((entry.path, entry.stat().st_size))

                        if sub_folders:
                            for sf in sub_folders:
                                tasks.append(executor.submit(self._scan_recursive_sequential, sf))
                except (PermissionError, OSError):
                    continue

            # 모든 비동기 작업의 결과를 수집합니다.
            for future in tasks:
                try:
                    result = future.result()
                    if result:
                        with self._lock:
                            self.found_files.extend(result)
                except Exception as e:
                    logger.debug(AppStrings.LOG_SCAN_TASK_FAILED.format(e))

        # 3. 중복을 제거하고 최종 경로 리스트를 반환합니다.
        return list(set(self.found_files))

    def _check_filter(self, entry):
        """
        지정된 파일 항목이 확장자 및 파일명 필터 조건에 맞는지 검사합니다.
        
        Args:
            entry (os.DirEntry): 검사할 파일 항목 객체
        Returns:
            bool: 필터 조건 충족 여부
        """
        # 확장자 일치 여부를 대소문자 구분 없이 체크합니다.
        if self.extensions:
            name_lower = entry.name.lower()
            if not name_lower.endswith(self.extensions):
                return False
        else:
            name_lower = entry.name.lower()

        # 파일명 필터가 설정되지 않은 경우 확장자 체크만으로 통과입니다.
        if not self.filename_filters:
            return True

        # 파일명 검색 시 유니코드 정규화를 적용하여 정확도를 높입니다.
        name_nfc = normalize_unicode(name_lower)
        for filter_str, is_glob in self.filename_filters:
            if is_glob:
                if fnmatch.fnmatch(name_nfc, filter_str):
                    return True
            else:
                if filter_str in name_nfc:
                    return True
        return False

    def _scan_recursive_sequential(self, folder):
        """
        지정된 폴더 이하의 트리 구조를 순차적으로 재귀 탐색합니다. (워커 스레드 내부용)
        
        Args:
            folder (str): 스캔을 시작할 폴더 경로
        Returns:
            list: 해당 폴더 트리 내에서 발견된 파일 목록
        """
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
                                    local_matches.append((entry.path, entry.stat().st_size))
                            elif entry.is_dir():
                                stack.append(entry.path)
                        except (PermissionError, OSError):
                            continue
            except (PermissionError, OSError):
                continue
        return local_matches


def search_in_excel_streaming(file_path, search_string):
    """
    openpyxl의 read_only 모드를 사용하여 대용량 Excel 파일을 메모리 효율적으로 스트리밍 검색합니다. (항상 대소문자 무시)
    """
    try:
        from openpyxl import load_workbook
        from openpyxl.utils import get_column_letter

        search_string = normalize_unicode(search_string).lower()

        # read_only=True: 전체를 메모리에 올리지 않고 행 단위로 호출하여 안정성을 보장합니다.
        wb = load_workbook(file_path, read_only=True, data_only=True)
        matches = []
        count = 0

        for sheet_name in wb.sheetnames:
            sheet = wb[sheet_name]
            for row_idx, row in enumerate(sheet.iter_rows(values_only=True), 1):
                if not row:
                    continue
                for col_idx, value in enumerate(row, 1):
                    if value is not None:
                        val_str = str(value).lower()

                        if search_string in val_str:
                            count += 1
                            cell_addr = f"{get_column_letter(col_idx)}{row_idx}"
                            # 상세 정보: (Sheet명!Cell주소, 실제 값)
                            matches.append((f"{sheet_name}!{cell_addr}", str(value)))

        wb.close()

        if count > 0:
            return (file_path, count, matches)
    except Exception as e:
        logger.debug(AppStrings.ERROR_EXCEL_SEARCH.format(file_path, AppStrings.ERROR_STREAMING.format(e)))
    return None


def search_in_excel(file_path, search_string):
    """
    Excel 파일 크기에 따라 Calamine 또는 openpyxl을 선택하여 검색합니다. (항상 대소문자 무시)
    """
    try:
        # 파일 크기 임계값 (200MB 이상은 스트리밍 모드로 처리)
        file_size = getsize(file_path)
        LARGE_FILE_THRESHOLD = 200 * 1024 * 1024

        if file_size > LARGE_FILE_THRESHOLD:
            logger.info(AppStrings.LOG_LARGE_EXCEL_DETECTED.format(file_size / 1024 / 1024))
            return search_in_excel_streaming(file_path, search_string)

        # 200MB 미만 파일은 Calamine을 사용합니다.
        from python_calamine import CalamineWorkbook
        from openpyxl.utils import get_column_letter

        search_string = normalize_unicode(search_string).lower()

        workbook = CalamineWorkbook.from_path(file_path)
        matches = []
        count = 0

        for sheet_name in workbook.sheet_names:
            sheet = workbook.get_sheet_by_name(sheet_name)
            for row_idx, row in enumerate(sheet.to_python(), 1):
                for col_idx, value in enumerate(row, 1):
                    if value is not None:
                        val_str = str(value).lower()

                        if search_string in val_str:
                            count += 1
                            cell_addr = f"{get_column_letter(col_idx)}{row_idx}"
                            matches.append((f"{sheet_name}!{cell_addr}", str(value)))

        if count > 0:
            return (file_path, count, matches)
    except (IOError, OSError) as e:
        logger.debug(AppStrings.ERROR_EXCEL_SEARCH.format(file_path, str(e)))
    except Exception as e:
        logger.error(AppStrings.ERROR_EXCEL_SEARCH_UNEXPECTED.format(file_path, e), exc_info=True)
    return None


def detect_encoding_quickly(content_bytes):
    """
    BOM 및 빈번히 발생하는 인코딩(UTF-8, CP949)을 우선적으로 탐지하여 성능을 최적화한 인코딩 판별 함수입니다.
    
    Args:
        content_bytes (bytes): 분석할 샘플 바이트 데이터
    Returns:
        str: 탐지된 인코딩 이름 (Python에서 사용 가능한 형태)
    """
    # 1. BOM(Byte Order Mark)을 확인하여 정확한 인코딩을 즉시 반환합니다.
    if content_bytes.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if content_bytes.startswith(b"\xff\xfe"):
        return "utf-16-le"
    if content_bytes.startswith(b"\xfe\xff"):
        return "utf-16-be"

    # 2. UTF-8 인코딩 시도 (앞부분 10KB 샘플링)
    try:
        content_bytes[:10000].decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass

    # 3. 한국어 윈도우 환경 표준인 CP949 시도
    try:
        content_bytes[:10000].decode("cp949")
        return "cp949"
    except UnicodeDecodeError:
        pass

    # 4. 휴리스틱 방식 실패 시 chardet 라이브러리를 통해 통계적 분석을 수행합니다.
    detected = chardet.detect(content_bytes[:10000])
    return detected["encoding"] or "utf-8"


def search_in_file(file_path, search_string, file_size=None):
    """
    mmap과 바이트 레벨 정규표현식을 사용하여 텍스트 파일 내에서 문자열을 고속 검색합니다. (항상 대소문자 무시)
    """
    search_string_nfc = normalize_unicode(search_string)
    ext = splitext(file_path)[1].lower()

    # Excel 관련 확장자인 경우 전용 함수로 분기합니다.
    if ext in [".xlsx", ".xlsm", ".xls", ".xlsb"]:
        return search_in_excel(file_path, search_string_nfc)

    matches = []
    count = 0
    try:
        if not exists(file_path):
            return None

        if file_size is None:
            file_size = getsize(file_path)
        # 2GB 이상의 파일은 시스템 리소스 안전을 위해 현재 버전에서 스킵합니다.
        if file_size == 0 or file_size > 1024 * 1024 * 2000:
            return None

        with open(file_path, "rb") as f:
            with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                # 1. 샘플 데이터를 통한 고속 인코딩 탐지
                sample = mm.read(min(file_size, 10000))
                mm.seek(0)
                encoding = detect_encoding_quickly(sample)

                # 2. UTF-16 계열은 바이트 검색이 난해하므로 가시성을 위해 전체 디코딩 방식으로 처리합니다.
                if "utf-16" in encoding.lower():
                    content = mm.read().decode(encoding, errors="ignore")
                    lines = content.splitlines()
                    for i, line in enumerate(lines):
                        line_to_check = line.lower()
                        pattern_to_check = search_string_nfc.lower()
                        if pattern_to_check in line_to_check:
                            count += 1
                            clean_line = line.replace("\r", "").replace("\n", "")
                            # 가독성을 위해 긴 라인은 1000자로 제한합니다.
                            if len(clean_line) > 1000:
                                clean_line = clean_line[:1000] + "..."
                            matches.append((i + 1, clean_line))
                    return (file_path, count, matches) if count > 0 else None

                # 3. UTF-8, CP949 등 일반 인코딩에 대한 바이트 레벨 최적화 검색을 수행합니다.
                clean_encoding = "utf-8" if encoding.lower() == "utf-8-sig" else encoding
                try:
                    search_pattern_bytes = search_string_nfc.encode(clean_encoding)
                except UnicodeEncodeError:
                    # 해당 인코딩으로 변환 불가능한 검색어인 경우 검색 대상에서 제외됩니다.
                    return None

                flags = re.IGNORECASE
                pattern = re.escape(search_pattern_bytes)

                # 파일 전체의 개행 위치를 한 번에 파악하여 라인 번호 계산 시 활용합니다.
                newline_offsets = [m.start() for m in re.finditer(b"\n", mm)]

                # mmap 객체 위에서 정규표현식으로 검색어를 찾습니다.
                mm.seek(0)
                for match in re.finditer(pattern, mm, flags):
                    count += 1
                    start_pos = match.start()

                    # 이진 탐색을 통해 개행 위치 배열에서 현재 일치 위치의 라인 번호를 도출합니다.
                    line_no = bisect.bisect_right(newline_offsets, start_pos) + 1

                    # 일치 위치 주변 데이터를 추출하여 한 줄의 텍스트로 만듭니다.
                    line_start = 0 if line_no == 1 else newline_offsets[line_no - 2] + 1
                    line_end = newline_offsets[line_no - 1] if line_no <= len(newline_offsets) else file_size

                    # 바이트 조각만 디코딩하여 메모리 부하를 줄입니다.
                    line_bytes = mm[line_start:line_end]
                    line_content = line_bytes.decode(encoding, errors="ignore").replace("\r", "")

                    if len(line_content) > 1000:
                        line_content = line_content[:1000] + "..."

                    matches.append((line_no, line_content))

        if count > 0:
            return (file_path, count, matches)
    except Exception:
        return None


def search_in_files_batch(file_batch, search_string):
    """
    파일 리스트(배치)를 받아 순차적으로 검색하고 결과를 취합하여 반환합니다. (항상 대소문자 무시)
    """
    batch_results = []
    for file_path, file_size in file_batch:
        res = search_in_file(file_path, search_string, file_size=file_size)
        if res:
            batch_results.append(res)
    return batch_results


class SearchEngine:
    """
    여러 파일에 대한 병렬 검색 프로세스를 관리하는 엔진 클래스입니다.
    """
    def __init__(self):
        """SearchEngine을 초기화합니다. 가용 CPU 코어 수를 확인합니다."""
        self.num_cores = multiprocessing.cpu_count()

    def search_parallel(self, file_with_sizes, search_string, progress_callback=None):
        """
        파일 목록과 크기 정보를 바탕으로 시스템 자원을 최적으로 활용하여 병렬 검색을 수행합니다. (항상 대소문자 무시)
        
        Args:
            file_with_sizes (list): (path, size) 튜플 리스트
            search_string (str): 검색할 문자열
            progress_callback (callable): 진행률 업데이트를 위한 콜백 함수 (completed, total)
        """
        results = []
        total = len(file_with_sizes)
        logger.info(AppStrings.LOG_WORKER_STARTED.format(search_string))

        # 1. 파일 크기 정보를 활용하여 대용량 파일 체크 (스캔 단계에서 이미 얻은 정보를 활용하여 I/O 비용 제거)
        LARGE_FILE_SIZE = 100 * 1024 * 1024
        large_files_count = sum(1 for _, size in file_with_sizes if size > LARGE_FILE_SIZE)

        # 2. 시스템 자원 및 파일 특성에 따른 동적 워커 수 결정
        actual_workers = self.num_cores
        if large_files_count > 0:
            actual_workers = max(1, min(self.num_cores // 2, 4))
            logger.info(AppStrings.LOG_ADAPTIVE_WORKERS.format(large_files_count, actual_workers))

        # 3. IPC 오버헤드를 최소화하기 위한 배치 전략
        # 10만 개 이상의 개별 태스크를 프로세스 풀에 던지는 대신, 적절한 크기로 묶어서 던집니다.
        batch_size = 500 if total > 10000 else 100
        batches = [file_with_sizes[i : i + batch_size] for i in range(0, total, batch_size)]

        with ProcessPoolExecutor(max_workers=actual_workers) as executor:
            # 배치 단위로 작업을 제출합니다.
            future_to_batch = {
                executor.submit(search_in_files_batch, batch, search_string): batch
                for batch in batches
            }

            completed = 0
            for future in future_to_batch:
                batch_res = future.result()
                if batch_res:
                    results.extend(batch_res)
                
                # 프로세스로부터 배치가 완료될 때마다 진행률을 갱신합니다.
                batch = future_to_batch[future]
                completed += len(batch)
                if progress_callback:
                    progress_callback(completed, total)

        return results
