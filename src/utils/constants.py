class Constants:
    # 앱 메타데이터
    APP_NAME = "StringFinder"
    APP_VERSION = "dev"

    # 빌드 시 build.py에 의해 생성되는 _version.py에서 실제 버전을 시도함
    try:
        from . import _version

        APP_VERSION = _version.VERSION
    except (ImportError, AttributeError):
        pass

    # 데이터 타입 식별자
    TYPE_SEARCH = "search"
    TYPE_FILENAME = "filename"
    TYPE_FOLDER = "folder"
    TYPE_EXT = "ext"
    TYPE_FILENAME_LIST = "filename_list"

    # 뷰 식별자
    VIEW_RESULT = "result"
    VIEW_MATCH = "match"

    # 인코딩 목록
    ENC_UTF8 = "utf-8"
    ENC_UTF8_SIG = "utf-8-sig"
    ENC_UTF16 = "utf-16"
    ENC_UTF16_LE = "utf-16-le"  # BOM 없는 UTF-16 Little Endian
    ENC_UTF16_BE = "utf-16-be"
    ENC_CP949 = "cp949"

    # 검색 모드 키워드
    MODE_XML = "XML"
    MODE_JSON = "JSON"
    MODE_ARCHIVE = "Archive"
    MODE_EXACT = "전체 일치"

    # 키보드 키 이름
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

    # 심볼 및 특수 문자
    SYMBOL_CLOSE = "×"

    # 기술적 설정 상수
    YIELD_SLEEP_TIME = 0.001  # 스레드 양보 시간 (초)
    HISTORY_ACTION_CLEAR = "action_clear"
    STATUS_SKIPPED = "SKIPPED"

    # Dock Object Names
    OBJ_NAME_SEARCH_DOCK = "SearchDock"
    OBJ_NAME_FOLDER_DOCK = "FolderDock"
    OBJ_NAME_EXT_DOCK = "ExtDock"
    OBJ_NAME_FILENAME_DOCK = "FilenameDock"

    # 설정 키 이름
    CONFIG_ENABLE_PROFILER = "enable_profiler"

    # 공통 색상 값 (Hex)
    COLOR_RED = "#FF5555"

    # 임계값 및 설정 상수
    THRESHOLD_LARGE_FILE = 100 * 1024 * 1024  # 100MB
    BATCH_SIZE_LARGE = 500
    BATCH_SIZE_NORMAL = 100
    TIMEOUT_WORKER_HANG = 600  # 10분

    # 검색 상태 상수 (State Machine)
    class SearchState:
        IDLE = "IDLE"  # 대기 중
        SCANNING = "SCANNING"  # 파일 목록 스캔 중 (Phase 1)
        SEARCHING = "SEARCHING"  # 본문 검색 중 (Phase 2/3)
        STOPPING = "STOPPING"  # 중단 요청됨 (정리 대기)
