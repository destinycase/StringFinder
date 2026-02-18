class Constants:
    APP_NAME = "StringFinder"
    APP_VERSION = "v4.34.0"

    TYPE_SEARCH = "search"
    TYPE_FILENAME = "filename"
    TYPE_FOLDER = "folder"
    TYPE_EXT = "ext"
    TYPE_FILENAME_LIST = "filename_list"

    VIEW_RESULT = "result"
    VIEW_MATCH = "match"

    ENC_UTF8 = "utf-8"
    ENC_UTF8_SIG = "utf-8-sig"
    ENC_UTF16 = "utf-16"
    ENC_UTF16_LE = "utf-16-le"
    ENC_UTF16_BE = "utf-16-be"
    ENC_CP949 = "cp949"

    MODE_XML = "XML"
    MODE_JSON = "JSON"
    MODE_ARCHIVE = "Archive"
    MODE_EXCEL = "Excel"
    MODE_EXACT = "정확히 일치"

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

    SYMBOL_CLOSE = "×"

    YIELD_SLEEP_TIME = 0.001
    HISTORY_ACTION_CLEAR = "action_clear"
    STATUS_SKIPPED = "SKIPPED"

    # Dock Object Names
    OBJ_NAME_SEARCH_DOCK = "SearchDock"
    OBJ_NAME_FOLDER_DOCK = "FolderDock"
    OBJ_NAME_EXT_DOCK = "ExtDock"
    OBJ_NAME_FILENAME_DOCK = "FilenameDock"

    COLOR_RED = "#FF5555"

    THRESHOLD_LARGE_FILE = 100 * 1024 * 1024  # 100MB
    BATCH_SIZE_LARGE = 500
    BATCH_SIZE_NORMAL = 100
    TIMEOUT_WORKER_HANG = 600

    class SearchState:
        IDLE = "IDLE"
        SCANNING = "SCANNING"
        SEARCHING = "SEARCHING"
        STOPPING = "STOPPING"
