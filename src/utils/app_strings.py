class AppStrings:
    APP_NAME = "StringFinder"
    # 앱 기본 정보
    APP_TITLE = "StringFinder"
    APP_VERSION = "v4.21.0"  # PyInstaller 빌드 시 override됨
    SEARCH_LABEL = "검색 문자열:"
    ADD_TAB_BTN = "+"

    SEARCH_TAB_DEFAULT_TITLE = "Search"
    SELECT_FOLDER_TITLE = "검색할 폴더 선택"
    SEARCH_EDIT_PLACEHOLDER = "검색어 입력..."
    SEARCH_BTN = "검색"
    SEARCH_BTN_STOP = "중지"
    SEARCH_BTN_STOPPING = "중지 중.."
    FILENAME_FILTER_LABEL = "파일명 필터:"

    FILENAME_EDIT_PLACEHOLDER = "입력한 문자열을 포함한 파일만 검색 (예: npc, fo / 콤마로 구분)..."

    # Filter Group Boxes
    # Filter Group Boxes

    # Buttons
    ADD_FOLDER_BTN = "폴더 추가"
    ADD_EXT_BTN = "추가"
    DELETE_BTN = "제거"
    SELECT_ALL_BTN = "모두 선택"
    DESELECT_ALL_BTN = "모두 해제"
    BTN_CLOSE = "닫기"

    # Placeholders
    EXT_EDIT_PLACEHOLDER = "확장자(예: txt)"

    # Result UI
    TAB_RESULTS = "검색 결과"

    TAB_LOGS = "검색 로그"
    RESULT_EMPTY_MSG = "검색 된 결과가 없습니다"
    RESULT_FILTER_FILE_PLACEHOLDER = "결과 내 파일 필터..."
    RESULT_FILTER_FOLDER_PLACEHOLDER = "결과 내 폴더 필터..."
    MATCH_FILTER_CONTENT_PLACEHOLDER = "결과 상세 내 내용 필터..."
    MATCH_FILTER_NAME_PLACEHOLDER = "결과 상세 내 이름 필터..."
    MATCH_FILTER_KEY_PLACEHOLDER = "결과 상세 내 키 필터..."
    MATCH_FILTER_VALUE_PLACEHOLDER = "결과 상세 내 수치 필터..."
    MATCH_FILTER_ARCHIVE_NS_PLACEHOLDER = "결과 상세 내 네임스페이스 필터..."
    MATCH_FILTER_ARCHIVE_SOURCE_PLACEHOLDER = "결과 상세 내 Source 필터..."
    MATCH_FILTER_ARCHIVE_TRANS_PLACEHOLDER = "결과 상세 내 Translation 필터..."
    MATCH_FILTER_EXCEL_POS_PLACEHOLDER = "결과 상세 내 위치 필터..."
    MATCH_FILTER_EXCEL_VAL_PLACEHOLDER = "결과 상세 내 값 필터..."
    RESULT_EMPTY_NO_FOLDER = "검색 대상 폴더를 추가해주세요."

    RESULT_EMPTY_NO_MATCH = "'{}'에 대한 결과가 없습니다. 다른 검색어를 시도해보세요."
    RESULT_EXPORT_TITLE = "검색 결과 내보내기"

    RESULT_EXPORT_FILTER = "엑셀 파일 (*.xlsx);;텍스트 파일 (*.txt)"
    RESULT_EXPORT_ALL = "결과 모두 내보내기"

    RESULT_PREVIEW_TITLE = "상세 미리보기"
    RESULT_PREVIEW_ERROR = "이 파일은 상세 미리 보기를 표시할 수 없습니다."

    # Dock Titles
    DOCK_SEARCH_TITLE = "검색 설정"
    DOCK_FOLDER_TITLE = "폴더 필터"
    DOCK_EXT_TITLE = "확장자 필터"
    DOCK_FILENAME_TITLE = "파일명 필터"
    DOCK_RESULT_TITLE = "검색 결과"

    FILENAME_LIST_PLACEHOLDER = "필터 단어(예: npc)"

    # Context Menu
    OPEN_FILE = "파일 열기"
    OPEN_FOLDER = "파일 위치 열기"
    COPY_PATH = "경로 복사 (Ctrl+C)"
    COPY_CONTENT = "내용 복사 (Ctrl+C)"
    ERROR_TITLE = "오류"
    SUCCESS_TITLE = "완료"
    INFO_TITLE = "알림"
    INFO_CLEAR_SUCCESS = "데이터가 성공적으로 초기화되었습니다."
    INFO_RESET_LAYOUT_DONE = "현재 탭의 레이아웃이 초기화되었습니다."

    # UX & Symbols
    STATUS_READY = "검색 준비"
    SEARCH_TAB_TITLE_TEMPLATE = "{} {}"
    HISTORY_CLEAR_ALL = "--- 히스토리 전체 삭제 ---"

    # Settings Dialog
    SETTINGS_TITLE = "설정"
    THEME_LABEL = "테마 설정:"
    THEME_DARK = "Dark"
    THEME_LIGHT = "Light"
    OPEN_DATA_DIR_BTN = "데이터 폴더 열기"
    CLEAR_ALL_DATA_BTN = "모든 데이터 초기화"
    CLEAR_ALL_DATA_CONFIRM = "모든 설정과 히스토리가 삭제됩니다. 계속하시겠습니까?"

    # Settings Groups
    SETTINGS_GROUP_APPEARANCE = "외형"
    SETTINGS_GROUP_OPERATION = "동작 설정"
    SETTINGS_GROUP_DATA = "데이터 설정"
    SETTINGS_GROUP_LAB = "실험실"

    LOG_RETENTION_LABEL = "로그 파일 보존:"

    # Tray Icon
    TRAY_OPEN = "열기"
    TRAY_QUIT = "종료"

    # Combo Box States
    COMBO_LOCKED = "잠금"
    COMBO_UNLOCKED = "해제"

    # Settings Dialog - Operation
    STARTUP_LABEL = "윈도우 시작 시 자동 실행:"
    STARTUP_ENABLE = "자동 실행"
    STARTUP_DISABLE = "자동 실행 안함"
    CLOSE_BEHAVIOR_LABEL = "닫기 버튼(X) 동작:"
    CLOSE_QUIT = "프로그램 종료"
    CLOSE_TRAY = "백그라운드에서 동작 (트레이로 숨김)"
    HOTKEY_LABEL = "백그라운드 실행 중 호출 단축키:"
    HOTKEY_EDIT_PLACEHOLDER = "여기를 클릭하고 단축키를 누르세요"
    HOTKEY_RECORDING = "단축키 기록 중... (키를 누르세요)"

    # Pagination
    PAGINATION_PREV = "◀ 이전"
    PAGINATION_NEXT = "다음 ▶"
    PAGINATION_PAGE = "페이지:"
    PAGINATION_OF = "/"
    PAGINATION_DISPLAY = "표시:"
    PAGINATION_SIZE_1000 = "1000개씩"
    PAGINATION_SIZE_2000 = "2000개씩"
    PAGINATION_SIZE_5000 = "5000개씩"

    # Settings Dialog - Data Management
    COMBO_ENABLE = "활성화"
    COMBO_DISABLE = "비활성화"
    MAX_FILES_LABEL = "최대 보관 파일 수:"
    MAX_DAYS_LABEL = "최대 보관 일수:"

    # Layout Controls
    MENU_LOCK_LAYOUT = "레이아웃 고정"
    MENU_RESET_LAYOUT = "레이아웃 초기화"

    # History Context Menu

    ERROR_SESSION_SAVE = "세션 저장 실패: {}"

    SPECIAL_SEARCH_LABEL = "특수 검색 모드:"
    SPECIAL_SEARCH_OFF = "미사용"
    SPECIAL_SEARCH_ITEMS = [
        "미사용",
        "XML (부분 일치)",
        "XML (전체 일치)",
        "JSON (부분 일치)",
        "JSON (전체 일치)",
        "Archive (부분 일치)",
        "Archive (전체 일치)",
        "Excel (부분 일치)",
        "Excel (전체 일치)",
    ]

    # Validation
    ERROR_NO_SELECTION = "검색 폴더와 확장자를 각각 최소 1개 선택해야합니다."

    # --- [시스템 관련 로그] ---
    LOG_SYS_APP_STARTED_DECO = "--- 애플리케이션 시작 ---"

    LOG_SYS_SHUTDOWN = "[시스템] 애플리케이션 종료 중... 로그 파일을 정리합니다."
    LOG_SYS_LOG_CLEANUP_DONE = "[시스템] 로그 파일 {}개 정리 완료."

    LOG_SYS_RUST_SUCCESS = "[시스템] 검색 엔진(Rust sf_engine)을 성공적으로 불러왔습니다."
    LOG_SYS_RUST_FALLBACK = "[시스템] 검색 엔진(Rust)을 찾을 수 없습니다. 검색 엔진(Python)으로 대체합니다."
    LOG_SYS_TRAY_UNAVAILABLE = "[시스템][경고] 시스템 트레이를 사용할 수 없습니다."
    LOG_SYS_HOTKEY_ERROR = "[시스템][오류] 전역 단축키 등록 실패: {}"
    LOG_SYS_RE_COMPILE_ERROR = "[시스템][디버그] 정규식 초기 컴파일 실패 ({}): {}"

    LOG_SYS_REGISTRY_ERROR = "[시스템][오류] 레지스트리 조작 오류: {}"

    LOG_SYS_SF_ENGINE_NOT_FOUND = "[시스템][경고] 검색 엔진(Rust sf_engine) 라이브러리를 찾을 수 없습니다: {}"

    # Settings Dialog - Search Cache
    SETTINGS_GROUP_PERFORMANCE = "성능(실험실)"
    CACHE_ENABLE_LABEL = "검색 결과 캐싱:"
    CACHE_ENABLE_DESC = (
        "이전 검색 결과를 저장하여 재검색 시 성능 향상. 단, 캐시 파일이 너무 커질 수 있으므로 주의가 필요합니다."
    )
    CACHE_CLEAR_BTN = "캐시 삭제"
    CACHE_STATS_LABEL = "캐시 통계:"
    CACHE_STATS_FORMAT = "저장: {count}개 | 히트율: {hit_rate:.1f}% | 메모리: {memory}MB"

    # Critical Errors
    ERROR_SEARCH_CRITICAL_TITLE = "검색 오류"
    ERROR_SEARCH_CRITICAL_MSG = "검색을 시작하는 중 오류가 발생했습니다.\n로그를 확인해주세요.\n\n오류: {}"
    ERROR_SEARCH_START_FAIL = "치명적인 검색 시작 오류:\n{}\n\n{}"
    LOG_SYS_UNCAUGHT_EXCEPTION = "처리되지 않은 예외 발생"

    # --- [검색 흐름 로그] ---
    LOG_SCH_STARTED = "[검색] 시작"
    LOG_SCH_COND_QUERY = "[검색] 조건- 검색어 '{}'"
    LOG_SCH_COND_FOLDER = "[검색] 조건- 폴더: '{}'"
    LOG_SCH_COND_FILTER = "[검색] 조건- 파일명 필터: {}"
    LOG_SCH_COND_SPECIAL = "[검색] 조건- 특수 모드: {}"
    LOG_SCH_COND_EXT = "[검색] 조건- 확장자: '{}'"
    LOG_SCH_PATH_ENTER = "[검색] 검색 경로 진입"

    LOG_SCH_SCAN_DONE = "[검색] 파일 스캔 완료: {}개 파일 발견 (소요 시간: {:.3f}초)"
    LOG_SCH_SCAN_DONE_SIMPLE = "[검색] 파일 스캔 완료 (소요 시간: {:.3f}초)"
    LOG_SCH_SCAN_DONE_FAST_PATH = "[검색] 파일 스캔 완료 (Fast Path)"

    LOG_SCH_SEARCH_DONE = "[검색] 문자열 검색 완료 (소요 시간: {:.3f}초)"
    LOG_SCH_NO_FILES = "[검색] 검색할 파일이 없습니다. UI를 초기화합니다."
    LOG_SCH_EMPTY_QUERY = "[검색][경고] 검색어를 입력해 주세요."

    LOG_SCH_RUST_MODE = "[시스템] Rust 고성능 엔진 모드 활성화"
    LOG_SCH_PYTHON_MODE = "[시스템] Python 호환 엔진 모드 활성화"
    LOG_SCH_SCAN_DONE_FAST_PATH = "[시스템] Rust 엔진: 스캔 및 검색 통합 수행"

    LOG_SCH_STOP_REQUESTED = "[검색] 사용자에 의해 중지 요청을 받았습니다. 현재 작업을 정리 중입니다..."

    LOG_SCH_SKIP_COUNT = "[검색][경고] 규격 미준수 또는 오류로 스크린된 파일: {}개 (상세 내역은 로그 참조)"
    LOG_SCH_BINARY_CHECK_FAIL = "[검색][오류] 바이너리 체크 중 오류 ({}): {}"
    LOG_SCH_PARSE_ERROR = "[검색][오류] {} 파싱 실패 ({}): {}"
    LOG_SCH_UNEXPECTED_ERROR = "[검색][오류] {} 검색 중 예기치 못한 오류 ({}): {}"
    LOG_SCH_ERROR = "[검색][오류] {}"
    LOG_SCH_RUST_ENGINE_ERROR = "[검색][오류] 검색 엔진(Rust) 오류: {}"
    LOG_SCH_RUST_DIR_SEARCH_ERROR = "[검색][오류] 검색 엔진(Rust) 디렉토리 검색 오류: {}"
    LOG_SCH_RUST_SMART_SCAN_ERROR = "[검색][오류] 검색 엔진(Rust) 스마트 스캔 오류: {}"
    LOG_SCH_RETRY_PYTHON = "[검색] 검색 엔진(Rust) 재시도: 검색 엔진(Python) 폴백 사용 ({})"
    LOG_SCH_ERROR_FILE = "[검색][오류] 파일 처리 실패 ({}): {}"

    LOG_SCH_ALL_FILES_GUIDE = "[검색] 모든 파일 검색 모드 (확장자 필터 무시)"
    LOG_SCH_FILENAME_FILTER_GUIDE = "[검색] 파일명 필터 적용: {}"
    LOG_SCH_SMART_SCAN_STARTED = "[검색] 스마트 스캔 시작 (검색어: '{}')"
    LOG_SCH_BINARY_CHECK_ERROR = "[검색][오류] 바이너리 체크 중 오류: {}"

    # --- [워커(Worker) 상세 로그] ---

    LOG_WKR_INIT = "[워커] 백그라운드 워커 초기화 중..."
    LOG_WKR_STARTED = "[워커] 작업 시작 (검색어: '{}')"
    LOG_WKR_RUNNING = "[워커] {}개 파일 검색 중..."
    LOG_WKR_PROGRESS = "[워커] 진행률: {}% 완료 ({}/{})"
    LOG_WKR_DONE = "[워커] 작업 완료 (일치 파일: {}개, 매칭 항목: {}개)"
    LOG_WKR_RUST_ACT = "[워커] 검색 엔진(Rust) 활성화 (대상: {}개 경로)"
    LOG_WKR_EXCEL_SCAN = "[워커] 엑셀 파일(xlsx, xlsm 등) 보완 검색 중..."

    LOG_WKR_BATCH_ERROR = "[워커][오류] 배치 처리 실패: {}"
    LOG_WKR_BATCH_RETRY = "[워커][디버그] 개별 검색 배치 실패 (재시도 가능): {}"

    LOG_SCH_RESTART_PENDING = "[검색] 재시작 요청이 감지되었습니다. 새 검색을 시작합니다."
    LOG_SCH_RESTART_SCHEDULED = "[검색] 상태({})에서 검색 요청됨. 재시작을 예약합니다."

    LOG_WKR_STOPPED = "[워커] 작업이 중단되었습니다."
    LOG_WKR_STOP_SIGNAL = "[워커] 중지 신호를 수신했습니다. 작업을 중단합니다."

    LOG_WKR_ERROR = "[워커][오류] 워커 실행 중 오류: {}"

    LOG_THREAD_FORCE_TERM = "[스레드][경고] 스레드가 제시간에 종료되지 않아 강제로 종료합니다."
    MSG_DECODE_ERROR = "[디코딩 오류]"

    MSG_RUST_LOAD_FAIL = "Rust 가속 엔진을 로드할 수 없어 Python 엔진으로 동작합니다. (성능 저하 가능성 있음)"
    ERR_CRITICAL_SYSTEM = "치명적인 시스템 오류: {}"
    ERR_BATCH_SEARCH_FAIL = "배치 검색 오류: {}"

    # --- [설정 및 리소스 로그] ---
    LOG_CFG_LOAD_FAIL = "[설정][경고] 설정 파일이 손상되었습니다. 기본값을 사용합니다: {}"
    LOG_CFG_READ_ERROR = "[설정][오류] 설정 파일을 읽는 중 오류 발생: {}"
    LOG_CFG_SAVE_FAIL = "[설정][오류] 설정 저장 실패: {}"
    LOG_CFG_SAVE_RETRY = "[설정][디버그] 설정 저장 시도 {}회 실패: {}. 재시도 중..."
    # --- [세션 관련 로그 (Session)] ---

    LOG_SES_SAVE_FAIL = "[세션][오류] 세션 '{}' 저장 실패: {}"
    LOG_SES_LOAD_FAIL = "[세션][오류] 세션 '{}' 불러오기 실패: {}"
    LOG_SES_DELETE_FAIL = "[세션][오류] 세션 '{}' 삭제 실패: {}"

    LOG_RES_NOT_FOUND = "[리소스][오류] 파일을 찾을 수 없음: {}"
    LOG_RES_DATA_DIR_FAIL = "[리소스][오류] 데이터 폴더를 열 수 없음: {}"

    LOG_RES_CLEANUP_START = "[리소스] SearchTab 자원 정리 시작..."
    LOG_RES_CLEANUP_ERROR = "[리소스][경고] SearchTab 정리 중 오류: {}"

    LOG_RES_LOG_CLEANUP_FAIL = "[리소스][오류] 로그 파일 정리 중 오류: {}"
    # --- [UI 및 상태바 메시지] ---

    STATUS_ERROR_PREFIX = "에러: "

    RESULT_SUMMARY_TEMPLATE = "{}개 파일, {}개 일치"
    SEARCH_FINISHED_MSG = "검색 완료: 총 {}개 파일에서 {}개 일치 내용을 찾았습니다. (제외: {}개) / 소요 시간: {:.2f}초"
    STATUS_SEARCHING = "검색 중"
    STATUS_SEARCH_COMPLETED = "검색 완료 (파일: {} / 매칭: {} / 무시: {} / 소요시간: {:.2f}초)"

    # --- [공통 에러/경고 메시지] ---
    ERROR_IO_GENERIC = "입출력 오류 발생: {}"

    ERROR_JSON_PARSE = "JSON 파싱 실패: {}"
    ERROR_EXPORT_FAIL = "내보내기 실패: {}"

    ERROR_OPEN_DIR_FAILED = "폴더를 열 수 없습니다: {}"
    ERROR_JSON_PARSE_EX = "JSON 파싱 예외 발생: {}"
    ERROR_XML_PARSE_EX = "XML 파싱 예외 발생: {}"
    ERROR_EXCEL_EXCEPTION = "Excel 처리 중 예외 발생: {}"
    ERROR_EXCEL_CALAMINE = "Excel 라이브러리(calamine) 오류: {}"
    ERROR_FILE_PROCESSING = "파일 처리 중 오류 발생: {}"
    ERROR_IO_DURING_SEARCH = "검색 중 입출력 오류 발생: {}"
    ERROR_PREVIEW = "미리보기를 생성할 수 없는 파일입니다."
    ERROR_STOP_SEARCH_TAB = "탭 종료 중 검색 중단 오류: {}"

    ERROR_EXCEL_ACCESS = "Excel 파일 접근 오류: {}"
    ERROR_EXCEL_SIGNATURE = "유효하지 않은 Excel 파일 시그니처"
    ERROR_EXCEL_PROCESS = "Excel 파일 처리 중 오류 발생: {}"
    ERROR_JSON_RECURSION = "JSON 재귀 깊이 제한 초과 (RecursionError)"
    ERROR_JSON_DEPTH_LIMIT = "JSON 재귀 깊이 제한 초과 (1000+)"
    ERROR_XML_PARSE = "XML 파싱 오류: {}"
    ERROR_UNKNOWN = "알 수 없는 오류"

    DEBUG_QTHREAD_WARNING_TITLE = "스레드 경고 (QThread)"

    # --- [기타 상수 (로그 외)] ---
    HEADER_COUNT = "일치"

    HEADER_FOLDER = "폴더"
    HEADER_FILE = "파일"
    HEADER_POSITION = "위치"
    HEADER_CONTENT = "내용"
    HEADER_JSON_KEY = "키 (Key)"
    HEADER_JSON_VALUE = "수치 (Value)"
    HEADER_XML_NAME = "이름 (Element/Attr)"
    HEADER_XML_VALUE = "내용 (Value)"
    HEADER_ARCHIVE_NAMESPACE = "네임스페이스"
    HEADER_ARCHIVE_KEY = "키 (Key)"
    HEADER_ARCHIVE_SOURCE = "Source Text"
    HEADER_ARCHIVE_TRANSLATION = "Translation Text"
    HEADER_EXCEL_POSITION = "위치 (Sheet!Cell)"
    HEADER_EXCEL_VALUE = "값 (Value)"

    # Export
    EXPORT_TEXT_HEADER = "--- StringFinder Search Results ---"
    EXPORT_TEXT_SEPARATOR = "-" * 80
    EXPORT_TEXT_LINE_PREFIX = "Line {}: "
    EXPORT_SUMMARY_PREFIX = "Summary: "

    # Other
    EXCEL_MATCH_DETAIL = "{} (Matches: {})"
    EXCEL_SHEET_TITLE = "Search Results"
    LOG_SCH_JSON_LIMIT = "[검색][경고] JSON 데이터 크기 제한 초과"
    LOG_SCH_JSON_RECURSION = "[검색][경고] JSON 재귀 깊이 제한 도달"

    LOG_SCH_SKIP_REASON = " [Skip] {} \n\t -> 원인: {}"

    LOG_SCH_SKIP_SIMPLE = " [Skip] {}"

    MSG_BINARY_MATCH = "[바이너리 파일 내 {}개 일치 항목 존재]"
