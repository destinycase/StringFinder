from sf_utils.app_strings import AppStrings


class Constants:
    """애플리케이션 전반에서 사용되는 설정 키, 임계값, 상수 값들을 정의합니다."""
    APP_NAME = "StringFinder"
    APP_VERSION = AppStrings.APP_VERSION  # 애플리케이션 버전 정보를 통합 관리합니다.
    ENV_APPDATA = "APPDATA"
    APPDATA_FALLBACK_DIR = ".stringfinder"
    APPDATA_TEMP_DIR = "StringFinder_Temp"
    CONFIG_FILENAME = "config.json"
    SESSIONS_DIRNAME = "sessions"
    JSON_EXTENSION = ".json"
    LOG_FILE_GLOB = "*.log"
    TEMP_FILE_SUFFIX = ".tmp"
    BACKUP_FILE_SUFFIX = ".old"
    # 하드코딩되었던 상수들은 고급 설정의 '기본값'으로 사용되며, 
    # UI 편집용 수치(MB 단위)와 내부 바이트 계산용 수치로 적절히 분리됩니다.
    DEFAULT_MAX_JSON_DOM_SIZE_MB = 500
    DEFAULT_MAX_SMALL_FILE_SIZE_MB = 10
    DEFAULT_JSON_MMAP_THRESHOLD_MB = 5
    DEFAULT_MAX_TOTAL_MATCHES = 500_000
    DEFAULT_MAX_PER_FILE_MATCHES = 5_000
    DEFAULT_TIMEOUT_WORKER_HANG = 600
    DEFAULT_MAX_CHECK_CELLS = 500_000
    DEFAULT_MAX_JSON_DEPTH = 20_000

    # 유지보수 호환상 기존 상수를 기본값 기반 동적 연산 프로퍼티가 아닌 형태로 남길 경우:
    MAX_JSON_DOM_SIZE = DEFAULT_MAX_JSON_DOM_SIZE_MB * 1024 * 1024
    MAX_SMALL_FILE_SIZE = DEFAULT_MAX_SMALL_FILE_SIZE_MB * 1024 * 1024
    JSON_MMAP_THRESHOLD = DEFAULT_JSON_MMAP_THRESHOLD_MB * 1024 * 1024
    
    TYPE_SEARCH = "search"
    TYPE_FILENAME = "filename"
    VIEW_RESULT = "result"
    VIEW_MATCH = "match"
    ENC_UTF8 = "utf-8"
    ENC_UTF8_SIG = "utf-8-sig"
    ENC_UTF16 = "utf-16"
    ENC_UTF16_LE = "utf-16-le"
    ENC_UTF16_BE = "utf-16-be"
    ENC_EUCKR = "euc-kr"
    ENC_CP949 = "cp949"
    MODE_XML = "XML"
    MODE_JSON = "JSON"
    MODE_ARCHIVE = "Archive"
    MODE_EXCEL = "Excel"
    MODE_EXACT = AppStrings.SEARCH_MODE_EXACT
    MODE_COMPLEX = AppStrings.SEARCH_MODE_COMPLEX
    MODE_NORMAL = "Normal"
    EXT_XML = ".xml"
    EXT_JSON = ".json"
    EXT_ARCHIVE = ".archive"
    EXT_EXCEL = ("xlsx", "xlsm", "xls", "xlsb")
    DEFAULT_THEME = "Dark"
    RUST_MODE_NORMAL = 0
    RUST_MODE_JSON = 1 << 0
    RUST_MODE_XML = 1 << 1
    RUST_MODE_ARCHIVE = 1 << 2
    RUST_MODE_EXACT = 1 << 3
    RUST_MODE_EXCEL = 1 << 4
    RUST_MODE_EXCLUDE_BINARY = 1 << 5
    RUST_MODE_EXISTENCE_ONLY = 1 << 6
    SYMBOL_CLOSE = "×"
    YIELD_SLEEP_TIME = 0.001
    HISTORY_ACTION_CLEAR = "action_clear"
    STATUS_SKIPPED = "SKIPPED"
    PAYLOAD_INPUTS = "inputs"
    PAYLOAD_RESULTS = "results"
    PAYLOAD_LOGS = "logs"
    PAYLOAD_TIMESTAMP = "timestamp"
    PAYLOAD_SUMMARY = "summary"
    STATE_KEY_SEARCH = "search"
    STATE_KEY_FILENAME = "filename"
    PAYLOAD_SEARCH_STRING = "search_string"
    PAYLOAD_SEARCH_PATHS = "search_paths"
    PAYLOAD_FILE_LIST = "file_list"
    PAYLOAD_SPECIAL_MODE = "special_mode"
    PAYLOAD_EXTENSIONS = "extensions"
    PAYLOAD_SKIPPED = "skipped"
    PAYLOAD_FILENAME_FILTER = "filename_filter"
    PAYLOAD_USE_COMPLEX_SEARCH = "use_complex_search"
    PAYLOAD_EXISTENCE_ONLY = "existence_only"
    PAYLOAD_EXCLUDE_HIDDEN = "exclude_hidden"
    PAYLOAD_EXCLUDE_BINARY = "exclude_binary"
    CONFIG_KEY_VERSION = "config_version"
    CONFIG_KEY_FILTERS = "filters"
    CONFIG_KEY_FOLDERS = "folders"
    CONFIG_KEY_EXTENSIONS = "extensions"
    CONFIG_KEY_FILENAMES = "filenames"
    CONFIG_KEY_HISTORY = "history"
    CONFIG_KEY_FILENAME_HISTORY = "filename_history"
    CONFIG_KEY_GEOMETRY = "geometry"
    CONFIG_KEY_WINDOW_STATE = "windowState"
    CONFIG_KEY_MAIN_SPLITTER_STATE = "main_splitter_state"
    CONFIG_KEY_RESULT_SPLITTER_STATE = "result_splitter_state"
    CONFIG_KEY_FILTER_SPLITTER_STATE = "filter_splitter_state"
    CONFIG_KEY_THEME = "theme"
    CONFIG_KEY_CASE_INSENSITIVE = "case_insensitive"
    CONFIG_KEY_EXCLUDE_BINARY = "exclude_binary"
    CONFIG_KEY_RESULT_COLUMN_WIDTHS = "result_column_widths"
    CONFIG_KEY_MATCH_COLUMN_WIDTHS = "match_column_widths"
    CONFIG_KEY_LOG_RETENTION = "log_retention"
    CONFIG_KEY_LOG_RETENTION_ENABLED = "enabled"
    CONFIG_KEY_LOG_RETENTION_MAX_FILES = "max_files"
    CONFIG_KEY_LOG_RETENTION_MAX_DAYS = "max_days"
    CONFIG_KEY_DOCK_LAYOUT_STATE = "dock_layout_state"
    CONFIG_KEY_LOCK_DOCK_LAYOUT = "lock_dock_layout"
    CONFIG_KEY_TABS = "tabs"
    OBJ_NAME_SEARCH_DOCK = "SearchDock"
    OBJ_NAME_FOLDER_DOCK = "FolderDock"
    OBJ_NAME_EXT_DOCK = "ExtDock"
    OBJ_NAME_FILENAME_DOCK = "FilenameDock"
    
    # 고급 설정용 키
    CONFIG_KEY_ADVANCED = "advanced"
    CONFIG_KEY_MAX_TOTAL_MATCHES = "max_total_matches"
    CONFIG_KEY_MAX_PER_FILE_MATCHES = "max_per_file_matches"
    CONFIG_KEY_MAX_JSON_DOM_SIZE = "max_json_dom_size"
    CONFIG_KEY_MAX_SMALL_FILE_SIZE = "max_small_file_size"
    CONFIG_KEY_JSON_MMAP_THRESHOLD = "json_mmap_threshold"
    CONFIG_KEY_TIMEOUT_WORKER_HANG = "timeout_worker_hang"
    CONFIG_KEY_MAX_CHECK_CELLS = "max_check_cells"
    CONFIG_KEY_MAX_JSON_DEPTH = "max_json_depth"

    ADAPTIVE_BATCH_SIZE_THRESHOLD = 100 * 1024 * 1024  # 100MB (적응형 배치 임계치)
    COLOR_RED = "#FF5555"
    BATCH_SIZE_LARGE = 500
    BATCH_SIZE_NORMAL = 100
    TIMEOUT_WORKER_HANG = DEFAULT_TIMEOUT_WORKER_HANG
    MAX_TOTAL_MATCHES = DEFAULT_MAX_TOTAL_MATCHES  # 글로벌 매치 상한 (기본값 캐시)
    MAX_PER_FILE_MATCHES = DEFAULT_MAX_PER_FILE_MATCHES  # 파일당 매치 상한 (기본값 캐시)
    MEMORY_THRESHOLD_PERCENT = 85  # 메모리 사용량 임계값 (%)

    # Rust 엔진 결과 배치 크기 및 지연 간격 (성능 튜닝용)
    RUST_RESULT_BATCH_SIZE = 128
    RUST_RESULT_FLUSH_MS = 75

    class SearchState:
        IDLE = "IDLE"
        SCANNING = "SCANNING"
        SEARCHING = "SEARCHING"
        STOPPING = "STOPPING"
