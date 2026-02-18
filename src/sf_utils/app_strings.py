class AppStrings:
    # =========================================================================
    # 1. Basic Info & Common UI
    # =========================================================================
    APP_TITLE = "StringFinder"
    SEARCH_LABEL = "문자열 검색"

    # Common Buttons
    ADD_BTN = "추가"  # (If needed, mostly specific ones used)
    ADD_TAB_BTN = "+"
    ADD_FOLDER_BTN = "폴더 추가"
    ADD_EXT_BTN = "추가"
    DELETE_BTN = "삭제"
    SELECT_ALL_BTN = "전체 선택"
    DESELECT_ALL_BTN = "전체 해제"
    BTN_CLOSE = "닫기"

    # Placeholders
    SEARCH_EDIT_PLACEHOLDER = "검색할 문자열 입력..."
    EXT_EDIT_PLACEHOLDER = "확장자 (예: txt)"
    FILENAME_EDIT_PLACEHOLDER = "파일명 필터 (예: npc, fo / 콤마 구분)..."
    FILENAME_LIST_PLACEHOLDER = "필터 단어 (예: npc)"

    # Search Controls
    SEARCH_BTN = "검색"
    SEARCH_BTN_STOP = "중지"
    SEARCH_BTN_STOPPING = "중지 중..."

    # Context Menu
    OPEN_FILE = "파일 열기"
    OPEN_FOLDER = "폴더 열기"
    COPY_PATH = "경로 복사 (Ctrl+C)"
    COPY_CONTENT = "내용 복사 (Ctrl+C)"

    # Labels
    FILENAME_FILTER_LABEL = "파일명 필터:"
    SELECT_FOLDER_TITLE = "폴더 선택"

    # Pagination
    PAGINATION_PREV = "이전"
    PAGINATION_NEXT = "다음"
    PAGINATION_PAGE = "페이지:"
    PAGINATION_OF = "/"
    PAGINATION_DISPLAY = "표시:"
    PAGINATION_SIZE_1000 = "1000"
    PAGINATION_SIZE_2000 = "2000"
    PAGINATION_SIZE_5000 = "5000"

    # Success/Info Titles
    SUCCESS_TITLE = "성공"
    INFO_TITLE = "알림"
    INFO_CLEAR_SUCCESS = "데이터가 성공적으로 삭제되었습니다."
    INFO_RESET_LAYOUT_DONE = "레이아웃이 초기화되었습니다."
    HISTORY_CLEAR_ALL = "--- 기록 삭제 ---"

    # =========================================================================
    # 2. Main Window & Menu
    # =========================================================================
    # Dock Titles
    DOCK_SEARCH_TITLE = "검색 설정"
    DOCK_FOLDER_TITLE = "폴더 목록"
    DOCK_EXT_TITLE = "확장자 목록"
    DOCK_FILENAME_TITLE = "파일명 필터"
    DOCK_RESULT_TITLE = "검색 결과"

    # Tray Icon
    TRAY_OPEN = "열기"
    TRAY_QUIT = "종료"

    # Menu Controls
    MENU_LOCK_LAYOUT = "레이아웃 잠금"
    MENU_RESET_LAYOUT = "레이아웃 초기화"

    # Tab Management
    TAB_RENAME_TITLE = "탭 이름 변경"
    TAB_RENAME_PROMPT = "새 탭 이름을 입력하세요:"
    TAB_CLOSE_MENU = "탭 닫기"
    SEARCH_TAB_DEFAULT_TITLE = "검색"
    SEARCH_TAB_TITLE_TEMPLATE = "{} {}"

    # =========================================================================
    # 3. Search Tab (Filters, Special Search)
    # =========================================================================
    SPECIAL_SEARCH_LABEL = "특수 검색:"
    SPECIAL_SEARCH_OFF = "끄기"
    SPECIAL_SEARCH_ITEMS = [
        "끄기",
        "XML (부분 일치)",
        "XML (정확히 일치)",
        "JSON (부분 일치)",
        "JSON (정확히 일치)",
        "Archive (부분 일치)",
        "Archive (정확히 일치)",
        "Excel (부분 일치)",
        "Excel (정확히 일치)",
    ]

    # =========================================================================
    # 4. Results UI (Table, Headers, Export)
    # =========================================================================
    TAB_RESULTS = "검색 결과"
    TAB_LOGS = "로그"

    # Empty/Filter States
    RESULT_EMPTY_MSG = "검색 결과가 없습니다."
    RESULT_EMPTY_NO_FOLDER = "검색할 폴더를 추가해주세요."
    RESULT_EMPTY_NO_MATCH = "'{}'에 대한 검색 결과가 없습니다. 다른 검색어를 입력해보세요."
    RESULT_FILTER_FILE_PLACEHOLDER = "파일 필터..."
    RESULT_FILTER_FOLDER_PLACEHOLDER = "폴더 필터..."

    # Match Filters
    MATCH_FILTER_CONTENT_PLACEHOLDER = "내용 필터..."
    MATCH_FILTER_NAME_PLACEHOLDER = "이름 필터..."
    MATCH_FILTER_KEY_PLACEHOLDER = "키 필터..."
    MATCH_FILTER_VALUE_PLACEHOLDER = "값 필터..."
    MATCH_FILTER_ARCHIVE_NS_PLACEHOLDER = "네임스페이스 필터..."
    MATCH_FILTER_ARCHIVE_SOURCE_PLACEHOLDER = "소스 필터..."
    MATCH_FILTER_ARCHIVE_TRANS_PLACEHOLDER = "번역 필터..."
    MATCH_FILTER_EXCEL_POS_PLACEHOLDER = "위치 필터..."
    MATCH_FILTER_EXCEL_VAL_PLACEHOLDER = "값 필터..."

    # Headers
    HEADER_COUNT = "개수"
    HEADER_FOLDER = "폴더"
    HEADER_FILE = "파일"
    HEADER_POSITION = "위치"
    HEADER_CONTENT = "내용"
    HEADER_JSON_KEY = "키"
    HEADER_JSON_VALUE = "값"
    HEADER_XML_NAME = "이름"
    HEADER_XML_VALUE = "값"
    HEADER_ARCHIVE_NAMESPACE = "네임스페이스"
    HEADER_ARCHIVE_KEY = "키"
    HEADER_ARCHIVE_SOURCE = "소스"
    HEADER_ARCHIVE_TRANSLATION = "번역"
    HEADER_EXCEL_POSITION = "위치"
    HEADER_EXCEL_VALUE = "값"

    # Export
    RESULT_EXPORT_TITLE = "결과 내보내기"
    RESULT_EXPORT_FILTER = "엑셀 파일 (*.xlsx);;텍스트 파일 (*.txt)"
    RESULT_EXPORT_ALL = "전체 내보내기"
    EXPORT_TEXT_HEADER = "--- StringFinder 검색 결과 ---"
    EXPORT_TEXT_SEPARATOR = "-" * 80
    EXPORT_TEXT_LINE_PREFIX = "라인 {}: "
    EXPORT_SUMMARY_PREFIX = "요약: "

    # Preview & Details
    RESULT_PREVIEW_TITLE = "미리보기"
    RESULT_PREVIEW_ERROR = "이 파일을 미리볼 수 없습니다."
    MSG_BINARY_FILE = "[이진 파일]"
    MSG_BINARY_MATCH = "[이진 파일에서 {}개 항목 발견]"
    SKIP_EMPTY_FILE = "빈 파일"
    EXCEL_MATCH_DETAIL = "{} (매치: {})"
    EXCEL_SHEET_TITLE = "검색 결과"

    # Search Status Messages
    STATUS_READY = "준비"
    STATUS_SEARCHING = "검색 중..."
    STATUS_SEARCH_COMPLETED = "완료 (파일: {} / 매치: {} / 무시됨: {} / {:.2f}초)"
    STATUS_ERROR_PREFIX = "오류: "
    RESULT_SUMMARY_TEMPLATE = "{}개 파일, {}개 매치"
    SEARCH_FINISHED_MSG = "검색 완료: {}개 파일, {}개 매치 (무시됨: {} / {:.2f}초)"

    # =========================================================================
    # 5. Settings Dialog
    # =========================================================================
    SETTINGS_TITLE = "설정"

    # Groups
    SETTINGS_GROUP_APPEARANCE = "화면"
    SETTINGS_GROUP_OPERATION = "동작"
    SETTINGS_GROUP_LOG = "로그"
    SETTINGS_GROUP_PERFORMANCE = "성능"
    SETTINGS_GROUP_DATA = "데이터"

    # Appearance
    THEME_LABEL = "테마:"
    THEME_DARK = "다크"
    THEME_LIGHT = "라이트"

    # Operation
    STARTUP_LABEL = "시작 시 실행:"
    STARTUP_ENABLE = "사용"
    STARTUP_DISABLE = "사용 안 함"
    CLOSE_BEHAVIOR_LABEL = "닫기 버튼 동작:"
    CLOSE_QUIT = "종료"
    CLOSE_TRAY = "트레이로 최소화"
    HOTKEY_LABEL = "전역 단축키:"
    HOTKEY_EDIT_PLACEHOLDER = "클릭 후 키 입력"
    HOTKEY_RECORDING = "입력 중..."

    # Log Management
    LOG_RETENTION_LABEL = "로그 보관:"
    BTN_DELETE_ALL_LOGS = "모든 로그 삭제"
    MSG_DELETE_LOGS_CONFIRM = "모든 로그 파일을 삭제하시겠습니까?"
    INFO_LOGS_DELETED = "로그가 삭제되었습니다."
    MAX_FILES_LABEL = "최대 파일 수:"
    MAX_DAYS_LABEL = "최대 보관 일수:"

    # Data Management
    OPEN_DATA_DIR_BTN = "데이터 폴더 열기"
    CLEAR_ALL_DATA_BTN = "모든 데이터 삭제"
    CLEAR_ALL_DATA_CONFIRM = "모든 설정과 기록을 삭제하시겠습니까?"

    # Common Combo States
    COMBO_ENABLE = "사용"
    COMBO_DISABLE = "사용 안 함"
    COMBO_LOCKED = "잠금"
    COMBO_UNLOCKED = "해제"
    MSG_ENABLED = "사용"
    MSG_DISABLED = "사용 안 함"

    # Cache Settings
    CACHE_ENABLE_LABEL = "캐시 사용:"
    CACHE_ENABLE_DESC = "검색 결과를 캐싱하여 성능을 향상시킵니다. 디스크 공간 사용에 주의하세요."
    CACHE_CLEAR_BTN = "캐시 삭제"
    MSG_CACHE_CLEAR_CONFIRM = (
        "모든 검색 캐시를 삭제하시겠습니까?\n\n검색 기능에는 영향을 주지 않으며, 다음 검색 시 다시 생성됩니다."
    )
    INFO_CACHE_CLEARED = "캐시가 삭제되었습니다."

    # =========================================================================
    # 6. System & Config Logs
    # =========================================================================
    # System Lifecycle
    LOG_SYS_APP_STARTED_DECO = "--- 앱 시작 ---"
    LOG_SYS_SINGLE_INSTANCE_DETECTED = "[System] 이미 실행 중인 인스턴스가 발견되었습니다. 기존 창을 활성화합니다."
    LOG_SYS_SHUTDOWN = "[시스템] 종료 중..."
    LOG_SYS_LOG_CLEANUP_DONE = "[시스템] 로그 파일 {}개 정리 완료."
    LOG_SYS_RUST_SUCCESS = "[시스템] Rust 엔진 로드됨."
    LOG_SYS_RUST_FALLBACK = "[시스템] Rust 엔진을 찾을 수 없음. Python 엔진으로 전환."
    LOG_SYS_TRAY_UNAVAILABLE = "[시스템] 트레이 아이콘을 사용할 수 없음."
    LOG_SYS_TRAY_DISABLED_TEST = "[테스트] 안정성을 위해 시스템 트레이가 비활성화되었습니다."
    LOG_SYS_HOTKEY_ERROR = "[시스템] 단축키 오류: {}"
    LOG_SYS_WKR_SHUTDOWN = "[시스템] 워커 프로세스 종료 중..."
    LOG_SYS_WKR_SHUTDOWN_DONE = "[시스템] 워커 프로세스 종료 완료"
    LOG_SYS_WKR_SHUTDOWN_FAIL = "[시스템] 워커 프로세스 종료 실패: {}"
    LOG_SYS_RE_COMPILE_ERROR = "[시스템] 정규식 컴파일 오류 ({}): {}"
    LOG_SYS_REGISTRY_ERROR = "[시스템] 레지스트리 오류: {}"
    LOG_SYS_SF_ENGINE_NOT_FOUND = "[시스템] 경고: Rust 엔진 라이브러리를 찾을 수 없음: {}"
    LOG_SYS_LOG_DIR_TEMP = "[시스템] 경고: AppData에 로그 디렉토리를 생성하지 못해 임시 폴더를 사용합니다: {}"
    LOG_SYS_UNCAUGHT_EXCEPTION = "처리되지 않은 예외 발생"
    LOG_SYS_SCAN_WORKER_BROKEN = "[시스템] 스캔 워커가 손상되었습니다 (None)."
    LOG_SYS_QT_THREAD_WARNING = "[Qt 경고] QThread 경고 감지: {}\n스택 트레이스:\n{}"
    LOG_SYS_REGISTER_HOTKEY_FAIL = "[시스템] 단축키 등록 실패: {}"
    LOG_SYS_HOTKEY_NOT_FOUND = "[시스템] 단축키를 찾을 수 없음: {}"
    LOG_SYS_DATA_DIR_CHECK_FAIL = "[시스템] 데이터 디렉토리 확인 실패 (권한 등): {}"
    LOG_SYS_CLI_ARGS_PARSE_FAIL = "[시스템] 명령줄 인수 파싱 실패 (권한 등): {}"
    LOG_SYS_CLEANUP_SKIP = "[시스템] 로그 정리 건너뜀 ({}): {}"
    LOG_SYS_CLEANUP_ERROR = "[시스템] 종료 정리 중 오류: {}"

    # Config Logs
    LOG_CFG_LOAD_FAIL = "[설정] 로드 실패, 기본값 사용: {}"
    LOG_CFG_READ_ERROR = "[설정] 읽기 오류: {}"
    LOG_CFG_SAVE_FAIL = "[설정] 저장 실패: {}"
    LOG_CFG_SAVE_RETRY = "[설정] 저장 재시도 {} 실패: {}"
    LOG_CFG_MIGRATE_WARN = (
        "[설정] 설정 파일 버전이 현재 앱 버전보다 높습니다 (파일: v{}, 앱: v{}). 일부 설정이 유실될 수 있습니다."
    )
    LOG_CFG_DELETE_FAIL_MSG = "[설정] 설정 파일 삭제 실패: {}"
    LOG_CFG_LOG_CLEANUP_DONE_MSG = "[설정] 로그 파일 {}개 삭제 완료"
    LOG_CFG_INVALID_VER = "[설정] 구성 파일 버전이 올바르지 않습니다. 기본값을 사용합니다."
    LOG_CFG_MIGRATION = "[설정] 구성 마이그레이션: v{} -> v{}"
    LOG_CFG_LOAD_ERROR_MSG = "[설정] 구성 파일 로드 오류: {}"
    LOG_CFG_CACHE_STATUS = "[설정] 검색 캐시: {}"
    LOG_CFG_CACHE_CLEARED = "[설정] 캐시가 삭제되었습니다."
    LOG_CFG_CACHE_CLEAR_FAIL = "[설정] 캐시 삭제 실패: {}"

    # Session Logs
    LOG_SES_SAVE_FAIL = "[세션] 저장 실패 '{}': {}"
    LOG_SES_LOAD_FAIL = "[세션] 로드 실패 '{}': {}"
    LOG_SES_DELETE_FAIL = "[세션] 삭제 실패 '{}': {}"
    LOG_SES_LIST_FAIL = "[세션] 목록 조회 실패: {}"

    # Resource Logs
    LOG_RES_NOT_FOUND = "[리소스] 찾을 수 없음: {}"
    LOG_RES_DATA_DIR_FAIL = "[리소스] 데이터 디렉토리 오류: {}"
    LOG_RES_CLEANUP_START = "[리소스] 정리 시작..."
    LOG_RES_CLEANUP_ERROR = "[리소스] 정리 오류: {}"
    LOG_RES_LOG_CLEANUP_FAIL = "[리소스] 로그 정리 실패: {}"

    # Messages
    MSG_RUST_LOAD_FAIL = "[시스템] Rust 엔진 로드 실패."
    MSG_DECODE_ERROR = "[디코딩 오류]"
    MSG_QT_THREAD_DESTROYED = "스레드가 종료되기 전에 파괴되었습니다."
    MSG_QT_RESOURCE_LEAK = "프로그램 종료 시 리소스가 정상적으로 해제되지 않았을 수 있습니다."

    # =========================================================================
    # 7. Search & Worker Logs
    # =========================================================================
    # Search Process
    LOG_SCH_STARTED = "[검색] 시작됨"
    LOG_SCH_COND_QUERY = "[검색] 검색어: '{}'"
    LOG_SCH_COND_FOLDER = "[검색] 폴더 목록: '{}'"
    LOG_SCH_COND_FILTER = "[검색] 파일명 필터: {}"
    LOG_SCH_COND_SPECIAL = "[검색] 특수 모드: {}"
    LOG_SCH_COND_EXT = "[검색] 확장자 목록: '{}'"
    LOG_SCH_PATH_ENTER = "[검색] 경로 진입"
    LOG_SCH_SCAN_DONE = "[검색] 스캔 완료: {}개 파일 ({:.3f}초)"
    LOG_SCH_SCAN_DONE_SIMPLE = "[검색] 스캔 완료 ({:.3f}초)"
    LOG_SCH_SCAN_DONE_FAST_PATH = "[검색] 빠른 경로(Fast Path) 스캔 완료"
    LOG_SCH_SEARCH_DONE = "[검색] 검색 완료 ({:.3f}초)"
    LOG_SCH_NO_FILES = "[검색] 검색할 파일이 없습니다."
    LOG_SCH_EMPTY_QUERY = "[검색] 검색어가 비어 있습니다."

    # Search Modes
    LOG_SCH_RUST_MODE = "[검색] Rust 모드"
    LOG_SCH_PYTHON_MODE = "[검색] Python 모드"
    LOG_SCH_HYBRID_MODE = "[검색] 하이브리드 모드 (Rust 스캔 + Python 검색)"
    ENGINE_STATUS_RUST = "[Rust]"
    ENGINE_STATUS_PYTHON = "[Python]"

    # Control
    LOG_SCH_STOP_REQUESTED = "[검색] 중지 요청됨..."
    LOG_SCH_STOPPED_BY_USER = "[검색] 사용자에 의해 중지됨."
    LOG_SCH_RESTART_PENDING = "[검색] 재시작 대기 중..."
    LOG_SCH_RESTART_SCHEDULED = "[검색] 재시작 예약됨..."

    # Skips & Errors (Non-Critical)
    LOG_SCH_SKIP_COUNT = "[검색] {}개 파일 건너뜀."
    LOG_SCH_SKIP_REASON = "[건너뜀] {} \n\t -> 사유: {}"
    LOG_SCH_SKIP_SIMPLE = "[건너뜀] {}"
    LOG_SCH_BINARY_CHECK_FAIL = "[검색] 바이너리 확인 실패 ({}): {}"
    LOG_SCH_PARSE_ERROR = "[검색] 파싱 오류 {} ({}): {}"
    LOG_SCH_UNEXPECTED_ERROR = "[검색] 예상치 못한 오류 {} ({}): {}"
    LOG_SCH_ERROR = "[검색] 오류: {}"
    LOG_SCH_RUST_ENGINE_ERROR = "[검색] Rust 오류: {}"
    LOG_SCH_RUST_DIR_SEARCH_ERROR = "[검색] Rust 디렉토리 검색 오류: {}"
    LOG_SCH_RUST_SMART_SCAN_ERROR = "[검색] Rust 스마트 스캔 오류: {}"
    LOG_SCH_RETRY_PYTHON = "[검색] Python으로 재시도 ({})"
    LOG_SCH_ERROR_FILE = "[검색] 파일 오류 ({}): {}"
    LOG_SCH_ALL_FILES_GUIDE = "[검색] 전체 파일 모드"
    LOG_SCH_FILENAME_FILTER_GUIDE = "[검색] 파일명 필터: {}"
    LOG_SCH_SMART_SCAN_STARTED = "[검색] 스마트 스캔 시작 ('{}')"
    LOG_SCH_BINARY_CHECK_ERROR = "[검색] 바이너리 확인 오류: {}"
    LOG_SCH_JSON_LIMIT = "[검색] JSON 깊이 제한 도달"
    LOG_SCH_JSON_RECURSION = "[검색] JSON 재귀 오류 발생"
    LOG_SCH_ENCODING_ERROR = "[검색] 인코딩 감지 오류 ({}): {}"
    LOG_SCH_STREAM_ERROR = "[검색] 대형 파일 스트리밍 오류: {}"
    LOG_SCH_MMAP_FAILED = "[검색] Mmap 미리보기 실패: {}"
    LOG_SCH_STOP_THREAD_ERR = "[검색] 스레드 중지 오류: {}"

    # Worker
    LOG_WKR_INIT = "[워커] 초기화..."
    LOG_WKR_STARTED = "[워커] 시작됨 ('{}')"
    LOG_WKR_RUNNING = "[워커] 실행 중 {}개 파일..."
    LOG_WKR_PROGRESS = "[워커] 진행률 {}% ({}/{})"
    LOG_WKR_DONE = "[워커] 완료 (발견: {}, 매치: {})"
    LOG_WKR_RUST_ACT = "[워커] Rust 활성화 ({}개 경로)"
    LOG_WKR_EXCEL_SCAN = "[워커] 엑셀 스캔..."
    LOG_WKR_BATCH_ERROR = "[워커] 배치 오류: {}"
    LOG_WKR_BATCH_RETRY = "[워커] 재시도: {}"
    LOG_WKR_STOPPED = "[워커] 중지됨."
    LOG_WKR_STOP_SIGNAL = "[워커] 중지 신호."
    LOG_WKR_ERROR = "[워커] 오류: {}"
    LOG_WKR_HANG_DETECTED = "[성능] 워커 지연 감지 (경과: {:.2f}초). 복구 중..."

    # =========================================================================
    # 8. Cache & Performance Logs
    # =========================================================================
    # Process Manager
    LOG_PERF_MANAGER_INIT = "[성능] 전역 멀티프로세싱 매니저 생성 완료"
    LOG_PERF_MANAGER_FAIL = "[성능] 매니저 생성 실패: {}"
    LOG_PERF_MANAGER_SHUTDOWN = "[성능] 전역 멀티프로세싱 매니저 종료 완료"
    LOG_EXECUTOR_SHUTDOWN_ERROR = "[성능] 실행기(Executor) 종료 오류: {}"
    LOG_CLEANUP_CHILDREN = "[성능] {}개의 자식 프로세스 정리 중..."
    LOG_ZOMBIE_CLEANUP_ERROR = "[성능] 좀비 프로세스 정리 중 오류: {}"
    LOG_EXECUTOR_STOPPING = "[성능] 실행기(Executor) 중지 중..."
    LOG_EXECUTOR_FORCE_STOP = "[성능] 중지 요청에 의해 실행기 강제 종료 중..."
    LOG_WORKER_ERROR_SIGNAL_FAIL = "[워커] 오류 신호 전송 실패"
    LOG_WORKER_FINISH_SIGNAL_FAIL = "[워커] 완료 신호 전송 실패"
    LOG_THREAD_FORCE_TERM = "[스레드] 강제 종료됨."

    # Cache Internals
    LOG_CACHE_INIT_COMPLETE = "[캐시] 초기화 완료"
    LOG_CACHE_INIT_FAIL = "[캐시] 초기화 실패: {}"
    LOG_CACHE_KEY = "[캐시] 키: {}..."
    LOG_CACHE_KEY_FAIL = "[캐시] 키 생성 실패: {}"
    LOG_CACHE_FOUND = "[캐시] {}개 결과 발견"
    LOG_CACHE_HIT = "[캐시] 적중 - {}개 결과 반환"
    LOG_CACHE_FILE_CHANGED = "[캐시] 파일 변경됨 - 재검색"
    LOG_CACHE_MISS = "[캐시] 미적중"
    LOG_CACHE_CHECK_FAIL = "[캐시] 확인 실패: {}"
    LOG_CACHE_SYNC_START = "[캐시] 결과 동기화 시작: {}개 항목"
    LOG_CACHE_LRU_UPDATED = "[캐시] LRU 업데이트됨"
    LOG_CACHE_FILE_FAIL = "[캐시] 파일 캐시 실패: {}"
    LOG_CACHE_FILE_ITEMS = "[캐시] 파일 캐시 {}개 항목"
    LOG_CACHE_RUNTIME_ERROR = "[캐시] 동기화 중 런타임 오류"
    LOG_CACHE_SYNC_FAIL = "[캐시] 동기화 실패: {}"
    LOG_CACHE_NO_KEY = "[캐시] 키 없음"
    LOG_CACHE_NO_RESULTS = "[캐시] 캐시할 결과 없음"
    LOG_CACHE_RUNTIME_ERROR_SIMPLE = "[캐시] 런타임 오류"
    LOG_CACHE_HIT_RATE = "[캐시] 캐시 히트율 계산"
    LOG_CACHE_FILE_SEARCH_FAIL = "[캐시] 파일 검색 실패: {}, {}"
    LOG_CACHE_INC_DONE = "[캐시] 증분 검색 완료: 재스캔={}, 캐시={}"
    LOG_CACHE_LOCK_FAIL = "[캐시] 락을 획득하지 못하여 저장을 건너뜁니다."
    LOG_CACHE_SAVE_DONE = "[캐시] 디스크 저장 완료 (Atomic & Locked)"
    LOG_CACHE_SAVE_FAIL = "[캐시] 디스크 저장 실패: {}"
    LOG_CACHE_LOAD_DONE = "[캐시] 디스크 로드 완료"
    LOG_CACHE_LOAD_FAIL = "[캐시] 디스크 로드 실패: {}"
    LOG_CACHE_DELETE_DONE = "[캐시] 캐시 삭제 완료"
    LOG_CACHE_DELETE_FAIL = "[캐시] 캐시 삭제 실패: {}"
    LOG_CACHE_KEY_GEN = "[캐시] 키 생성: {}, 엔트리 추가 여부: {}"
    LOG_CACHE_SCAN_DIR_CHANGE = "[캐시] 검색 경로(폴더) 변경 감지 - 재검색 수행"
    LOG_CACHE_STORAGE_DIR = "[캐시] 저장 경로 확인: {}"
    LOG_CACHE_STORAGE_DONE_MSG = "[캐시] 디스크 저장 완료"
    LOG_CACHE_SYNC_REPORT = "[캐시] {}개 항목이 캐시되었습니다."

    # =========================================================================
    # 9. Error Messages
    # =========================================================================
    # Titles & Critical
    ERROR_TITLE = "오류"
    TITLE_QT_WARNING = "QThread Warning"
    TITLE_CRITICAL_ERROR = "StringFinder Error"
    ERROR_UNKNOWN = "알 수 없는 오류"
    MSG_CRITICAL_ERROR_POPUP = "치명적인 오류가 발생했습니다:\n\n{}\n\n상세 내용은 crash_dump.txt를 확인하세요."

    # Common Errors
    ERROR_IO_GENERIC = "[검색] IO 오류: {}"
    ERROR_UNEXPECTED_FILE = "[검색] 파일 '{}' 처리 중 예기치 못한 오류 발생: {}"
    ERROR_JSON_PARSE = "[검색] JSON 파싱 오류: {}"
    ERROR_EXPORT_FAIL = "[시스템] 내보내기 실패: {}"
    ERROR_OPEN_DIR_FAILED = "[시스템] 디렉토리 열기 실패: {}"

    # Exceptions
    ERROR_JSON_PARSE_EX = "[검색] JSON 예외: {}"
    ERROR_XML_PARSE_EX = "[검색] XML 예외: {}"
    ERROR_EXCEL_EXCEPTION = "[검색] Excel 예외: {}"
    ERROR_EXCEL_CALAMINE = "[검색] Calamine 오류: {}"

    # Processing Errors
    ERROR_FILE_PROCESSING = "[검색] 파일 처리 오류: {}"
    ERROR_IO_DURING_SEARCH = "[검색] 검색 중 IO 오류: {}"
    ERROR_PREVIEW = "미리보기 없음"
    ERROR_STOP_SEARCH_TAB = "[검색] 중지 오류: {}"

    # Excel specific
    ERROR_EXCEL_ACCESS = "[검색] Excel 접근 오류: {}"
    ERROR_EXCEL_SIGNATURE = "[검색] 유효하지 않은 Excel 서명"
    ERROR_EXCEL_PROCESS = "[검색] Excel 처리 오류: {}"
    ERROR_EXCEL_CALAMINE_REQ = "[검색] Excel 검색을 위해 python-calamine 라이브러리가 필요합니다."
    ERROR_EXCEL_LIB = "[검색] Excel 라이브러리 오류: {}"
    ERROR_SEARCH_EXCEL = "[검색] Excel 검색 오류 {}: {}"
    ERROR_EXCEL_PANIC = "[검색] 치명적인 Excel 오류 (Panic): {}"

    # Recursion & Limits
    ERROR_JSON_RECURSION = "[검색] JSON 재귀 오류"
    ERROR_JSON_DEPTH_LIMIT = "[검색] JSON 깊이 제한 초과"

    # XML/File
    ERROR_XML_PARSE = "[검색] XML 파싱 오류: {}"
    ERROR_FILE_ACCESS_BINARY = "[검색] 파일 접근 오류 (바이너리 체크): {}"
    ERROR_READ_FILE = "[검색] 파일 읽기 오류 {}: {}"

    # Search Critical
    ERROR_SEARCH_CRITICAL_TITLE = "검색 오류"
    ERROR_SEARCH_CRITICAL_MSG = "검색 중 치명적인 오류가 발생했습니다.\n로그를 확인해주세요.\n\n오류: {}"
    ERROR_SEARCH_START_FAIL = "시작 실패:\n{}\n\n{}"
    ERROR_PROCESS_CRITICAL = "[워커] 치명적인 처리 오류: {}"
    ERROR_SEARCH_UNKNOWN = "[검색] 알 수 없는 검색 오류: {}"
    ERROR_WKR_HANG_RECOVERY = "작업이 지연되어 실행기를 재생성했습니다."

    # Failures
    ERR_CRITICAL_SYSTEM = "[시스템] 치명적인 시스템 오류: {}"
    ERR_BATCH_SEARCH_FAIL = "[워커] 배치 검색 실패: {}"
    ERR_ACTION_FAILED = "실패: {}"

    # Validation
    ERROR_SESSION_SAVE = "[세션] 저장 실패: {}"
    ERROR_NO_SELECTION = "최소 하나의 폴더와 확장자를 선택해주세요."

    # Qt Specific
    LOG_QT_WARNING = "[Qt 경고] {}"
    LOG_QT_CRITICAL = "[Qt 치명적 오류] {}"
    LOG_QT_FATAL = "[Qt 치명적 오류] {}"
