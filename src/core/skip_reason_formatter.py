"""건너뛴 사유 코드(``ERR_…|상세``·``INFO_…|상세``)를 지금 언어의 문장으로 만듭니다.

검색 코드(``search_engine``)와 나눠 둔 표시 전용 모듈입니다(7.2.7). 세션·화면 모델은 코드를 들고 있고,
보여 줄 때만 ``render_skip_reason``으로 문장을 만듭니다. 원래 상세는 화면에 내지 않고 기록에만 남깁니다.
"""

import logging
import os
import re
from typing import Any, cast

from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants
from core.skip_reason_codes import (
    SKIP_CODE_ARCHIVE_FORMAT,
    SKIP_CODE_CRITICAL,
    SKIP_CODE_EXCEL_PANIC,
    SKIP_CODE_EXCEL_PROCESS,
    SKIP_CODE_JSON_PARSE,
    SKIP_CODE_JSON_SIZE_LIMIT,
    SKIP_CODE_MMAP,
    SKIP_CODE_METADATA,
    SKIP_CODE_OPEN,
    SKIP_CODE_PANIC,
    SKIP_CODE_RESOURCE_BUDGET,
    SKIP_CODE_TOO_LARGE,
    SKIP_CODE_UNKNOWN,
    SKIP_CODE_WALK,
    SKIP_CODE_XML_PARSE,
    SKIP_CODE_XML_UNSUPPORTED_DTD,
    TEMPLATE_NAMES,
    XML_DETAIL_TRANSLATION_NAMES,
    decode_skip_reason,
    is_coded_reason,
)

# 검색 코드와 같은 기록 이름을 써서 기록 위치가 바뀌지 않게 합니다.
logger = logging.getLogger("StringFinder.SearchEngine")


def _localize_xml_error_detail(detail: Any, *, unsupported_dtd: bool = False) -> str:
    """XML parser diagnostics are converted to the active UI language."""
    raw_detail = str(detail or "").strip()
    if not raw_detail:
        return (
            AppStrings.XML_DETAIL_DTD_UNSUPPORTED
            if unsupported_dtd
            else AppStrings.XML_DETAIL_INVALID_DOCUMENT
        )

    translated_name = XML_DETAIL_TRANSLATION_NAMES.get(raw_detail)
    if translated_name:
        return cast(str, getattr(AppStrings, translated_name))

    tag_mismatch = re.fullmatch(r"Expecting\s+(</[^>]+>)\s+found\s+(</[^>]+>)", raw_detail)
    if tag_mismatch:
        return AppStrings.XML_DETAIL_TAG_MISMATCH.format(*tag_mismatch.groups())

    if raw_detail.startswith(("Malformed UTF-8 input", "Malformed input")):
        return AppStrings.XML_DETAIL_INVALID_ENCODING
    if raw_detail.startswith("Unexpected EOF during reading"):
        return AppStrings.XML_DETAIL_INCOMPLETE_DOCUMENT
    if raw_detail.startswith("Unexpected token"):
        return AppStrings.XML_DETAIL_INVALID_TOKEN
    if raw_detail.startswith(("XmlDecl must start", "XML declaration")):
        return AppStrings.XML_DETAIL_DECLARATION_POSITION
    if raw_detail.startswith(("error while parsing attribute", "position ")):
        return AppStrings.XML_DETAIL_INVALID_ATTRIBUTE
    if raw_detail.startswith("Unknown namespace prefix"):
        prefix_match = re.search(r"'([^']+)'", raw_detail)
        prefix = prefix_match.group(1) if prefix_match else "?"
        return AppStrings.XML_DETAIL_UNKNOWN_NAMESPACE.format(prefix)
    if any(token in raw_detail for token in ("escaping character", "valid codepoint", "valid hexadecimal", "valid decimal")):
        return AppStrings.XML_DETAIL_INVALID_ENTITY
    if unsupported_dtd:
        return AppStrings.XML_DETAIL_DTD_UNSUPPORTED
    return AppStrings.XML_DETAIL_INVALID_DOCUMENT


def _log_raw_skip_detail(context: str, detail: Any) -> str:
    """Keep diagnostic details in logs without exposing them in the skipped-file UI."""
    raw_detail = str(detail or "").strip()
    if raw_detail:
        logger.debug(AppStrings.LOG_SCH_SKIP_RAW_DETAIL.format(context, raw_detail))
    return raw_detail


def _localize_io_error_detail(detail: Any) -> str:
    """Convert common OS error details into the active UI language."""
    raw_detail = _log_raw_skip_detail("I/O", detail)
    if isinstance(detail, PermissionError):
        return AppStrings.SKIP_DETAIL_PERMISSION_DENIED
    if isinstance(detail, FileNotFoundError):
        return AppStrings.SKIP_DETAIL_FILE_NOT_FOUND

    normalized = raw_detail.casefold()
    if os.name == "nt":
        # Rust reports localized Windows messages with a stable OS error code.
        code = re.search(r"(?:os error|winerror)\s*(\d+)", normalized)
        if code:
            detail_by_code = {
                "5": AppStrings.SKIP_DETAIL_PERMISSION_DENIED,
                "2": AppStrings.SKIP_DETAIL_FILE_NOT_FOUND,
                "3": AppStrings.SKIP_DETAIL_FILE_NOT_FOUND,
                "32": AppStrings.SKIP_DETAIL_FILE_IN_USE,
                "206": AppStrings.SKIP_DETAIL_PATH_TOO_LONG,
            }
            if code.group(1) in detail_by_code:
                return detail_by_code[code.group(1)]
    if any(token in normalized for token in ("permission denied", "access denied", "winerror 5", "접근이 거부", "접근 거부")):
        return AppStrings.SKIP_DETAIL_PERMISSION_DENIED
    if any(token in normalized for token in ("sharing violation", "winerror 32", "being used by another process", "다른 프로세스", "사용 중")):
        return AppStrings.SKIP_DETAIL_FILE_IN_USE
    if any(token in normalized for token in ("file not found", "no such file", "winerror 2", "winerror 3", "찾을 수 없")):
        return AppStrings.SKIP_DETAIL_FILE_NOT_FOUND
    if any(token in normalized for token in ("filename too long", "path too long", "winerror 206", "경로가 너무", "파일 이름이 너무")):
        return AppStrings.SKIP_DETAIL_PATH_TOO_LONG
    return AppStrings.SKIP_DETAIL_IO_FAILURE


def _localize_json_error_detail(detail: Any) -> str:
    """Convert JSON parser diagnostics into a stable localized explanation."""
    if "duplicate JSON object key" in str(detail):
        return AppStrings.JSON_DETAIL_DUPLICATE_KEYS
    raw_detail = _log_raw_skip_detail("JSON", detail)
    position = re.search(r"(?:at\s+)?line\s+(\d+)(?:\s+column\s+(\d+))?", raw_detail, re.IGNORECASE)
    if position:
        line, column = position.groups()
        return AppStrings.JSON_DETAIL_WITH_POSITION.format(
            AppStrings.JSON_DETAIL_INVALID_DOCUMENT,
            line,
            column or "?",
        )
    return AppStrings.JSON_DETAIL_INVALID_DOCUMENT


def _localize_file_size_detail(detail: Any) -> str:
    raw_detail = _log_raw_skip_detail("file-size", detail)
    byte_count = re.search(r"(\d+)\s*bytes?", raw_detail, re.IGNORECASE)
    if byte_count:
        return AppStrings.SKIP_DETAIL_FILE_SIZE_BYTES.format(byte_count.group(1))
    return AppStrings.SKIP_DETAIL_SIZE_LIMIT


def _localize_internal_error_detail(context: str, detail: Any) -> str:
    _log_raw_skip_detail(context, detail)
    return AppStrings.SKIP_DETAIL_INTERNAL_FAILURE


def format_skip_reason(reason: Any) -> str:
    code, detail = decode_skip_reason(reason)
    template_name = TEMPLATE_NAMES.get(code, "SKIP_REASON_UNKNOWN")
    template = cast(str, getattr(AppStrings, template_name))
    safe_detail = detail if detail else str(reason or "")
    if code in (SKIP_CODE_XML_PARSE, SKIP_CODE_XML_UNSUPPORTED_DTD):
        safe_detail = _localize_xml_error_detail(
            safe_detail,
            unsupported_dtd=code == SKIP_CODE_XML_UNSUPPORTED_DTD,
        )
    elif code == SKIP_CODE_JSON_PARSE:
        safe_detail = _localize_json_error_detail(safe_detail)
    elif code == SKIP_CODE_ARCHIVE_FORMAT and (depth := re.fullmatch(r"depth>(\d+)", safe_detail.strip())):
        # 깊이 제한으로 건너뛴 정상 archive를 "형식이 아님"으로 보이지 않게 합니다(7.1.29).
        return AppStrings.ERROR_ARCHIVE_DEPTH_LIMIT.format(depth.group(1))
    elif code == SKIP_CODE_EXCEL_PROCESS:
        safe_detail = _localize_internal_error_detail("Excel conversion", safe_detail)
    elif code == SKIP_CODE_EXCEL_PANIC:
        return format_excel_panic_reason(safe_detail)
    elif code == SKIP_CODE_TOO_LARGE:
        safe_detail = _localize_file_size_detail(safe_detail)
    elif code == SKIP_CODE_JSON_SIZE_LIMIT:
        _log_raw_skip_detail("json-size-limit", safe_detail)
        safe_detail = AppStrings.SKIP_DETAIL_JSON_SIZE_LIMIT
    elif code == SKIP_CODE_RESOURCE_BUDGET:
        _log_raw_skip_detail("resource-budget", safe_detail)
        safe_detail = AppStrings.SKIP_DETAIL_RESOURCE_BUDGET
    elif code == SKIP_CODE_WALK and "file system loop" in safe_detail.casefold():
        _log_raw_skip_detail("walk", safe_detail)
        safe_detail = AppStrings.SKIP_DETAIL_WALK_LOOP
    elif code in (SKIP_CODE_WALK, SKIP_CODE_OPEN, SKIP_CODE_METADATA, SKIP_CODE_MMAP):
        safe_detail = _localize_io_error_detail(safe_detail)
    elif code in (SKIP_CODE_PANIC, SKIP_CODE_CRITICAL, SKIP_CODE_UNKNOWN) or code not in TEMPLATE_NAMES:
        safe_detail = _localize_internal_error_detail(code, safe_detail)
    try:
        return template.format(safe_detail)
    except Exception:
        return AppStrings.SKIP_REASON_UNKNOWN.format(AppStrings.SKIP_DETAIL_INTERNAL_FAILURE)


def format_excel_panic_reason(detail: Any) -> str:
    """Convert internal Excel panic diagnostics into a localized user message."""
    raw_detail = str(detail or "").strip()
    if raw_detail:
        logger.warning(AppStrings.LOG_SCH_EXCEL_ENGINE_PANIC.format(raw_detail))
    format_name = ""
    engine_detail = raw_detail
    if "|" in raw_detail:
        candidate, engine_detail = raw_detail.split("|", 1)
        if candidate.strip().lower() in Constants.EXT_EXCEL:
            format_name = candidate.strip().upper()
        else:
            engine_detail = raw_detail
    elif raw_detail.lower() in Constants.EXT_EXCEL:
        format_name = raw_detail.upper()
        engine_detail = ""

    range_error = re.fullmatch(
        r"range start index (\d+) out of range for slice of length (\d+)",
        engine_detail.strip(), flags=re.IGNORECASE,
    )
    if range_error:
        localized_detail = AppStrings.EXCEL_DETAIL_RANGE_OUT_OF_BOUNDS.format(
            format_name or "Excel", *range_error.groups())
    elif format_name:
        localized_detail = AppStrings.EXCEL_DETAIL_ENGINE_FAILURE.format(format_name)
    else:
        localized_detail = AppStrings.EXCEL_DETAIL_UNKNOWN_FORMAT
    return AppStrings.ERROR_EXCEL_PANIC.format(localized_detail)


def render_skip_reason(reason: Any) -> str:
    """저장된 건너뛴 사유(코드 형식, 여러 줄 가능)를 지금 언어의 문장으로 만듭니다(7.2.5).

    세션·화면 모델에는 ``ERR_…|상세``·``INFO_…|상세`` 코드를 두고, 보여 줄 때만 이 함수로 문장을 만듭니다.
    코드 형식이 아닌 줄은 그대로 둡니다.
    """
    lines = []
    for line in str(reason or "").splitlines():
        line = line.strip()
        if not line:
            continue
        lines.append(format_skip_reason(line) if is_coded_reason(line) else line)
    return "\n".join(dict.fromkeys(lines))
