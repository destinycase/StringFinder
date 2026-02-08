import os
import sys


class AppStrings:
    # Main Window
    APP_NAME = "String Finder"

    # 상수로 하드코딩하여 빌드된 실행 파일에서도 정확한 버전을 표시함
    APP_VERSION = "1.0.0"
    APP_TITLE = "String Finder"

    ADD_TAB_BTN = "+"
    SEARCH_TAB_DEFAULT_TITLE = "Search"

    # Search Tab - General UI
    SEARCH_LABEL = "검색 문자열:"
    SEARCH_EDIT_PLACEHOLDER = "검색할 문자열을 입력하세요..."
    SEARCH_BTN = "검색"
    FILENAME_FILTER_LABEL = "파일명 필터:"
    FILENAME_EDIT_PLACEHOLDER = "파일명 일부 입력 (비워두면 전체 검색)..."

    # Filter Group Boxes
    FOLDER_GROUP = "검색 폴더 리스트"
    EXT_GROUP = "확장자 리스트"

    # Buttons
    ADD_FOLDER_BTN = "폴더 추가"
    ADD_EXT_BTN = "추가"
    DELETE_BTN = "제거"

    # Placeholders
    EXT_EDIT_PLACEHOLDER = "확장자(예: txt)"

    # Result UI
    RESULT_GROUP_TITLE = "검색 결과"
    RESULT_SUMMARY_TEMPLATE = "총 {0}개 파일 / 총 {1}개 일치 찾음"
    STATUS_SEARCH_SUMMARY = "검색 완료 - 총 {0}개 파일 / 총 {1}개 일치 찾음 (소요 시간: {2:.2f}초)"
    RESULT_EMPTY_MSG = "검색 결과가 없습니다. 상단에서 검색어나 필터를 조정해 보세요."
    RESULT_EXPORT_BTN = "내보내기"
    RESULT_EXPORT_TITLE = "검색 결과 내보내기"
    RESULT_EXPORT_FILTER = "엑셀 파일 (*.xlsx);;텍스트 파일 (*.txt)"
    RESULT_EXPORT_ALL = "결과 모두 내보내기"
    RESULT_HEADER_COUNT = "일치 수"
    RESULT_HEADER_FILE = "파일"
    RESULT_HEADER_POS = "위치"
    RESULT_HEADER_CONTENT = "라인 내용"
    RESULT_PREVIEW_TITLE = "상세 미리보기"
    RESULT_PREVIEW_ERROR = "이 파일을 상세 미리보기를 표시할 수 없습니다."

    # Context Menu
    OPEN_FILE = "파일 열기"
    OPEN_FOLDER = "파일 위치 열기"
    COPY_PATH = "경로 복사 (Ctrl+C)"
    COPY_CONTENT = "내용 복사 (Ctrl+C)"

    # UX & Symbols
    SYMBOL_CLOSE = "×"
    COLOR_RED = "#FF5555"
    HISTORY_ACTION_CLEAR = "action_clear"
    HISTORY_CLEAR_ALL = "--- 히스토리 전체 삭제 ---"

    # Settings Dialog
    SETTINGS_TITLE = "설정"
    THEME_LABEL = "테마 설정:"
    THEME_DARK = "Dark"
    THEME_LIGHT = "Light"
    OPEN_DATA_DIR_BTN = "데이터 폴더 열기"
    CLEAR_ALL_DATA_BTN = "모든 데이터 초기화"
    CLEAR_ALL_DATA_CONFIRM = "모든 설정과 히스토리가 삭제됩니다. 계속하시겠습니까?"

    # History Context Menu
    DELETE_HISTORY_ITEM = "삭제"
    CLEAR_HISTORY = "전체 삭제"

    # Logging & Debug
    LOG_APP_STARTED = "애플리케이션 시작 중..."
    LOG_SEARCH_STARTED = "검색 시작. 검색어: '{}', 파일명 필터: '{}'"
    LOG_SCANNING_FOLDERS = "폴더 스캔 중: {} (확장자: {}, 파일명 필터: '{}')"
    LOG_FOUND_CANDIDATE_FILES = "검색 대상 파일 {}개 발견."
    LOG_NO_FILES_TO_SEARCH = "검색할 파일이 없습니다. UI를 초기화합니다."
    LOG_BACKGROUND_WORKER_INIT = "백그라운드 워커 초기화 중..."
    LOG_EMPTY_SEARCH_ABORTED = "검색어가 비어 있어 검색을 중단합니다."

    ERROR_EXCEL_SEARCH = "Excel 검색 오류 ({}): {}"
    ERROR_LEGACY_EXCEL_SEARCH = "구형 Excel 검색 오류 ({}): {}"

    LOG_WORKER_STARTED = "워커 시작. 검색어: '{}'"
    LOG_WORKER_SCANNING = "워커가 {}개 파일을 검색 중입니다..."
    LOG_WORKER_FINISHED = "워커 종료. {}개 파일에서 일치하는 항목 발견 (총 {}개 파일 중)."
    LOG_WORKER_STOPPED = "워커가 조기에 중단되었습니다."
    LOG_WORKER_ERROR = "워커 오류: {}"


# 클래스 정의 후 타이틀 최종 확정
AppStrings.APP_TITLE = f"{AppStrings.APP_NAME} v{AppStrings.APP_VERSION}"
