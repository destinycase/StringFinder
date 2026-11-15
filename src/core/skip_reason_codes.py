"""Stable skip-reason codes and pure parsing helpers."""

SKIP_CODE_WALK = "ERR_WALK"
UNKNOWN_WALK_PATH_PREFIX = "__SF_WALK_UNKNOWN__|"
SKIP_CODE_OPEN = "ERR_OPEN"
SKIP_CODE_ENCODING = "ERR_ENCODING"
SKIP_CODE_DECODING = "ERR_DECODING"
SKIP_CODE_METADATA = "ERR_METADATA"
SKIP_CODE_MMAP = "ERR_MMAP"
SKIP_CODE_TOO_LARGE = "ERR_TOO_LARGE"
SKIP_CODE_JSON_SIZE_LIMIT = "ERR_JSON_SIZE_LIMIT"
SKIP_CODE_RESOURCE_BUDGET = "ERR_RESOURCE_BUDGET"
SKIP_CODE_JSON_PARSE = "ERR_JSON_PARSE"
SKIP_CODE_ARCHIVE_FORMAT = "ERR_ARCHIVE_FORMAT"
SKIP_CODE_XML_PARSE = "ERR_XML_PARSE"
SKIP_CODE_XML_UNSUPPORTED_DTD = "ERR_XML_UNSUPPORTED_DTD"
SKIP_CODE_EXCEL_PROCESS = "ERR_EXCEL_PROCESS"
SKIP_CODE_EXCEL_PANIC = "ERR_EXCEL_PANIC"
SKIP_CODE_PANIC = "ERR_PANIC"
SKIP_CODE_CRITICAL = "ERR_CRITICAL"
SKIP_CODE_FILE_MATCH_LIMIT = "INFO_FILE_MATCH_LIMIT"
SKIP_CODE_JSON_DEPTH_LIMIT = "INFO_JSON_DEPTH_LIMIT"
# 디코딩 중 해석하지 못한 바이트가 있어 일부 내용을 검색하지 못했을 수 있음(제안서 2.5).
SKIP_CODE_DECODE_PARTIAL = "INFO_DECODE_PARTIAL"
# 아래 두 코드는 Python이 만듭니다(7.2.5): 일부 시트를 읽지 못함, 지정한 폴더가 정션이라 제외함.
SKIP_CODE_EXCEL_PARTIAL = "INFO_EXCEL_PARTIAL"
SKIP_CODE_ROOT_JUNCTION = "INFO_ROOT_JUNCTION"
SKIP_CODE_UNKNOWN = "ERR_UNKNOWN"

TEMPLATE_NAMES = {
    SKIP_CODE_DECODING: "ERROR_DOCUMENT_ENCODING",
    SKIP_CODE_ENCODING: "ERROR_SELECTED_ENCODING",
    SKIP_CODE_WALK: "SKIP_REASON_WALK", SKIP_CODE_OPEN: "SKIP_REASON_OPEN",
    SKIP_CODE_METADATA: "SKIP_REASON_METADATA", SKIP_CODE_MMAP: "SKIP_REASON_MMAP",
    SKIP_CODE_TOO_LARGE: "SKIP_REASON_TOO_LARGE", SKIP_CODE_JSON_SIZE_LIMIT: "SKIP_REASON_JSON_SIZE_LIMIT",
    SKIP_CODE_RESOURCE_BUDGET: "SKIP_REASON_RESOURCE_BUDGET", SKIP_CODE_FILE_MATCH_LIMIT: "SKIP_REASON_FILE_MATCH_LIMIT",
    SKIP_CODE_JSON_DEPTH_LIMIT: "SKIP_REASON_JSON_DEPTH_LIMIT",
    SKIP_CODE_DECODE_PARTIAL: "SKIP_REASON_DECODE_PARTIAL",
    SKIP_CODE_EXCEL_PARTIAL: "SKIP_REASON_EXCEL_PARTIAL",
    SKIP_CODE_ROOT_JUNCTION: "SKIP_ROOT_JUNCTION",
    SKIP_CODE_JSON_PARSE: "ERROR_JSON_PARSE", SKIP_CODE_ARCHIVE_FORMAT: "ERROR_ARCHIVE_FORMAT",
    SKIP_CODE_XML_PARSE: "ERROR_XML_PARSE",
    SKIP_CODE_XML_UNSUPPORTED_DTD: "ERROR_XML_UNSUPPORTED_DTD", SKIP_CODE_EXCEL_PROCESS: "ERROR_EXCEL_PROCESS",
    SKIP_CODE_EXCEL_PANIC: "ERROR_EXCEL_PANIC", SKIP_CODE_PANIC: "SKIP_REASON_PANIC",
    SKIP_CODE_CRITICAL: "SKIP_REASON_CRITICAL", SKIP_CODE_UNKNOWN: "SKIP_REASON_UNKNOWN",
}

XML_DETAIL_TRANSLATION_NAMES = {
    "XML declaration must appear exactly once at the beginning of the document": "XML_DETAIL_DECLARATION_POSITION",
    "DOCTYPE declaration must appear before the root element": "XML_DETAIL_DOCTYPE_POSITION",
    "DTD declarations and entity expansion are not supported": "XML_DETAIL_DTD_UNSUPPORTED",
    "multiple root elements": "XML_DETAIL_MULTIPLE_ROOTS",
    "unexpected closing element": "XML_DETAIL_UNEXPECTED_CLOSING",
    "text outside the root element": "XML_DETAIL_TEXT_OUTSIDE_ROOT",
    "CDATA outside the root element": "XML_DETAIL_CDATA_OUTSIDE_ROOT",
    "XML document is incomplete or has no root element": "XML_DETAIL_INCOMPLETE_DOCUMENT",
    "Malformed input, decoding impossible": "XML_DETAIL_INVALID_ENCODING",
}


def build_skip_reason(code: str, detail: object) -> str:
    return "{}|{}".format(code, "" if detail is None else str(detail))


def decode_skip_reason(reason: object) -> tuple[str, str]:
    reason_str = str(reason or "").strip()
    if not reason_str:
        return SKIP_CODE_UNKNOWN, ""
    if "|" in reason_str:
        code, detail = reason_str.split("|", 1)
        code = code.strip().upper()
        if code.startswith(("ERR_", "INFO_")):
            return code, detail.strip()
    return SKIP_CODE_UNKNOWN, reason_str


def is_coded_reason(reason: object) -> bool:
    """건너뛴 사유가 코드 형식(``ERR_…|상세`` 또는 ``INFO_…|상세``, 여러 줄 가능)인지입니다."""
    lines = [line.strip() for line in str(reason or "").splitlines() if line.strip()]
    return bool(lines) and all(
        line.split("|", 1)[0].upper().startswith(("ERR_", "INFO_")) and "|" in line for line in lines
    )
