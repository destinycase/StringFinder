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
    """파일명 및 확장자 조건에 맞는 파일을 스캔하는 클래스"""

    def __init__(self, folders, extensions, filename_filter=""):
        self.folders = folders
        self.extensions = [f".{ext.lower().strip('.')}" for ext in extensions]
        self.filename_filter = normalize_unicode(filename_filter.lower())
        self.found_files = []

    def scan(self):
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
        try:
            with os.scandir(folder) as it:
                for entry in it:
                    if entry.is_file():
                        # 확장자 체크
                        ext_match = not self.extensions or any(
                            entry.name.lower().endswith(ext) for ext in self.extensions
                        )

                        # 파일명 필터 체크 (fnmatch를 통한 glob 패턴 및 부분 일치 지원)
                        # 입력값이 패턴(*, ?)을 포함하면 fnmatch, 아니면 단순 부분 일치 사용
                        name = normalize_unicode(entry.name.lower())
                        if not self.filename_filter:
                            name_match = True
                        elif "*" in self.filename_filter or "?" in self.filename_filter:
                            # fnmatch.fnmatch는 내부적으로 os.path.normcase를 쓸 수 있으므로
                            # 윈도우에서는 대소문자 구분을 안 하지만, 명시적인 lower()와 조합하여 사용
                            name_match = fnmatch.fnmatch(name, self.filename_filter)
                        else:
                            name_match = self.filename_filter in name

                        if ext_match and name_match:
                            self.found_files.append(entry.path)
                    elif entry.is_dir():
                        self._scan_folder(entry.path)
        except PermissionError:
            pass
        except Exception as e:
            logger.debug(f"Error scanning {folder}: {e}")


def search_in_excel(file_path, search_string):
    """
    Excel 파일(.xlsx, .xlsm) 내에서 문자열 검색
    """
    try:
        import openpyxl

        # 속도를 위해 read_only, data_only 모드 사용
        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
        matches = []
        count = 0

        for sheet_name in wb.sheetnames:
            sheet = wb[sheet_name]
            for row_idx, row in enumerate(sheet.iter_rows(values_only=True), 1):
                for col_idx, value in enumerate(row, 1):
                    if value and search_string in str(value):
                        count += 1
                        # 셀 주소 계산 (예: A1, B2...)
                        from openpyxl.utils import get_column_letter

                        cell_addr = f"{get_column_letter(col_idx)}{row_idx}"
                        matches.append((f"{sheet_name}!{cell_addr}", str(value)))

        wb.close()
        if count > 0:
            return (file_path, count, matches)
    except Exception as e:
        logger.debug(AppStrings.ERROR_EXCEL_SEARCH.format(file_path, e))
    return None


def search_in_old_excel(file_path, search_string):
    """
    구형 Excel 파일(.xls) 내에서 문자열 검색
    """
    try:
        import xlrd

        # xlrd는 xls 파일만 지원 (2.0.0 이상)
        workbook = xlrd.open_workbook(file_path)
        matches = []
        count = 0

        for sheet_idx in range(workbook.nsheets):
            sheet = workbook.sheet_by_index(sheet_idx)
            sheet_name = sheet.name
            for row_idx in range(sheet.nrows):
                row_values = sheet.row_values(row_idx)
                for col_idx, value in enumerate(row_values):
                    if value and search_string in str(value):
                        count += 1
                        # 셀 주소 계산 (0-indexed -> A1 형식)
                        from openpyxl.utils import get_column_letter

                        cell_addr = f"{get_column_letter(col_idx + 1)}{row_idx + 1}"
                        matches.append((f"{sheet_name}!{cell_addr}", str(value)))

        if count > 0:
            return (file_path, count, matches)
    except Exception as e:
        logger.debug(AppStrings.ERROR_LEGACY_EXCEL_SEARCH.format(file_path, e))
    return None


def search_in_file(file_path, search_string):
    """
    단일 파일에서 문자열 검색
    확장자에 따라 전용 검색 함수 호출
    """
    search_string = normalize_unicode(search_string)
    # 확장자 체크
    ext = os.path.splitext(file_path)[1].lower()
    if ext in [".xlsx", ".xlsm"]:
        return search_in_excel(file_path, search_string)
    elif ext == ".xls":
        return search_in_old_excel(file_path, search_string)

    # 기본 텍스트 파일 검색
    matches = []
    count = 0
    try:
        with open(file_path, "rb") as f:
            if os.path.getsize(file_path) == 0:
                return None

            with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                search_bytes = search_string.encode("utf-8", errors="ignore")
                if mm.find(search_bytes) == -1:
                    return None

                mm.seek(0)
                content_bytes = mm.read()
                detected = chardet.detect(content_bytes[:10000])
                encoding = detected["encoding"] or "utf-8"

                try:
                    content = content_bytes.decode(encoding, errors="ignore")
                except Exception:
                    content = content_bytes.decode("utf-8", errors="ignore")

                MAX_LINE_LEN = 1000
                lines = content.splitlines()
                for i, line in enumerate(lines):
                    if search_string in line:
                        count += 1
                        clean_line = line.replace("\r", "").replace("\n", "")
                        if len(clean_line) > MAX_LINE_LEN:
                            clean_line = clean_line[:MAX_LINE_LEN] + "..."
                        matches.append((i + 1, clean_line))

        if count > 0:
            return (file_path, count, matches)
    except Exception:
        pass
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
