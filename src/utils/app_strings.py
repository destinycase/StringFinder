class AppStrings:
    # Main Window
    APP_NAME = "String Finder"

    # 상수로 하드코딩하여 빌드된 실행 파일에서도 정확한 버전을 표시함
    APP_VERSION = "3.7.4"
    APP_TITLE = "String Finder"

    ADD_TAB_BTN = "+"
    SEARCH_TAB_DEFAULT_TITLE = "Search"
    SELECT_FOLDER_TITLE = "검색할 폴더 선택"

    # Search Tab - General UI
    SEARCH_LABEL = "검색 문자열:"
    SEARCH_EDIT_PLACEHOLDER = "검색어 입력..."
    SEARCH_BTN = "검색"
    SEARCH_BTN_STOP = "중지"  # UX 개선: 검색 중 버튼 텍스트
    SEARCH_CASE_INSENSITIVE = "대소문자 구분 안 함"
    FILENAME_FILTER_LABEL = "파일명 필터:"
    FILENAME_EDIT_PLACEHOLDER = "입력한 문자열을 포함한 파일만 검색 (예: npc, fo / 콤마로 구분)..."

    # Filter Group Boxes
    FOLDER_GROUP = "검색 폴더 리스트"
    EXT_GROUP = "확장자 리스트"

    # Buttons
    ADD_FOLDER_BTN = "폴더 추가"
    ADD_EXT_BTN = "추가"
    DELETE_BTN = "제거"
    SELECT_ALL_BTN = "모두 선택"  # UX 개선: 필터 토글
    DESELECT_ALL_BTN = "모두 해제"  # UX 개선: 필터 토글

    # Placeholders
    EXT_EDIT_PLACEHOLDER = "확장자(예: txt)"

    # Result UI
    RESULT_GROUP_TITLE = "결과 및 로그"
    TAB_RESULTS = "검색 결과"
    TAB_LOGS = "검색 로그"
    RESULT_SUMMARY_TEMPLATE = "총 {0}개 파일 / 총 {1}개 일치 찾음"
    STATUS_SEARCH_SUMMARY = "검색 완료 - 총 {0}개 파일 / 총 {1}개 일치 찾음 (소요 시간: {2:.2f}초)"
    RESULT_EMPTY_MSG = "검색 된 결과가 없습니다"
    RESULT_FILTER_FILE_PLACEHOLDER = "결과 내 파일 필터..."
    RESULT_FILTER_FOLDER_PLACEHOLDER = "결과 내 폴더 필터..."
    MATCH_FILTER_CONTENT_PLACEHOLDER = "결과 상세 내 내용 필터..."
    MATCH_FILTER_NAME_PLACEHOLDER = "결과 상세 내 이름 필터..."
    MATCH_FILTER_KEY_PLACEHOLDER = "결과 상세 내 키 필터..."
    MATCH_FILTER_VALUE_PLACEHOLDER = "결과 상세 내 수치 필터..."
    RESULT_FILTER_PLACEHOLDER = "결과 내 파일명 필터..."
    RESULT_EMPTY_NO_FOLDER = "검색 대상 폴더를 추가해주세요."  # UX 개선
    RESULT_EMPTY_NO_MATCH = "'{}'에 대한 결과가 없습니다. 다른 검색어를 시도해보세요."  # UX 개선
    RESULT_EXPORT_BTN = "내보내기"
    RESULT_EXPORT_TITLE = "검색 결과 내보내기"
    RESULT_EXPORT_FILTER = "엑셀 파일 (*.xlsx);;텍스트 파일 (*.txt)"
    RESULT_EXPORT_ALL = "결과 모두 내보내기"
    RESULT_HEADER_COUNT = "일치 수"
    RESULT_HEADER_FOLDER = "폴더 경로"
    RESULT_HEADER_FILENAME = "파일명"
    RESULT_HEADER_EXTENSION = "확장자"
    RESULT_HEADER_POS = "위치"
    RESULT_HEADER_CONTENT = "라인 내용"
    RESULT_PREVIEW_TITLE = "상세 미리보기"
    RESULT_PREVIEW_ERROR = "이 파일은 상세 미리 보기를 표시할 수 없습니다."

    # Context Menu
    OPEN_FILE = "파일 열기"
    OPEN_FOLDER = "파일 위치 열기"
    COPY_PATH = "경로 복사 (Ctrl+C)"
    COPY_CONTENT = "내용 복사 (Ctrl+C)"
    ERROR_TITLE = "오류"
    SUCCESS_TITLE = "완료"
    INFO_TITLE = "알림"

    # UX & Symbols
    SYMBOL_CLOSE = "×"
    COLOR_RED = "#FF5555"
    SYMBOL_TAB_SEPARATOR = " "
    PROGRESS_BAR_FORMAT = "%p% (%v/%m)"
    SEARCH_TAB_TITLE_TEMPLATE = "{} {}"  # 예: "Search 1"
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

    # Tray & Shortcut Settings
    TRAY_OPEN = "열기"
    TRAY_QUIT = "종료"
    HOTKEY_LABEL = "전역 호출 단축키:"
    HOTKEY_EDIT_PLACEHOLDER = "여기를 클릭하고 단축키를 누르세요"
    HOTKEY_RECORDING = "단축키 기록 중... (키를 누르세요)"
    STARTUP_LABEL = "윈도우 시작 시 자동 실행:"
    STARTUP_ENABLE = "자동 실행"
    STARTUP_DISABLE = "자동 실행 안함"
    CLOSE_BEHAVIOR_LABEL = "닫기 버튼(X) 동작:"
    CLOSE_QUIT = "프로그램 종료"
    CLOSE_TRAY = "백그라운드에서 동작 (트레이로 숨김)"

    # History Context Menu
    DELETE_HISTORY_ITEM = "삭제"
    CLEAR_HISTORY = "전체 삭제"

    # Special Search
    SPECIAL_SEARCH_LABEL = "특수 검색 모드:"
    SPECIAL_SEARCH_OFF = "미사용"
    SPECIAL_SEARCH_XML_PARTIAL = "XML (부분 일치)"
    SPECIAL_SEARCH_XML_EXACT = "XML (전체 일치)"
    SPECIAL_SEARCH_JSON_PARTIAL = "JSON (부분 일치)"
    SPECIAL_SEARCH_JSON_EXACT = "JSON (전체 일치)"

    # 레거시 호환 및 내부 매핑 용도
    SPECIAL_SEARCH_ITEMS = ["미사용", "XML (부분 일치)", "XML (전체 일치)", "JSON (부분 일치)", "JSON (전체 일치)"]

    # Validation
    ERROR_NO_SELECTION = "검색 폴더와 확장자를 각각 최소 1개 선택해야합니다."
    ERROR_NO_FOLDER_SELECTION = "검색 폴더를 최소 1개 선택해야합니다."

    # Logging & Debug
    LOG_APP_STARTED = "애플리케이션 시작 중..."
    LOG_SEARCH_STARTED = "검색 시작. 검색어: '{}', 파일명 필터: '{}'"
    LOG_SCANNING_FOLDERS = "폴더 스캔 중: {} (확장자: {}, 파일명 필터: '{}')"
    LOG_FOUND_CANDIDATE_FILES = "검색 대상 파일 {}개 발견."
    LOG_NO_FILES_TO_SEARCH = "검색할 파일이 없습니다. UI를 초기화합니다."
    LOG_BACKGROUND_WORKER_INIT = "백그라운드 워커 초기화 중..."
    LOG_EMPTY_SEARCH_ABORTED = "검색어를 입력해 주세요."
    LOG_SEARCH_ALL_FILES_GUIDE = "전체 파일을 대상으로 스캔을 시작합니다."
    LOG_FILENAME_FILTER_GUIDE = (
        "입력한 문자열을 포함한 파일만 검색합니다. ','를 넣어 여러 문자열을 넣을 수 있습니다. (예시: npc, fo)"
    )
    LOG_SCAN_COMPLETED = "[Step 1] 파일 스캔 완료: {}개 파일 발견 (소요 시간: {:.3f}초)"
    LOG_SEARCH_COMPLETED_STEP = "[Step 2] 문자열 검색 완료 (소요 시간: {:.3f}초)"

    ERROR_EXCEL_SEARCH = "Excel 검색 오류 ({}): {}"

    LOG_WORKER_STARTED = "워커 시작. 검색어: '{}'"
    LOG_WORKER_SCANNING = "워커가 {}개 파일을 검색 중입니다..."
    LOG_WORKER_FINISHED = "검색 완료: 총 {}개의 파일을 찾았습니다. (전체 대상: {}개)"
    LOG_SEARCH_SKIPPED_SUMMARY = "규격 미준수 또는 오류로 스킵된 파일: {}개 (상세 목록은 로그 확인)"
    LOG_WORKER_STOPPED = "사용자에 의해 검색이 중단되었습니다."
    LOG_WORKER_ERROR = "워커 오류: {}"
    LOG_WORKER_PROGRESS = "검색 진행 중... {}% 완료 ({}/{})"
    LOG_WORKER_COLLECTING_RESULTS = "일치하는 항목들을 취합하여 표시를 준비 중입니다 ({}개 파일)..."
    LOG_UI_DISPLAYING_RESULTS = "검색 결과를 화면에 구성 중입니다. 잠시만 기다려 주세요..."
    LOG_BATCH_TIMEOUT = "배치 작업 타임아웃 (300초)"
    LOG_BATCH_ERROR = "배치 처리 중 오류: {}"
    ERROR_TRAY_UNAVAILABLE = "시스템 트레이를 사용할 수 없습니다."
    ERROR_RESOURCE_NOT_FOUND = "리소스를 찾을 수 없음: {}"
    ERROR_OPEN_DIR_FAILED = "폴더를 열 수 없습니다: {}"

    LOG_REGISTRY_ERROR = "레지스트리 조작 오류: {}"
    LOG_HOTKEY_REGISTERED = "전역 단축키 등록 완료: {}"
    LOG_HOTKEY_ERROR = "전역 단축키 등록 실패: {}"

    # Rust Engine Logging
    LOG_RUST_ENGINE_ACTIVATED = "Rust 고성능 검색 엔진이 활성화되었습니다. (대상 경로: {}개)"
    LOG_SMART_SCAN_STARTED = "스마트 스캔(Rust) 시작. 검색어: '{}'"

    # UI Styles & Fonts
    STYLE_SCROLLBAR = """
        QScrollBar:vertical { width: 16px; }
        QScrollBar:horizontal { height: 16px; }
        QScrollBar::handle:vertical { min-height: 30px; }
        QScrollBar::handle:horizontal { min-width: 30px; }
    """
    FONT_PREVIEW_WIN = "Consolas, Courier New, monospace"
    FONT_PREVIEW_MAC = "Menlo, Monaco, Courier, monospace"
    STYLE_DANGER_TEXT = "color: #ff5555;"
    STYLE_STOP_BTN_ACTIVE = "QPushButton { background-color: #ff5555; color: white; }"
    STYLE_SELECTION_INFO = "color: #888; font-size: 14px; margin: 20px;"
    STYLE_SETTINGS_RECORDING = "QLineEdit { border: 2px solid #3498db; background-color: #2c3e50; }"

    # Additional Errors
    ERROR_FILE_NOT_FOUND = "파일을 찾을 수 없습니다: {}"
    ERROR_PERMISSION_DENIED = "파일 접근 권한이 없습니다: {}"
    ERROR_SCAN_FAILED = "폴더 스캔 중 오류 ({}): {}"
    ERROR_REGISTRY_ACCESS = "시스템 설정 접근 권한이 없습니다."
    ERROR_HOTKEY_OCCUPIED = "사용하려는 단축키가 이미 사용 중입니다."

    BTN_CLOSE = "닫기"
    INFO_CLEAR_SUCCESS = "모든 데이터가 초기화되었습니다. 프로그램을 재시작해 주세요."

    # Excel / Model Headers (Internationalization support)
    HEADER_COUNT = "일치"
    HEADER_FOLDER = "폴더"
    HEADER_FILE = "파일"
    HEADER_POSITION = "위치"
    HEADER_CONTENT = "내용"
    HEADER_JSON_KEY = "키 (Key)"
    HEADER_JSON_VALUE = "수치 (Value)"
    HEADER_XML_NAME = "이름 (Element/Attr)"
    HEADER_XML_VALUE = "내용 (Value)"
    EXCEL_SHEET_TITLE = "검색 결과"
    EXCEL_MATCH_DETAIL = "매칭 상세"
    EXPORT_TEXT_HEADER = "=== {} 검색 결과 ==="
    EXPORT_SUMMARY_PREFIX = "요약: "

    # Adaptive Logic Messages
    LOG_LARGE_EXCEL_DETECTED = "대용량 Excel 감지 ({:.1f}MB). 메모리 안전 스트리밍 모드를 사용합니다."
    LOG_ADAPTIVE_WORKERS = "대용량 파일 {}개 감지. 메모리 안정성을 위해 워커 수를 {}개로 조절합니다."
    ERROR_STREAMING = "스트리밍 오류: {}"

    # Status Bar Messages
    STATUS_SEARCH_COMPLETED = "검색 완료 - 총 {}개 대상 - 총 {}개 일치 파일 - 총 {}개 일치 내용  / 검색 제외 된 파일 {}개(검색 로그 참고) / 소요 시간: {:.2f}초"
    STATUS_SEARCH_PROGRESS = "검색 진행 중... {} / {} 완료"

    # Newly consolidated strings
    LOG_FILE_NAME = "string_finder.log"
    LOG_APP_SHUTDOWN = "애플리케이션 종료 중... 로그 파일을 정리합니다."
    ERROR_LOG_DELETE = "종료 시 로그 파일 삭제 실패: {}"
    ERROR_FILE_TIMEOUT = "파일 처리 시간 초과 (300초): {}"
    ERROR_FILE_PROCESSING = "파일 처리 오류 {}: {}"
    ERROR_IO_DURING_SEARCH = "검색 중 I/O 오류 발생: {}"
    ERROR_SEARCH_LOG = "검색 오류: {}"
    ERROR_EXPORT = "내보내기 오류: {}"
    EXPORT_TEXT_LINE_PREFIX = "   L{}: {}\n"
    EXPORT_TEXT_SEPARATOR = "-" * 50 + "\n"
    LOG_SCAN_TASK_FAILED = "스캔 작업 실패: {}"
    ERROR_EXCEL_SEARCH_UNEXPECTED = "Excel 검색 중 예기치 않은 오류 발생 ({}): {}"
    LOG_SEARCH_ERROR_IN_FILE = "파일 검색 중 오류 발생 {}: {}"

    # 추가 통합 문자열
    STATUS_ERROR_PREFIX = "에러: "
    STYLE_PREVIEW_HIGHLIGHT_LINE = (
        "background-color: #404040; color: #ffffff; border-left: 3px solid #ff9900; font-weight: bold;"
    )

    # Keyboard / Hotkey Names
    KEY_CTRL = "ctrl"
    KEY_SHIFT = "shift"
    KEY_ALT = "alt"
    KEY_META = "meta"
    KEY_SPACE = "space"
    KEY_ESC = "esc"
    KEY_DELETE = "delete"
    KEY_BACKSPACE = "backspace"
    KEY_ENTER = "enter"
    KEY_TAB = "tab"
    KEY_UP = "up"
    KEY_DOWN = "down"
    KEY_LEFT = "left"
    KEY_RIGHT = "right"

    # Internal Identifiers
    TYPE_SEARCH = "search"
    TYPE_FILENAME = "filename"
    TYPE_FOLDER = "folder"
    TYPE_EXT = "ext"
    VIEW_RESULT = "result"
    VIEW_MATCH = "match"
    STATUS_SKIPPED = "SKIPPED"

    # Encodings
    ENC_UTF8 = "utf-8"
    ENC_UTF8_SIG = "utf-8-sig"
    ENC_UTF16 = "utf-16"
    ENC_UTF16_BE = "utf-16-be"
    ENC_CP949 = "cp949"

    # Special Mode Strings
    MODE_XML = "XML"
    MODE_JSON = "JSON"
    MODE_EXACT = "전체 일치"
    MODE_PARTIAL = "부분 일치"

    # Additional Log/Error Messages
    LOG_RUST_SUCCESS = "Rust 검색 엔진 (sf_engine)을 성공적으로 불러왔습니다."
    LOG_RUST_FALLBACK = "Rust 검색 엔진을 찾을 수 없습니다. Python 구현으로 대체합니다."
    LOG_RUST_RETRY_PYTHON = (
        "{}에 대해 Rust가 빈 결과를 반환했습니다. 인코딩 안전을 위해 Python으로 대체하여 재검색합니다."
    )
    LOG_JSON_LIMIT = "{}에서 JSON 재귀 제한을 초과했습니다."
    ERROR_JSON_PARSE = "JSON 형식이 올바르지 않음 (JSONDecodeError)"
    ERROR_EXCEL_LOAD = "Excel 파일 로드 오류: {}"
    ERROR_EXCEL_CALAMINE = "python-calamine 라이브러리가 필요합니다."
    ERROR_EXCEL_EXCEPTION = "Excel 처리 예외: {}"
    ERROR_JSON_PARSE_EX = "JSON 파싱 오류: {}"
    ERROR_XML_PARSE_EX = "XML 파싱 오류: {}"
    LOG_FILE_TIMEOUT = "파일 처리 시간 초과 (300초): {}"
    LOG_THREAD_TIMEOUT_SCAN = "스캔 스레드가 제시간에 종료되지 않아 강제 종료합니다."
    LOG_THREAD_TIMEOUT_SEARCH = "검색 스레드가 제시간에 종료되지 않아 강제 종료합니다."
    LOG_RUST_FAST_PATH = "Rust 고속 경로가 활성화되었습니다. 폴더: {}, 확장자: {}"

    # New Korean Log Constants (Previously Hard-coded)
    ERROR_CONFIG_CORRUPT = "설정 파일이 손상되었습니다. 기본값을 사용합니다: {}"
    ERROR_CONFIG_READ = "설정 파일을 읽는 중 오류 발생: {}"
    ERROR_CONFIG_SAVE = "설정 저장 오류: {}"
    ERROR_CONFIG_SAVE_UNEXPECTED = "설정 저장 중 예기치 않은 오류 발생: {}"
    ERROR_OPEN_DATA_DIR = "데이터 폴더를 여는데 실패했습니다: {}"
    ERROR_PREVIEW = "미리보기 오류: {}"
    LOG_SKIP_REASON = " [Skip] {} \n\t -> 원인: {}"
    LOG_SKIP_SIMPLE = " [Skip] {}"
    ERROR_STOP_SEARCH_TAB = "탭 {}에서 검색 중지 중 오류 발생: {}"
    LOG_JSON_DEPTH_RECURSION = "RecursionError: {}에서 JSON 깊이가 너무 깊습니다."
    LOG_BINARY_CHECK_ERROR = "이진 파일 사전 검사 오류 ({}): {}"
    LOG_RUST_ENGINE_ERROR = "{}에서 Rust 엔진 오류 발생: {}. Python으로 대체합니다."
    LOG_SEARCH_ERROR_FILE = "{}에서 검색 오류 발생: {}"
    LOG_SF_ENGINE_NOT_FOUND = "{}를 찾을 수 없습니다. Python으로 대체합니다."
    LOG_RUST_DIR_SEARCH_ERROR = "Rust 디렉토리 검색 오류: {}"
    LOG_RUST_SMART_SCAN_ERROR = "Rust 스마트 스캔 오류: {}"
    # UI 및 스타일 설정
    STYLE_PREVIEW_EMPTY = "QTextEdit { background-color: transparent; border: none; }"
    STYLE_EMPTY_LABEL = "color: {}; font-size: 16px; font-weight: bold;"


# 클래스 정의 후 타이틀 최종 확정
AppStrings.APP_TITLE = f"{AppStrings.APP_NAME} v{AppStrings.APP_VERSION}"
