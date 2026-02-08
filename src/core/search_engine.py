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
        # 1. 포함 관계에 있는 폴더 제거 (중복 스캔 방지)
        # 예: ['C:/A', 'C:/A/B'] -> ['C:/A']
        sorted_folders = sorted([os.path.abspath(f) for f in self.folders], key=len)
        unique_folders = []
        for i, folder in enumerate(sorted_folders):
            is_subfolder = False
            for parent in sorted_folders[:i]:
                # os.path.relpath를 사용하여 실제로 상위/하위 관계인지 확인
                try:
                    rel = os.path.relpath(folder, parent)
                    if not rel.startswith("..") and rel != ".":
                        is_subfolder = True
                        break
                except ValueError:
                    # 드라이브가 다른 경우 등 예외 처리
                    continue
            if not is_subfolder:
                unique_folders.append(folder)

        logger.info(AppStrings.LOG_SCANNING_FOLDERS.format(unique_folders, self.extensions, self.filename_filter))

        threads = []
        for folder in unique_folders:
            if os.path.exists(folder):
                t = threading.Thread(target=self._scan_folder, args=(folder,))
                threads.append(t)
                t.start()

        for t in threads:
            t.join()

        # 2. 최종 결과에서 중복 제거 (대소문자 구분 없는 윈도우 환경 고려 등)
        unique_files = list(set(os.path.abspath(p) for p in self.found_files))
        logger.info(AppStrings.LOG_FOUND_CANDIDATE_FILES.format(len(unique_files)))
        return unique_files

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


def search_in_excel_streaming(file_path, search_string):
    """
    openpyxl의 read_only=True 모드를 사용하여 대용량 엑셀 파일을 한 줄씩 스트리밍 검색합니다.
    이 방식은 속도는 Calamine보다 느리지만, 메모리 사용량을 매우 낮게 유지할 수 있습니다.
    """
    try:
        from openpyxl import load_workbook
        from openpyxl.utils import get_column_letter

        search_string = normalize_unicode(search_string)
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
                    if value is not None and search_string in str(value):
                        count += 1
                        cell_addr = f"{get_column_letter(col_idx)}{row_idx}"
                        matches.append((f"{sheet_name}!{cell_addr}", str(value)))

        wb.close()

        if count > 0:
            return (file_path, count, matches)
    except Exception as e:
        logger.debug(AppStrings.ERROR_EXCEL_SEARCH.format(file_path, AppStrings.ERROR_STREAMING.format(e)))
    return None


def search_in_excel(file_path, search_string):
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
            return search_in_excel_streaming(file_path, search_string)

        # 일반적인 크기인 경우 초고속 Calamine 엔진 사용
        from python_calamine import CalamineWorkbook
        from openpyxl.utils import get_column_letter

        search_string = normalize_unicode(search_string)
        workbook = CalamineWorkbook.from_path(file_path)
        matches = []
        count = 0

        for sheet_name in workbook.sheet_names:
            sheet = workbook.get_sheet_by_name(sheet_name)
            for row_idx, row in enumerate(sheet.to_python(), 1):
                for col_idx, value in enumerate(row, 1):
                    if value is not None and search_string in str(value):
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
        # 빈 파일은 검색 대상에서 제외 (mmap 오류 방지)
        if file_size == 0:
            return None
        # 1000개 x 500MB 시나리오 대응을 위해 텍스트 파일 제한도 2GB로 상향
        if file_size > 1024 * 1024 * 2000:  # 2GB 제한
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
    except (UnicodeDecodeError, LookupError) as e:
        # 인코딩 오류는 디버그 레벨로 기록 (일반적인 상황)
        logger.debug(f"Encoding error in {file_path}: {e}")
    except (IOError, OSError) as e:
        # 파일 I/O 오류 (권한, 잠금 등)
        logger.debug(f"File access error in {file_path}: {e}")
    except Exception as e:
        # 예상치 못한 오류는 에러 레벨로 기록
        logger.error(f"Unexpected error searching in {file_path}: {e}", exc_info=True)
    return None


class SearchEngine:
    def __init__(self):
        self.num_cores = multiprocessing.cpu_count()

    def search_parallel(self, file_list, search_string, progress_callback=None):
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
