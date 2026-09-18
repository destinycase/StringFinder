"""Stable skip-reason codes and pure parsing helpers."""

import re

SKIP_CODE_WALK = "ERR_WALK"
SKIP_CODE_OPEN = "ERR_OPEN"
SKIP_CODE_METADATA = "ERR_METADATA"
SKIP_CODE_MMAP = "ERR_MMAP"
SKIP_CODE_TOO_LARGE = "ERR_TOO_LARGE"
SKIP_CODE_JSON_SIZE_LIMIT = "ERR_JSON_SIZE_LIMIT"
SKIP_CODE_RESOURCE_BUDGET = "ERR_RESOURCE_BUDGET"
SKIP_CODE_JSON_PARSE = "ERR_JSON_PARSE"
SKIP_CODE_XML_PARSE = "ERR_XML_PARSE"
SKIP_CODE_XML_UNSUPPORTED_DTD = "ERR_XML_UNSUPPORTED_DTD"
SKIP_CODE_EXCEL_PROCESS = "ERR_EXCEL_PROCESS"
SKIP_CODE_EXCEL_PANIC = "ERR_EXCEL_PANIC"
SKIP_CODE_PANIC = "ERR_PANIC"
SKIP_CODE_CRITICAL = "ERR_CRITICAL"
SKIP_CODE_FILE_MATCH_LIMIT = "INFO_FILE_MATCH_LIMIT"
SKIP_CODE_JSON_DEPTH_LIMIT = "INFO_JSON_DEPTH_LIMIT"
SKIP_CODE_EXCEL_CELL_LIMIT = "INFO_EXCEL_CELL_LIMIT"
SKIP_CODE_UNKNOWN = "ERR_UNKNOWN"

TEMPLATE_NAMES = {
    SKIP_CODE_WALK: "SKIP_REASON_WALK", SKIP_CODE_OPEN: "SKIP_REASON_OPEN",
    SKIP_CODE_METADATA: "SKIP_REASON_METADATA", SKIP_CODE_MMAP: "SKIP_REASON_MMAP",
    SKIP_CODE_TOO_LARGE: "SKIP_REASON_TOO_LARGE", SKIP_CODE_JSON_SIZE_LIMIT: "SKIP_REASON_JSON_SIZE_LIMIT",
    SKIP_CODE_RESOURCE_BUDGET: "SKIP_REASON_RESOURCE_BUDGET", SKIP_CODE_FILE_MATCH_LIMIT: "SKIP_REASON_FILE_MATCH_LIMIT",
    SKIP_CODE_JSON_DEPTH_LIMIT: "SKIP_REASON_JSON_DEPTH_LIMIT", SKIP_CODE_EXCEL_CELL_LIMIT: "SKIP_REASON_EXCEL_CELL_LIMIT",
    SKIP_CODE_JSON_PARSE: "ERROR_JSON_PARSE", SKIP_CODE_XML_PARSE: "ERROR_XML_PARSE",
    SKIP_CODE_XML_UNSUPPORTED_DTD: "ERROR_XML_UNSUPPORTED_DTD", SKIP_CODE_EXCEL_PROCESS: "ERROR_EXCEL_PROCESS",
    SKIP_CODE_EXCEL_PANIC: "ERROR_EXCEL_PANIC", SKIP_CODE_PANIC: "SKIP_REASON_PANIC",
    SKIP_CODE_CRITICAL: "SKIP_REASON_CRITICAL", SKIP_CODE_UNKNOWN: "SKIP_REASON_UNKNOWN",
}

LEGACY_MARKERS = (("walker error", SKIP_CODE_WALK), ("walk error", SKIP_CODE_WALK),
                  ("open error", SKIP_CODE_OPEN), ("metadata error", SKIP_CODE_METADATA),
                  ("mmap error", SKIP_CODE_MMAP), ("file too large", SKIP_CODE_TOO_LARGE),
                  ("panic", SKIP_CODE_PANIC), ("critical error", SKIP_CODE_CRITICAL))

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
    "DOCTYPE declaration must not be empty": "XML_DETAIL_EMPTY_DOCTYPE",
}

EXPAT_XML_DETAIL_TRANSLATION_NAMES = {
    "mismatched tag": "XML_DETAIL_MISMATCHED_TAG",
    "junk after document element": "XML_DETAIL_MULTIPLE_ROOTS",
    "not well-formed (invalid token)": "XML_DETAIL_INVALID_TOKEN",
    "no element found": "XML_DETAIL_INCOMPLETE_DOCUMENT",
    "unclosed token": "XML_DETAIL_INCOMPLETE_DOCUMENT",
    "duplicate attribute": "XML_DETAIL_INVALID_ATTRIBUTE",
    "XML or text declaration not at start of entity": "XML_DETAIL_DECLARATION_POSITION",
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
    bracket_match = re.match(r"^\[((?:ERR|INFO)_[A-Z_]+)\]\s*(.*)$", reason_str)
    if bracket_match:
        return bracket_match.group(1), bracket_match.group(2).lstrip(":").strip()
    lower_reason = reason_str.lower()
    for marker, code in LEGACY_MARKERS:
        if marker in lower_reason:
            detail = reason_str.split(":", 1)[1].strip() if ":" in reason_str else reason_str
            return code, detail
    return SKIP_CODE_UNKNOWN, reason_str
