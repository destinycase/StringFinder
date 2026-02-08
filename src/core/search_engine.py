import os
import mmap
import multiprocessing
import threading
from concurrent.futures import ProcessPoolExecutor
import chardet
from utils.logger import logger
from utils.app_strings import AppStrings
import fnmatch
import unicodedata


def normalize_unicode(text):
    """유니코드 자소 분리 방지를 위한 NFC 정규화"""
    if isinstance(text, str):
        return unicodedata.normalize("NFC", text)
    return text


class FileScanner:
    """
    지정된 폴더들을 순회하며 확장자 및 파일명 필터 조건에 맞는 파일 목록을 수집하는 클래스입니다.
    멀티스레딩을 사용하여 여러 폴더를 동시에 스캔합니다.
    """

    def __init__(self, folders, extensions, filename_filter=""):
        self.folders = folders
        self.extensions = [f".{ext.lower().strip('.')}" for ext in extensions]
        self.filename_filter = normalize_unicode(filename_filter.lower())
        self.found_files = []

    def scan(self):
        """
        설정된 폴더 및 필터 조건으로 전체 스캔을 수행하고 파일 경로 리스트를 반환합니다.
        
        Returns:
            list[str]: 조건에 부합하는 전체 파일 경로 목록
        """
        logger.info(AppStrings.LOG_SCANNING_FOLDERS.format(self.folders, self.extensions, self.filename_filter))
        threads = []
        for folder in self.folders:
            if os.path.exists(folder):
                t = threading.Thread(target=self._scan_folder, args=(folder,))
                threads.append(t)
                t.start()

        for t in threads:
            t.join()

        logger.info(AppStrings.LOG_FOUND_CANDIDATE_FILES.format(len(self.found_files)))
        return self.found_files

    def _scan_folder(self, folder):
        """
        개별 폴더를 재귀적으로 스캔합니다.
        
        Args:
            folder (str): 스캔할 폴더 경로
        """
        try:
            with os.scandir(folder) as it:
                for entry in it:
                    if entry.is_file():
                        # 확장자 체크: 설정된 확장자가 없으면 모든 파일 허용, 있으면 해당 확장자만 허용
                        ext_match = not self.extensions or any(
                            entry.name.lower().endswith(ext) for ext in self.extensions
                        )

                        # 파일명 필터 체크: fnmatch를 통한 glob 패턴(*, ?) 및 일반 문자열 부분 일치 지원
                        name = normalize_unicode(entry.name.lower())
                        if not self.filename_filter:
                            name_match = True
                        elif "*" in self.filename_filter or "?" in self.filename_filter:
                            name_match = fnmatch.fnmatch(name, self.filename_filter)
                        else:
                            name_match = self.filename_filter in name

                        if ext_match and name_match:
                            self.found_files.append(entry.path)
                    elif entry.is_dir():
                        # 디렉토리인 경우 재귀 호출 (하위 폴더 포함 스캔)
                        self._scan_folder(entry.path)
        except PermissionError:
            # 접근 권한이 없는 폴더는 조용히 넘어감
            pass
        except Exception as e:
            logger.debug(AppStrings.ERROR_SCAN_FAILED.format(folder, str(e)))


def search_in_excel(file_path, search_string):
    """
    python-calamine을 사용하여 Excel 파일 내의 모든 시트를 초고속으로 검색합니다.
    
    Args:
        file_path (str): 엑셀 파일 경로
        search_string (str): 검색할 문자열 (NFC 정규화 권장)
        
    Returns:
        tuple: (파일경로, 발견횟수, 매칭정보) 형태의 데이터 / 발견되지 않으면 None
    """
    try:
        from python_calamine import CalamineWorkbook

        search_string = normalize_unicode(search_string)
        # Calamine은 별도의 인코딩 걱정 없이 엑셀 파일을 빠르게 로드함
        workbook = CalamineWorkbook.from_path(file_path)
        matches = []
        count = 0

        for sheet_name in workbook.sheet_names:
            sheet = workbook.get_sheet_by_name(sheet_name)
            # to_python()은 스타일을 제외하고 데이터만 빠르게 파이썬 리스트로 변환함
            for row_idx, row in enumerate(sheet.to_python(), 1):
                for col_idx, value in enumerate(row, 1):
                    if value is not None and search_string in str(value):
                        count += 1
                        # openpyxl의 유틸리티를 사용하여 '1' -> 'A'와 같이 열 문자로 변환
                        from openpyxl.utils import get_column_letter

                        cell_addr = f"{get_column_letter(col_idx)}{row_idx}"
                        matches.append((f"{sheet_name}!{cell_addr}", str(value)))

        if count > 0:
            return (file_path, count, matches)
    except Exception as e:
        logger.debug(AppStrings.ERROR_EXCEL_SEARCH.format(file_path, str(e)))
    return None


def detect_encoding_quickly(content_bytes):
    """
    chardet을 사용하기 전, 가장 빈번하게 발생하는 인코딩들을 휴리스틱하게 탐지하여 성능을 높입니다.
    가장 먼저 UTF-8을 시도하고, 실패 시 한국어용 CP949를 시도합니다.
    
    Args:
        content_bytes (bytes): 탐지할 파일의 샘플 바이트 데이터
        
    Returns:
        str: 탐지된 인코딩 이름 (기본값 "utf-8")
    """
    # 1. 현대적인 UTF-8 우선 시도 (파일 크기가 클 수 있으므로 앞부분 10KB만 샘플링)
    try:
        content_bytes[:10000].decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass

    # 2. 한국어 윈도우 환경에서 흔히 쓰이는 CP949 (EUC-KR 확장) 시도
    try:
        content_bytes[:10000].decode("cp949")
        return "cp949"
    except UnicodeDecodeError:
        pass

    # 3. 위 두 가지로 실패 시, chardet 라이브러리를 통해 통계적 인코딩 분석 수행
    detected = chardet.detect(content_bytes[:10000])
    return detected["encoding"] or "utf-8"


def search_in_file(file_path, search_string):
    """
    단일 텍스트 또는 엑셀 파일 내에서 지정된 문자열을 검색합니다.
    성능을 위해 mmap과 지연 디코딩(Lazy Decoding)을 사용합니다.
    
    Args:
        file_path (str): 대상 파일 경로
        search_string (str): 검색할 문자열
        
    Returns:
        tuple: (파일경로, 발견횟수, 매칭정보 리스트) / 결과가 없거나 오류 시 None
    """
    search_string_nfc = normalize_unicode(search_string)
    ext = os.path.splitext(file_path)[1].lower()

    # 엑셀 파일의 경우 별도 전용 엔진(Calamine) 호출
    if ext in [".xlsx", ".xlsm", ".xls", ".xlsb"]:
        return search_in_excel(file_path, search_string_nfc)

    # 일반 텍스트 파일 처리를 위한 초기화
    matches = []
    count = 0
    try:
        if not os.path.exists(file_path):
            return None
            
        file_size = os.path.getsize(file_path)
        # 파일이 너무 크거나 비어있는 경우 스킵 (메모리 부족 및 무의미한 연산 방지)
        if file_size == 0 or file_size > 1024 * 1024 * 500:  # 500MB 제한
            return None

        with open(file_path, "rb") as f:
            # mmap을 사용하여 파일을 메모리에 직접 매핑 (대용량 파일 검색 성능 향상)
            with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                # 1. 바이트 수준 검색: 인코딩 전에 바이트 데이터에서 키워드 존재 여부 확인 (최고속 스캐닝)
                search_bytes = search_string_nfc.encode("utf-8", errors="ignore")
                if mm.find(search_bytes) == -1:
                    # UTF-8 바이트로 발견되지 않은 경우 한국어 환경 특성상 CP949 바이트로도 재시도
                    try:
                        search_bytes_cp949 = search_string_nfc.encode("cp949")
                        if mm.find(search_bytes_cp949) == -1:
                            return None
                    except UnicodeEncodeError:
                        return None

                # 2. 인코딩 감지 및 디코딩 (여기까지 오면 파일 내에 키워드가 확실히 존재함)
                mm.seek(0)
                sample = mm.read(min(file_size, 10000))
                encoding = detect_encoding_quickly(sample)

                mm.seek(0)
                content_bytes = mm.read()
                content = content_bytes.decode(encoding, errors="ignore")

                # 3. 라인별 실제 매칭 루프 및 상세 정보 생성
                MAX_LINE_LEN = 1000  # 너무 긴 라인은 UI 성능을 위해 잘라냄
                lines = content.splitlines()
                for i, line in enumerate(lines):
                    if search_string_nfc in line:
                        count += 1
                        clean_line = line.replace("\r", "").replace("\n", "")
                        if len(clean_line) > MAX_LINE_LEN:
                            clean_line = clean_line[:MAX_LINE_LEN] + "..."
                        matches.append((i + 1, clean_line))

        if count > 0:
            return (file_path, count, matches)
    except Exception as e:
        logger.debug(f"Error searching in {file_path}: {e}")
    return None


class SearchEngine:
    def __init__(self):
        self.num_cores = multiprocessing.cpu_count()

    def search_parallel(self, file_list, search_string, progress_callback=None):
        """파일 리스트에 대해 병렬 검색을 수행합니다."""
        results = []
        total = len(file_list)
        logger.info(AppStrings.LOG_WORKER_STARTED.format(search_string))

        with ProcessPoolExecutor(max_workers=self.num_cores) as executor:
            # 파일을 배치로 나누기보다 하나씩 처리하여 실시간성 확보
            future_to_file = {executor.submit(search_in_file, f, search_string): f for f in file_list}

            completed = 0
            for future in future_to_file:
                res = future.result()
                if res:
                    results.append(res)

                completed += 1
                if progress_callback:
                    progress_callback(completed, total)

        return results
