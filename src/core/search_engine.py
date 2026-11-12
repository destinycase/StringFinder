import ctypes
from contextlib import contextmanager
from contextvars import ContextVar
import logging
import os
import re
import threading
import unicodedata
from typing import Any, Dict, List, Optional, Tuple, cast

from sf_utils.app_strings import AppStrings
from sf_utils.config_manager import ConfigManager
from sf_utils.constants import Constants
from sf_utils.english_strings import ENGLISH_STRINGS
from sf_utils.localization import get_korean_strings, get_language
from core.search_query import validate_search_query
from core.skip_reason_codes import (
    EXPAT_XML_DETAIL_TRANSLATION_NAMES,
    LEGACY_MARKERS,
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
    build_skip_reason,
    decode_skip_reason,
)
from core.skip_reason_formatter import format_excel_panic_reason as _format_excel_panic_reason

_SEARCH_SETTINGS_SNAPSHOT: ContextVar[Optional[dict[str, Any]]] = ContextVar(
    "search_settings_snapshot", default=None
)


@contextmanager
def use_search_settings_snapshot(settings: Optional[dict[str, Any]]):
    """Use one immutable-for-this-search advanced-settings snapshot."""
    token = _SEARCH_SETTINGS_SNAPSHOT.set(settings)
    try:
        yield
    finally:
        _SEARCH_SETTINGS_SNAPSHOT.reset(token)


def _get_adv_setting(key, default):
    snapshot = _SEARCH_SETTINGS_SNAPSHOT.get()
    if snapshot is not None:
        return snapshot.get(key, default)
    return ConfigManager().get_advanced_settings().get(key, default)


def _get_rust_search_limits() -> Tuple[int, int, int]:
    """Return the safety limits supported by the Rust search API.

    Keep the normalization here so every Rust entry point receives the same
    positive integer values, including when a legacy or hand-edited config is
    malformed.  The defaults match the Rust API defaults, preserving existing
    behavior when users have not customized these settings.
    """
    settings = _SEARCH_SETTINGS_SNAPSHOT.get()
    if settings is None:
        settings = ConfigManager().get_advanced_settings()

    def positive_int(key: str, default: int) -> int:
        try:
            return max(1, int(settings.get(key, default)))
        except (TypeError, ValueError):
            return default

    max_search_file_size_mb = min(
        positive_int(
            Constants.CONFIG_KEY_MAX_SEARCH_FILE_SIZE_MB,
            Constants.DEFAULT_MAX_SEARCH_FILE_SIZE_MB,
        ),
        Constants.SETTING_MAX_SEARCH_FILE_SIZE_MB,
    )
    return (
        positive_int(Constants.CONFIG_KEY_MAX_PER_FILE_MATCHES, Constants.DEFAULT_MAX_PER_FILE_MATCHES),
        positive_int(Constants.CONFIG_KEY_MAX_JSON_DEPTH, Constants.DEFAULT_MAX_JSON_DEPTH),
        max_search_file_size_mb * 1024 * 1024,
    )


def _build_rust_options(**values: Any) -> Optional[Any]:
    """Rust 검색 옵션 객체(``SearchOptions``)를 만듭니다. 앱이 요구하는 엔진 API에는 항상 있습니다."""
    options_type = getattr(sf_engine, "SearchOptions", None)
    if options_type is None:
        # 테스트의 가짜 엔진처럼 옵션 객체가 없으면 키워드 인자로 호출합니다.
        return None
    include_junctions = _get_adv_setting(Constants.CONFIG_KEY_INCLUDE_JUNCTIONS, False) is True
    encoding = _get_adv_setting(Constants.CONFIG_KEY_SEARCH_ENCODING, "auto")
    if hasattr(options_type, "encoding"):
        values.setdefault("include_junctions", include_junctions)
        values.setdefault("encoding", encoding)
    elif include_junctions or encoding != "auto":
        raise RuntimeError("The Rust engine must be rebuilt to support junction and encoding options")
    if values.get("max_total_matches") is None or not hasattr(options_type, "max_total_matches"):
        values.pop("max_total_matches", None)
    return options_type(**values)


def _positive_int_or_none(value: Any) -> Optional[int]:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _rust_policy_kwargs() -> dict[str, Any]:
    options = _build_rust_options()
    return {"options": options} if options is not None else {}


def _get_structured_memory_budget() -> int:
    """Return one shared budget for concurrent native structured searches."""
    try:
        from sf_utils.resource_guard import (
            available_processing_budget_bytes,
            memory_snapshot,
        )

        budget = available_processing_budget_bytes(memory_snapshot())
        if budget > 0:
            return budget
    except Exception as exc:
        logger.debug("Structured memory budget calculation failed: %s", exc)
    return Constants.MIN_AVAILABLE_MEMORY_BYTES


EXCEL_EXTS = {f".{extension}" for extension in Constants.EXT_EXCEL}
logger = logging.getLogger("StringFinder.SearchEngine")
_RUST_ENGINE_ERROR: str = ""  # 로드 실패 시 상세 사유를 보존합니다 (진입점 팝업용)
sf_engine = None
try:
    from rust_engine import sf_engine  # type: ignore
    import hashlib

    REQUIRED_API_VERSION = 8
    engine_version = getattr(sf_engine, "API_VERSION", 0)
    if engine_version < REQUIRED_API_VERSION:
        _err_msg = AppStrings.ERROR_RUST_API_VERSION.format(REQUIRED_API_VERSION, engine_version)
        logger.error(AppStrings.LOG_SYS_SF_ENGINE_NOT_FOUND.format(_err_msg))
        _RUST_ENGINE_ERROR = _err_msg
        HAS_RUST_ENGINE = False
    else:
        HAS_RUST_ENGINE = True

        # 로드된 엔진의 물리 경로 및 해시 무결성 로깅
        engine_path = getattr(sf_engine, "__file__", "unknown")
        engine_hash = "unknown"
        if engine_path != "unknown" and os.path.exists(engine_path):
            try:
                with open(engine_path, "rb") as f:
                    file_hash = hashlib.sha256()
                    while chunk := f.read(8192):
                        file_hash.update(chunk)
                engine_hash = file_hash.hexdigest()
            except Exception as e:
                engine_hash = f"error: {e}"

        logger.info(AppStrings.LOG_SYS_RUST_PATH.format(engine_path))
        logger.info(AppStrings.LOG_SYS_RUST_HASH.format(engine_hash))
        logger.info(AppStrings.LOG_SYS_RUST_SUCCESS)

except ImportError as e:
    HAS_RUST_ENGINE = False
    _RUST_ENGINE_ERROR = f"ImportError: {e}"
    logger.warning(AppStrings.LOG_SYS_RUST_FALLBACK)
except Exception as e:
    HAS_RUST_ENGINE = False
    _RUST_ENGINE_ERROR = f"{type(e).__name__}: {e}"
    logger.warning(AppStrings.LOG_SYS_RUST_FALLBACK)

SearchMatch = Tuple[Any, ...]
SearchResult = Tuple[str, int, List[SearchMatch]]
SkippedResult = Tuple[str, str]


RUST_MATCH_MARKER_BINARY = "__SF_BINARY_MATCH__|"
RUST_MATCH_MARKER_LONG_LINE = "__SF_LONG_LINE__|"
RUST_MATCH_MARKER_TRUNCATED = "__SF_TRUNCATED__"
RUST_MATCH_MARKER_JSON_DEPTH_LIMIT = "__SF_JSON_DEPTH_LIMIT__|"
# Rust UE 현지화 archive 결과 표식입니다. 뒤에 [Namespace, Key, 원문, 번역문] JSON 배열이 옵니다.
RUST_MATCH_MARKER_ARCHIVE_FIELDS = "ARCHIVE_FIELDS|"
RUST_MATCH_MARKER_EXCEL_SHEET_ERROR = "__SF_EXCEL_SHEET_ERR__|"
RUST_MATCH_MARKER_DECODE_PARTIAL = "__SF_DECODE_PARTIAL__|"
RUST_MATCH_MARKER_EXCEL_PANIC = "__SF_EXCEL_PANIC__|"
_SKIP_REASON_TEMPLATE_NAMES = TEMPLATE_NAMES
# Keep accepting skip reasons saved by releases predating the structured
# ``ERR_*|detail`` protocol. Session files can outlive the application version.
_LEGACY_SKIP_MARKERS = LEGACY_MARKERS

_XML_DETAIL_TRANSLATION_NAMES = XML_DETAIL_TRANSLATION_NAMES
_EXPAT_XML_DETAIL_TRANSLATION_NAMES = EXPAT_XML_DETAIL_TRANSLATION_NAMES

def _build_skip_reason(code: str, detail: Any) -> str:
    return build_skip_reason(code, detail)


def _decode_skip_reason(reason: Any) -> Tuple[str, str]:
    return decode_skip_reason(reason)


def _localize_xml_error_detail(detail: Any, *, unsupported_dtd: bool = False) -> str:
    """XML parser diagnostics are converted to the active UI language."""
    raw_detail = str(detail or "").strip()
    if not raw_detail:
        return (
            AppStrings.XML_DETAIL_DTD_UNSUPPORTED
            if unsupported_dtd
            else AppStrings.XML_DETAIL_INVALID_DOCUMENT
        )

    translated_name = _XML_DETAIL_TRANSLATION_NAMES.get(raw_detail)
    if translated_name:
        return cast(str, getattr(AppStrings, translated_name))

    tag_mismatch = re.fullmatch(r"Expecting\s+(</[^>]+>)\s+found\s+(</[^>]+>)", raw_detail)
    if tag_mismatch:
        return AppStrings.XML_DETAIL_TAG_MISMATCH.format(*tag_mismatch.groups())

    expat_location = re.fullmatch(r"(.+): line (\d+), column (\d+)", raw_detail)
    if expat_location:
        message, line, column = expat_location.groups()
        if message == "unbound prefix":
            localized_message = AppStrings.XML_DETAIL_UNKNOWN_NAMESPACE.format("prefix")
        else:
            localized_name = _EXPAT_XML_DETAIL_TRANSLATION_NAMES.get(message)
            localized_message = (
                getattr(AppStrings, localized_name)
                if localized_name
                else AppStrings.XML_DETAIL_INVALID_DOCUMENT
            )
        return AppStrings.XML_DETAIL_WITH_POSITION.format(localized_message, line, column)

    # A Korean legacy-session reason is safe only while the Korean catalog is active.
    if re.search(r"[가-힣]", raw_detail):
        return raw_detail if get_language() == "ko" else AppStrings.XML_DETAIL_INVALID_DOCUMENT

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
    code, detail = _decode_skip_reason(reason)
    template_name = _SKIP_REASON_TEMPLATE_NAMES.get(code, "SKIP_REASON_UNKNOWN")
    template = cast(str, getattr(AppStrings, template_name))
    safe_detail = detail if detail else str(reason or "")
    if code in (SKIP_CODE_XML_PARSE, SKIP_CODE_XML_UNSUPPORTED_DTD):
        safe_detail = _localize_xml_error_detail(
            safe_detail,
            unsupported_dtd=code == SKIP_CODE_XML_UNSUPPORTED_DTD,
        )
    elif code == SKIP_CODE_JSON_PARSE:
        safe_detail = _localize_json_error_detail(safe_detail)
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
    elif code in (SKIP_CODE_PANIC, SKIP_CODE_CRITICAL, SKIP_CODE_UNKNOWN) or code not in _SKIP_REASON_TEMPLATE_NAMES:
        safe_detail = _localize_internal_error_detail(code, safe_detail)
    try:
        return template.format(safe_detail)
    except Exception:
        return AppStrings.SKIP_REASON_UNKNOWN.format(AppStrings.SKIP_DETAIL_INTERNAL_FAILURE)


def format_excel_panic_reason(detail: Any) -> str:
    return _format_excel_panic_reason(
        detail, app_strings=AppStrings, constants=Constants, logger=logger
    )


_LOCALIZED_SKIP_MESSAGE_NAMES = (
    "SKIP_ROOT_JUNCTION",
    "ERROR_SELECTED_ENCODING",
    "ERROR_DOCUMENT_ENCODING",
    "ERROR_JSON_PARSE",
    "ERROR_ARCHIVE_FORMAT",
    "ERROR_EXCEL_ACCESS",
    "ERROR_EXCEL_SIGNATURE",
    "ERROR_EXCEL_PROCESS",
    "ERROR_EXCEL_CALAMINE",
    "ERROR_SEARCH_EXCEL",
    "ERROR_SEARCH_EXCEL_SHEET",
    "ERROR_EXCEL_PANIC",
    "ERROR_XML_PARSE",
    "ERROR_XML_UNSUPPORTED_DTD",
    "ERROR_FILE_ACCESS_BINARY",
    "ERROR_IO_DURING_SEARCH",
    "ERROR_UNEXPECTED_FILE",
    "ERROR_MEMORY_CRITICAL",
    "SKIP_EMPTY_FILE",
    "SKIP_REASON_WALK",
    "SKIP_REASON_OPEN",
    "SKIP_REASON_METADATA",
    "SKIP_REASON_MMAP",
    "SKIP_REASON_TOO_LARGE",
    "SKIP_REASON_JSON_SIZE_LIMIT",
    "SKIP_REASON_RESOURCE_BUDGET",
    "SKIP_REASON_FILE_MATCH_LIMIT",
    "SKIP_REASON_JSON_DEPTH_LIMIT",
    "SKIP_REASON_EXCEL_PARTIAL",
    "SKIP_REASON_DECODE_PARTIAL",
    "SKIP_REASON_PANIC",
    "SKIP_REASON_CRITICAL",
    "SKIP_REASON_BATCH",
    "SKIP_REASON_UNKNOWN",
)


def _match_localized_skip_resource(reason: str, catalog: Dict[str, Any]) -> Optional[str]:
    for resource_name in _LOCALIZED_SKIP_MESSAGE_NAMES:
        template = str(catalog.get(resource_name, ""))
        prefix = template.split("{", 1)[0]
        if prefix and reason.startswith(prefix):
            return resource_name
        if template and "{" not in template and reason == template:
            return resource_name
    return None


def _is_current_localized_skip_reason(reason: str) -> bool:
    """Return whether a reason was already rendered with the active catalog."""
    current_catalog = {
        name: getattr(AppStrings, name, "")
        for name in _LOCALIZED_SKIP_MESSAGE_NAMES
    }
    return _match_localized_skip_resource(reason, current_catalog) is not None


def is_supported_skip_reason(reason: Any) -> bool:
    """Return whether a saved skip reason belongs to the supported protocol."""
    if not isinstance(reason, str):
        return False
    reason_text = reason.strip()
    if not reason_text:
        return False

    catalogs = (get_korean_strings(), ENGLISH_STRINGS)
    for line in reason_text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(("ERR_", "INFO_")) and "|" in line:
            code, _ = _decode_skip_reason(line)
            if code not in _SKIP_REASON_TEMPLATE_NAMES:
                return False
            continue
        if line.startswith(RUST_MATCH_MARKER_EXCEL_PANIC):
            continue
        if re.match(r"^(?:xlsx?|xlsm|xlsb)\|range start index ", line, re.IGNORECASE):
            continue
        if any(_match_localized_skip_resource(line, catalog) for catalog in catalogs):
            continue
        code, _ = _decode_skip_reason(line)
        if code not in _SKIP_REASON_TEMPLATE_NAMES or code == SKIP_CODE_UNKNOWN:
            return False
    return True


def _render_saved_skip_resource(resource_name: str, source_reason: str = "") -> str:
    """Re-render a categorized legacy-session reason with the active catalog."""
    saved_limit_match = re.search(
        r"\(([0-9,]+)(?:건|개|\s*cells?)?\)",
        source_reason,
        flags=re.IGNORECASE,
    )
    saved_limit = (
        saved_limit_match.group(1).replace(",", "")
        if saved_limit_match
        else None
    )
    direct_resources = {
        "ERROR_EXCEL_SIGNATURE",
        "ERROR_EXCEL_CALAMINE",
        "ERROR_MEMORY_CRITICAL",
        "SKIP_EMPTY_FILE",
        "ERROR_DOCUMENT_ENCODING",
        "ERROR_SELECTED_ENCODING",
        "SKIP_ROOT_JUNCTION",
    }
    if resource_name in direct_resources:
        return str(getattr(AppStrings, resource_name))
    if resource_name == "SKIP_REASON_DECODE_PARTIAL":
        for catalog in (get_korean_strings(), ENGLISH_STRINGS):
            prefix, suffix = str(catalog[resource_name]).split("{}", 1)
            if source_reason.startswith(prefix) and source_reason.endswith(suffix):
                encoding = source_reason[len(prefix):len(source_reason) - len(suffix)]
                return AppStrings.SKIP_REASON_DECODE_PARTIAL.format(encoding)
    if resource_name == "SKIP_REASON_EXCEL_PARTIAL":
        for catalog in (get_korean_strings(), ENGLISH_STRINGS):
            prefix, suffix = str(catalog[resource_name]).split("{}", 1)
            if source_reason.startswith(prefix) and source_reason.endswith(suffix):
                sheets = source_reason[len(prefix):len(source_reason) - len(suffix)]
                return AppStrings.SKIP_REASON_EXCEL_PARTIAL.format(sheets)
    if resource_name == "ERROR_JSON_PARSE":
        return AppStrings.ERROR_JSON_PARSE.format(AppStrings.JSON_DETAIL_INVALID_DOCUMENT)
    if resource_name == "ERROR_ARCHIVE_FORMAT":
        # 문제 위치는 언어와 무관한 구조 경로이므로 그대로 옮깁니다.
        for catalog in (get_korean_strings(), ENGLISH_STRINGS):
            prefix, suffix = str(catalog[resource_name]).split("{}", 1)
            if source_reason.startswith(prefix) and source_reason.endswith(suffix):
                location = source_reason[len(prefix):len(source_reason) - len(suffix)]
                return AppStrings.ERROR_ARCHIVE_FORMAT.format(location)
    if resource_name == "ERROR_XML_PARSE":
        return AppStrings.ERROR_XML_PARSE.format(AppStrings.XML_DETAIL_INVALID_DOCUMENT)
    if resource_name == "ERROR_XML_UNSUPPORTED_DTD":
        return AppStrings.ERROR_XML_UNSUPPORTED_DTD.format(AppStrings.XML_DETAIL_DTD_UNSUPPORTED)
    if resource_name == "ERROR_EXCEL_ACCESS":
        return AppStrings.ERROR_EXCEL_ACCESS.format(AppStrings.SKIP_DETAIL_IO_FAILURE)
    if resource_name in {"ERROR_EXCEL_PROCESS", "ERROR_SEARCH_EXCEL", "ERROR_SEARCH_EXCEL_SHEET"}:
        return AppStrings.ERROR_EXCEL_PROCESS.format(AppStrings.EXCEL_DETAIL_UNKNOWN_FORMAT)
    if resource_name == "ERROR_EXCEL_PANIC":
        return AppStrings.ERROR_EXCEL_PANIC.format(AppStrings.EXCEL_DETAIL_UNKNOWN_FORMAT)
    if resource_name in {"ERROR_FILE_ACCESS_BINARY", "ERROR_IO_DURING_SEARCH"}:
        return AppStrings.ERROR_IO_DURING_SEARCH.format(AppStrings.SKIP_DETAIL_IO_FAILURE)
    if resource_name == "ERROR_UNEXPECTED_FILE":
        return AppStrings.SKIP_REASON_UNKNOWN.format(AppStrings.SKIP_DETAIL_INTERNAL_FAILURE)

    if resource_name == "SKIP_REASON_OPEN":
        return AppStrings.SKIP_REASON_OPEN.format(AppStrings.SKIP_DETAIL_PERMISSION_DENIED)
    if resource_name in {"SKIP_REASON_WALK", "SKIP_REASON_METADATA", "SKIP_REASON_MMAP"}:
        template = cast(str, getattr(AppStrings, resource_name))
        return template.format(AppStrings.SKIP_DETAIL_IO_FAILURE)
    if resource_name == "SKIP_REASON_TOO_LARGE":
        return AppStrings.SKIP_REASON_TOO_LARGE.format(AppStrings.SKIP_DETAIL_SIZE_LIMIT)
    if resource_name == "SKIP_REASON_JSON_SIZE_LIMIT":
        return AppStrings.SKIP_REASON_JSON_SIZE_LIMIT.format(AppStrings.SKIP_DETAIL_JSON_SIZE_LIMIT)
    if resource_name == "SKIP_REASON_RESOURCE_BUDGET":
        return AppStrings.SKIP_REASON_RESOURCE_BUDGET.format(AppStrings.SKIP_DETAIL_RESOURCE_BUDGET)
    if resource_name == "SKIP_REASON_FILE_MATCH_LIMIT":
        return AppStrings.SKIP_REASON_FILE_MATCH_LIMIT.format(
            saved_limit
            or _get_adv_setting(
                Constants.CONFIG_KEY_MAX_PER_FILE_MATCHES,
                Constants.DEFAULT_MAX_PER_FILE_MATCHES,
            )
        )
    if resource_name == "SKIP_REASON_JSON_DEPTH_LIMIT":
        return AppStrings.SKIP_REASON_JSON_DEPTH_LIMIT.format(
            saved_limit
            or _get_adv_setting(
                Constants.CONFIG_KEY_MAX_JSON_DEPTH,
                Constants.DEFAULT_MAX_JSON_DEPTH,
            )
        )
    if resource_name in {"SKIP_REASON_PANIC", "SKIP_REASON_CRITICAL"}:
        template = cast(str, getattr(AppStrings, resource_name))
        return template.format(AppStrings.SKIP_DETAIL_INTERNAL_FAILURE)
    if resource_name == "SKIP_REASON_BATCH":
        return AppStrings.SKIP_REASON_BATCH.format(AppStrings.SKIP_DETAIL_INTERNAL_FAILURE)
    return AppStrings.SKIP_REASON_UNKNOWN.format(AppStrings.SKIP_DETAIL_INTERNAL_FAILURE)


def localize_skip_reason_for_display(reason: Any) -> str:
    """Normalize raw or saved skipped-file reasons to the active UI language."""
    reason_text = str(reason or "").strip()
    if not reason_text:
        return ""
    if "\n" in reason_text:
        localized_lines = [
            localize_skip_reason_for_display(line)
            for line in reason_text.splitlines()
            if line.strip()
        ]
        return "\n".join(dict.fromkeys(localized_lines))
    if reason_text.startswith(("ERR_", "INFO_")) and "|" in reason_text:
        return format_skip_reason(reason_text)
    if reason_text.startswith(RUST_MATCH_MARKER_EXCEL_PANIC):
        return format_excel_panic_reason(reason_text[len(RUST_MATCH_MARKER_EXCEL_PANIC) :])
    if re.match(r"^(?:xlsx?|xlsm|xlsb)\|range start index ", reason_text, re.IGNORECASE):
        return format_excel_panic_reason(reason_text)
    if _is_current_localized_skip_reason(reason_text):
        return reason_text

    normalized = reason_text.casefold()
    # Old sessions may contain a reason rendered in the other supported language.
    other_catalog = ENGLISH_STRINGS if get_language() == "ko" else get_korean_strings()
    saved_resource = _match_localized_skip_resource(reason_text, other_catalog)
    if saved_resource:
        _log_raw_skip_detail("saved-session", reason_text)
        return _render_saved_skip_resource(saved_resource, reason_text)
    if reason_text.startswith(("[오류]", "[안내]", "[건너뜀]", "[Error]", "[Info]", "[Skipped]")):
        _log_raw_skip_detail("saved-session", reason_text)
        return AppStrings.SKIP_REASON_UNKNOWN.format(AppStrings.SKIP_DETAIL_INTERNAL_FAILURE)
    if "json" in normalized:
        return AppStrings.ERROR_JSON_PARSE.format(_localize_json_error_detail(reason_text))
    if "xml" in normalized:
        return AppStrings.ERROR_XML_PARSE.format(_localize_xml_error_detail(reason_text))
    if any(token in normalized for token in ("permission", "access denied", "접근 거부", "접근이 거부")):
        return AppStrings.SKIP_REASON_OPEN.format(_localize_io_error_detail(reason_text))
    if any(token in normalized for token in ("read error", "os error", "i/o", "입출력")):
        return AppStrings.ERROR_IO_DURING_SEARCH.format(_localize_io_error_detail(reason_text))

    return AppStrings.SKIP_REASON_UNKNOWN.format(_localize_internal_error_detail("unclassified", reason_text))


def _rust_match_field(match: Any, index: int, attribute: str, default: Any = None) -> Any:
    """Read a Rust match from either the tuple ABI or the typed object ABI."""
    if isinstance(match, (tuple, list)):
        return match[index] if len(match) > index else default
    return getattr(match, attribute, default)


def _split_excel_match_content(content: str) -> Tuple[str, str, str]:
    """Excel 결과 ``시트\t셀\t값``을 세 칸으로 나눕니다."""
    parts = content.split("\t", 2)
    parts.extend([""] * (3 - len(parts)))
    return parts[0], parts[1], parts[2]


def _split_json_match_content(content: str) -> List[str]:
    """Decode explicit fields when a JSON path contains the tab delimiter."""
    if content.startswith("JSON_FIELDS|"):
        import json
        return cast(List[str], json.loads(content[len("JSON_FIELDS|"):]))
    return content.split("\t", 1)


def _normalize_rust_matches(
    matches: List[Any], 
    special_mode: Optional[str] = None, 
) -> Tuple[List[Any], int, List[Tuple[str, str]]]:
    """Rust 엔진의 반환 결과를 정규화된 매치 튜플 리스트, 바이너리 매치 카운트, 시트 스킵 목록으로 변환합니다."""
    res: List[Any] = []
    bin_cnt = 0
    sheet_skips: List[Tuple[str, str]] = []  # [(file_path, sheet_name), ...]
    # 일반 검색의 보통 일치(대부분의 결과)는 바로 담습니다. 일치가 많은 검색에서 결과 정리가
    # 검색 시간보다 오래 걸리던 문제를 줄입니다(검수 S-2). 표식(__SF_)이 있는 내용은 아래에서 처리합니다.
    plain_mode = not special_mode
    for m in matches:
        if plain_mode and getattr(m, "kind", None) == "match":
            content = m.content
            if not content.startswith("__SF_"):
                res.append((m.line, content, m.offset, m.length))
                continue
        # Rust 엔진은 (라인, 내용, 오프셋, 길이)를 반환합니다.
        c = _rust_match_field(m, 1, "content", "")
        marker_line = _rust_match_field(m, 0, "line")
        marker_offset = _rust_match_field(m, 2, "offset")
        marker_length = _rust_match_field(m, 3, "length")

        if c.startswith(RUST_MATCH_MARKER_LONG_LINE) and marker_line != 0 and marker_offset is None and marker_length is None:
            c = AppStrings.MSG_LONG_LINE_PREVIEW.format(c[len(RUST_MATCH_MARKER_LONG_LINE):])

        structured_kind = getattr(m, "kind", None)
        is_structured_match = structured_kind == "match"
        is_legacy_typed_marker = not isinstance(m, (tuple, list)) and c.startswith("__SF_")
        if (
            not is_structured_match
            and c.startswith("__SF_")
            and (marker_line == 0 or is_legacy_typed_marker)
        ):
            if c == RUST_MATCH_MARKER_TRUNCATED and marker_line == 0:
                res.append((-1, AppStrings.MSG_MATCH_LIMIT_PER_FILE.format(
                    _get_adv_setting(Constants.CONFIG_KEY_MAX_PER_FILE_MATCHES, Constants.DEFAULT_MAX_PER_FILE_MATCHES)
                ), None, None))
                continue
            if c.startswith((RUST_MATCH_MARKER_JSON_DEPTH_LIMIT, RUST_MATCH_MARKER_DECODE_PARTIAL)):
                # 파일 단위 부분 검색 안내로 별도 전달되며 결과 행에는 표시하지 않습니다.
                continue
            if c.startswith(RUST_MATCH_MARKER_LONG_LINE):
                c = AppStrings.MSG_LONG_LINE_PREVIEW.format(c[len("__SF_LONG_LINE__|"):])
            elif c.startswith(RUST_MATCH_MARKER_BINARY):
                try:
                    bin_cnt += int(c.split("|")[-1])
                except Exception:
                    bin_cnt += 1
                continue
            # EXCEL_SHEET_ERR 마커: 시트 스킵 정보를 추출하여 반환 목록에 추가
            elif c.startswith(RUST_MATCH_MARKER_EXCEL_SHEET_ERROR):
                payload = c[len(RUST_MATCH_MARKER_EXCEL_SHEET_ERROR):]
                if "|" in payload:
                    sheet_name, detail = payload.split("|", 1)
                else:
                    sheet_name, detail = payload, ""
                _log_raw_skip_detail(f"Excel sheet {sheet_name.strip()}", detail)
                sheet_skips.append((sheet_name.strip(), AppStrings.EXCEL_DETAIL_SHEET_FAILURE))
                continue
            else:
                continue

        try:
            line = _rust_match_field(m, 0, "line", 1)
            offset = _rust_match_field(m, 2, "offset")
            length = _rust_match_field(m, 3, "length")

            # Rust 엔진은 특수 모드(XML, JSON)에서
            # content 필드에 모든 필드를 \t로 합쳐서 반환함.
            # 앱의 검색 경로(폴더 검색)는 이 함수에서 직접 분리함.
            if special_mode:
                mode_up = str(special_mode).upper()

                if "XML" in mode_up and "\t" in c:
                    # 예) "/typesystem/ns/@name\tQtVideo" -> (line, "typesystem > ns > @name", "QtVideo", offset, length)
                    pts = c.split("\t", 1)
                    tag_path = pts[0].lstrip("/").replace("/", " > ")
                    val = pts[1] if len(pts) > 1 else ""
                    res.append((line, tag_path, val, offset, length))
                    continue

                if "JSON" in mode_up and ("\t" in c or c.startswith("JSON_FIELDS|")):
                    # 예) "/root/key\tvalue" -> (line, "root.key", "value", offset, length)
                    pts = _split_json_match_content(c)
                    json_path = json_path_for_display(pts[0])
                    val = pts[1] if len(pts) > 1 else ""
                    position = "" if line == 0 else line
                    res.append((position, json_path, val, offset, length))
                    continue

                if Constants.MODE_ARCHIVE.upper() in mode_up:
                    # 예) 'ARCHIVE_FIELDS|["NS","Key","원문","번역문"]' -> (위치, NS, Key, 원문, 번역문, offset, length)
                    archive_fields = _archive_fields_from_rust(c)
                    if archive_fields is not None:
                        res.append((line, *archive_fields, offset, length))
                        continue

                if "EXCEL" in mode_up:
                    # 예) "시트\t셀\t값" -> (line, sheet, cell, val, offset, length)
                    # set_matches Case1이 m[1]=sheet, m[2]=cell, m[3]=val로 매핑함
                    sheet, cell, val = _split_excel_match_content(str(c))
                    res.append((line, sheet, cell, val, offset, length))
                    continue

            # 기본: (line, content, offset, length)
            res.append((line, c, offset, length))
        except (IndexError, TypeError):
            res.append((getattr(m, "line", 1), c, getattr(m, "offset", None), getattr(m, "length", None)))

    return res, bin_cnt, sheet_skips


def _visible_match_count(matches: List[Any]) -> int:
    """내부 부분 검색 안내 행을 제외한 사용자 표시 매치 수를 반환합니다."""
    return sum(
        1
        for match in matches
        if isinstance(match, (tuple, list))
        and match
        and str(match[0]) not in {"-1", "-2"}
    )


def _metadata_items(matches: Any) -> List[Any]:
    """보통 일치를 뺀 항목(파일 단위 표식·오류)만 고릅니다.

    엔진의 보통 일치(kind == "match")는 표식이 아니므로 표식 검사에서 뺍니다. 그러면 일치가 많을 때 검사가
    빨라지고, 내용이 우연히 "ERR_…|"로 시작하는 실제 일치를 오류로 잘못 읽지도 않습니다(검수 S-2).
    """
    return [m for m in matches or [] if getattr(m, "kind", None) != "match"]


def _extract_marker_skip_reason(matches: Any) -> Optional[str]:
    """엑셀 마커 전용 결과뿐만 아니라, 모든 모드의 일반 오류를 스킵 사유로 추출합니다."""
    for m in _metadata_items(matches):
        structured_kind = getattr(m, "kind", None)
        structured_code = getattr(m, "code", None)
        structured_detail = getattr(m, "detail", None)
        if structured_kind == "error":
            if structured_code:
                if structured_code == "ERR_EXCEL_PANIC":
                    return format_excel_panic_reason(structured_detail)
                return format_skip_reason(_build_skip_reason(structured_code, structured_detail or ""))

        try:
            # m: (line, content, offset, length)
            content = str(_rust_match_field(m, 1, "content", ""))
        except (IndexError, TypeError):
            content = str(getattr(m, "content", ""))

        try:
            marker_line = _rust_match_field(m, 0, "line")
        except (IndexError, TypeError):
            marker_line = None
        # Current Rust output uses line 0 for metadata. Keep typed-object
        # results compatible with older extension builds that used line 1.
        if marker_line != 0 and isinstance(m, (tuple, list)):
            continue

        if content.startswith(RUST_MATCH_MARKER_EXCEL_PANIC):
            panic_detail = content[len(RUST_MATCH_MARKER_EXCEL_PANIC) :].strip() or "unknown"
            return format_excel_panic_reason(panic_detail)
        # 엑셀 시트 파싱 에러(sheet error)는 파일 전체를 건너뛰는 사유가 아닙니다.
        # 이 마커가 오더라도 다른 시트의 정상 매치 데이터가 존재할 수 있으므로 파일 전체를 스킵하지 않습니다.
        if content.startswith(RUST_MATCH_MARKER_EXCEL_SHEET_ERROR):
            continue
        # 일반 마커 처리
        if content.startswith("ERR_") and "|" in content:
            return format_skip_reason(content)
    return None


def json_path_for_display(raw_path: str) -> str:
    """엔진의 JSON 경로(`/a/b~1c`)를 화면 표기(`a.b/c`)로 바꿉니다(검수 F-4).

    엔진은 키 안의 `~`·`/`를 `~0`·`~1`로 적습니다. 그래서 키 `a/b`는 `a/b`로, 중첩 `a` → `b`는
    `a.b`로 구분되어 보이고, `/`로 시작하는 키도 글자가 사라지지 않습니다.
    """
    text = str(raw_path)
    if not text.startswith("/"):
        return text
    return ".".join(part.replace("~1", "/").replace("~0", "~") for part in text[1:].split("/"))


def _positive_limit(value: Any, key: str, default: int) -> int:
    """Return a safe positive limit from a marker or the active settings."""
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        try:
            return max(1, int(_get_adv_setting(key, default)))
        except (TypeError, ValueError):
            return default


def _extract_partial_skip_reason(matches: Any) -> Optional[str]:
    """Build one localized file notice for non-fatal result omissions."""
    reasons: List[str] = []
    failed_sheets: List[str] = []

    for match in _metadata_items(matches):
        line = _rust_match_field(match, 0, "line")
        content = str(_rust_match_field(match, 1, "content", ""))
        kind = getattr(match, "kind", None)
        code = getattr(match, "code", None)
        detail = getattr(match, "detail", None)

        is_tuple_metadata = isinstance(match, (tuple, list)) and line == 0
        if code == "EXCEL_SHEET_ERROR" or (
            (is_tuple_metadata or kind == "sheet_error")
            and content.startswith(RUST_MATCH_MARKER_EXCEL_SHEET_ERROR)
        ):
            payload = str(detail) if detail is not None else content[len(RUST_MATCH_MARKER_EXCEL_SHEET_ERROR):]
            sheet, _, raw_error = payload.partition("|")
            _log_raw_skip_detail(f"Excel sheet {sheet}", raw_error)
            failed_sheets.append(sheet)
            continue
        if kind == "truncated" or (is_tuple_metadata and content == RUST_MATCH_MARKER_TRUNCATED):
            limit = _positive_limit(
                detail,
                Constants.CONFIG_KEY_MAX_PER_FILE_MATCHES,
                Constants.DEFAULT_MAX_PER_FILE_MATCHES,
            )
            reasons.append(AppStrings.SKIP_REASON_FILE_MATCH_LIMIT.format(limit))
            continue

        if code == "DECODE_PARTIAL" or (
            is_tuple_metadata and content.startswith(RUST_MATCH_MARKER_DECODE_PARTIAL)
        ):
            encoding = str(detail) if detail is not None else content[len(RUST_MATCH_MARKER_DECODE_PARTIAL):]
            reasons.append(AppStrings.SKIP_REASON_DECODE_PARTIAL.format(encoding))
            continue

        if code == "JSON_DEPTH_LIMIT":
            limit = _positive_limit(
                detail,
                Constants.CONFIG_KEY_MAX_JSON_DEPTH,
                Constants.DEFAULT_MAX_JSON_DEPTH,
            )
            reasons.append(AppStrings.SKIP_REASON_JSON_DEPTH_LIMIT.format(limit))
            continue


        if kind == "partial":
            continue

        if is_tuple_metadata and content.startswith(RUST_MATCH_MARKER_JSON_DEPTH_LIMIT):
            limit = _positive_limit(
                content[len(RUST_MATCH_MARKER_JSON_DEPTH_LIMIT) :],
                Constants.CONFIG_KEY_MAX_JSON_DEPTH,
                Constants.DEFAULT_MAX_JSON_DEPTH,
            )
            reasons.append(AppStrings.SKIP_REASON_JSON_DEPTH_LIMIT.format(limit))
            continue


    if failed_sheets:
        reasons.append(AppStrings.SKIP_REASON_EXCEL_PARTIAL.format(", ".join(dict.fromkeys(failed_sheets))))
    unique_reasons = list(dict.fromkeys(reasons))
    return "\n".join(unique_reasons) if unique_reasons else None


def find_key_match_char(text: str, query: str, start_char: int = 0) -> Optional[int]:
    """미리보기용: 검색과 같은 비교 기준으로 ``text``의 ``start_char``번째 글자 이후 첫 일치 위치(글자 번호)입니다."""
    if not query or not HAS_RUST_ENGINE:
        return None
    return cast(Optional[int], sf_engine.find_key_match_char(text, query, max(0, int(start_char))))  # type: ignore


def find_key_match_spans(text: str, query: str, limit: int = 1000) -> List[Tuple[int, int]]:
    """강조용: 검색과 같은 비교 기준으로 일치 구간 (시작 글자 번호, 끝 글자 번호) 목록입니다(검수 F-3)."""
    if not query or not text or not HAS_RUST_ENGINE:
        return []
    spans = sf_engine.find_key_match_spans(text, query, max(0, int(limit)))  # type: ignore
    return [(int(start), int(start) + int(length)) for start, length in spans]


def detect_preview_encoding(file_path: str) -> str:
    """미리보기용: 검색 엔진과 같은 인코딩 판정(설정의 지정 인코딩, 없으면 파일 전체 기준 자동 판정)입니다."""
    selected = _get_adv_setting(Constants.CONFIG_KEY_SEARCH_ENCODING, "auto")
    # 같은 파일의 결과 행을 연달아 볼 때 파일 전체 판정을 반복하지 않도록, 경로·크기·수정 시각·
    # 설정이 같으면 이전 판정을 다시 씁니다(파일이 바뀌면 크기나 수정 시각이 달라져 새로 판정).
    stat = os.stat(file_path)
    cache_key = (os.path.normcase(os.path.abspath(file_path)), stat.st_size, stat.st_mtime_ns, selected)
    with _PREVIEW_ENCODING_LOCK:
        cached = _PREVIEW_ENCODING_CACHE.get(cache_key)
    if cached is not None:
        return cached
    detected = cast(str, sf_engine.detect_file_encoding(  # type: ignore
        str(file_path), None if selected == "auto" else selected))
    with _PREVIEW_ENCODING_LOCK:
        if len(_PREVIEW_ENCODING_CACHE) >= 256:
            _PREVIEW_ENCODING_CACHE.clear()
        _PREVIEW_ENCODING_CACHE[cache_key] = detected
    return detected


_PREVIEW_ENCODING_CACHE: Dict[Tuple[str, int, int, str], str] = {}
_PREVIEW_ENCODING_LOCK = threading.Lock()


def normalize_unicode(text: Any) -> str:
    """입력 텍스트를 NFC 유니코드 정규화 형식으로 변환하여 반환합니다."""
    if text is None:
        return ""
    return unicodedata.normalize("NFC", str(text))


def is_hidden_windows(path: str) -> bool:
    """Windows 전용: 파일 또는 폴더의 숨김 속성 여부를 반환합니다."""
    if os.name != "nt":
        return False
    try:
        attrs = ctypes.windll.kernel32.GetFileAttributesW(path)
        return attrs != -1 and bool(attrs & 0x02)
    except Exception:
        return False


def is_hidden_path(path: str) -> bool:
    """Match Rust directory traversal semantics for hidden paths on Windows.

    The Rust ``ignore`` walker treats dot-prefixed files and directories as
    hidden even on Windows, while the Win32 attribute check alone does not.
    Keep the Python-side path checks consistent with that rule.
    """
    name = os.path.basename(os.fspath(path))
    return name.startswith(".") and name not in {".", ".."} or is_hidden_windows(path)


def get_rust_mode_bits(special_mode: Optional[str], exclude_binary: bool = False) -> int:
    """검색 옵션에 따른 Rust 엔진 모드 비트 플래그를 생성하여 반환합니다."""
    bits = Constants.RUST_MODE_NORMAL
    if exclude_binary:
        bits |= Constants.RUST_MODE_EXCLUDE_BINARY
    if _get_adv_setting(Constants.CONFIG_KEY_ALLOW_DUPLICATE_JSON_KEYS, False):
        bits |= Constants.RUST_MODE_ALLOW_DUPLICATE_JSON_KEYS

    if not special_mode:
        return bits
    if Constants.MODE_XML in special_mode:
        bits |= Constants.RUST_MODE_XML
    if Constants.MODE_JSON in special_mode:
        bits |= Constants.RUST_MODE_JSON
    if Constants.MODE_ARCHIVE in special_mode:
        bits |= Constants.RUST_MODE_ARCHIVE

    if Constants.MODE_EXACT in special_mode:
        bits |= Constants.RUST_MODE_EXACT
    return bits


def _archive_fields_from_rust(content: str) -> Optional[List[str]]:
    """Rust의 ``ARCHIVE_FIELDS|[Namespace, Key, 원문, 번역문]`` 결과를 네 칸으로 나눕니다."""
    if not content.startswith(RUST_MATCH_MARKER_ARCHIVE_FIELDS):
        return None
    import json

    try:
        fields = json.loads(content[len(RUST_MATCH_MARKER_ARCHIVE_FIELDS):])
    except (TypeError, ValueError):
        return None
    if not isinstance(fields, list) or len(fields) != 4 or not all(isinstance(value, str) for value in fields):
        return None
    return fields


def format_file_matches(
    path: str,
    matches: Any,
    special_mode: Optional[str] = None,
) -> Tuple[Optional[SearchResult], List[Tuple[str, str]], List[Tuple[str, str]]]:
    """Rust 엔진이 돌려준 파일 하나의 결과를 화면용 결과로 정리합니다.

    폴더 검색과 워커의 실시간 결과 처리가 같은 규칙을 쓰도록 한곳에 둡니다.

    돌려주는 값:
    - 결과: ``(경로, 표시 일치 수, 일치 목록)`` 또는 표시할 일치가 없으면 ``None``
    - 건너뜀 목록: 파일 전체를 건너뛴 사유 또는 일부만 검색한 안내 ``[(경로, 사유)]``
    - 시트 실패 목록: Excel에서 읽지 못한 시트 ``[(시트, 사유)]``
    """
    skip_reason = _extract_marker_skip_reason(matches)
    if skip_reason:
        return None, [(path, skip_reason)], []
    skipped: List[Tuple[str, str]] = []
    partial_reason = _extract_partial_skip_reason(matches)
    if partial_reason:
        skipped.append((path, partial_reason))
    match_tuples, binary_count, sheet_skips = _normalize_rust_matches(matches, special_mode)
    if binary_count > 0:
        binary_row = (1, AppStrings.MSG_BINARY_MATCH.format(binary_count), None, None)
        return (path, binary_count, [binary_row]), skipped, sheet_skips
    if match_tuples:
        return (path, _visible_match_count(match_tuples), match_tuples), skipped, sheet_skips
    return None, skipped, sheet_skips


def _deduplicate_overlapping_roots(search_paths: List[str]) -> List[str]:
    """Drop duplicate roots and descendants already covered by a parent root."""
    candidates = [
        (path, os.path.normcase(os.path.realpath(os.path.abspath(path))))
        for path in search_paths
    ]
    canonical_roots = {canonical for _path, canonical in candidates}
    seen: set[str] = set()
    unique_roots: List[str] = []
    for original, canonical in candidates:
        covered = False
        for parent in canonical_roots:
            if parent == canonical:
                continue
            if os.path.splitdrive(parent)[0] != os.path.splitdrive(canonical)[0]:
                continue
            try:
                if (
                    len(parent) < len(canonical)
                    and os.path.commonpath((parent, canonical)) == parent
                ):
                    covered = True
                    break
            except ValueError:
                continue
        if not covered and canonical not in seen:
            seen.add(canonical)
            unique_roots.append(original)
    return unique_roots


def search_directory_fast(
    search_paths: List[str],
    search_string: str,
    extensions: Optional[List[str]] = None,
    stop_event=None,
    progress_callback=None,
    **kwargs,
) -> Dict[str, List]:
    """Rust 엔진을 사용하여 디렉토리를 고속으로 검색합니다."""
    validate_search_query(search_string)
    try:
        excluded_roots = []
        if _get_adv_setting(Constants.CONFIG_KEY_INCLUDE_JUNCTIONS, False) is not True:
            included_paths = []
            for path in search_paths:
                if os.path.isjunction(path):
                    if (path, AppStrings.SKIP_ROOT_JUNCTION) not in excluded_roots:
                        excluded_roots.append((path, AppStrings.SKIP_ROOT_JUNCTION))
                else:
                    included_paths.append(path)
            search_paths = included_paths
        if not search_paths:
            return {"results": [], "skipped": excluded_roots}
        search_paths = _deduplicate_overlapping_roots(search_paths)
        rust_pattern = normalize_unicode(search_string)
        rust_exts = None
        if extensions:
            rust_exts = [ext.lstrip(".").lower() for ext in extensions]
        filename_filter = kwargs.get("filename_filter")
        rust_fn_filters = None
        if filename_filter:
            if isinstance(filename_filter, str):
                rust_fn_filters = [f.strip() for f in filename_filter.split(",") if f.strip()]
            else:
                rust_fn_filters = filename_filter
        exclude_hidden = bool(kwargs.get("exclude_hidden", False))
        search_dir_func = getattr(sf_engine, "search_dir", None)
        if not search_dir_func:
            logger.error(AppStrings.LOG_SYS_SF_ENGINE_NOT_FOUND.format("sf_engine.search_dir"))  # type: ignore
            # A missing Rust API is an engine error, not a Python retry.
            raise AttributeError(AppStrings.LOG_SYS_SF_ENGINE_NOT_FOUND.format("sf_engine.search_dir"))  # type: ignore
        exclude_binary = bool(kwargs.get("exclude_binary", True))
        mode_bits = get_rust_mode_bits(kwargs.get("special_mode"), exclude_binary=exclude_binary)
        max_per_file, max_json_depth, max_json_size = _get_rust_search_limits()
        rust_options = _build_rust_options(
            extensions=rust_exts,
            mode_bits=mode_bits,
            filename_filter=rust_fn_filters,
            exclude_hidden=exclude_hidden,
            stop_event=stop_event,
            progress_callback=progress_callback,
            results_callback=kwargs.get("results_callback"),
            batch_size=kwargs.get("batch_size", Constants.RUST_RESULT_BATCH_SIZE),
            flush_ms=kwargs.get("flush_ms", Constants.RUST_RESULT_FLUSH_MS),
            max_per_file=max_per_file,
            max_json_depth=max_json_depth,
            max_json_size=max_json_size,
            structured_memory_budget=_get_structured_memory_budget(),
            max_total_matches=_positive_int_or_none(kwargs.get("max_total_matches")),
        )
        if rust_options is not None:
            raw_ret = search_dir_func(root_paths=search_paths, pattern=rust_pattern, options=rust_options)
        else:
            raw_ret = search_dir_func(
                root_paths=search_paths,
                pattern=rust_pattern,
                extensions=rust_exts,
                mode_bits=mode_bits,
                filename_filter=rust_fn_filters,
                exclude_hidden=exclude_hidden,
                stop_event=stop_event,
                progress_callback=progress_callback,
                results_callback=kwargs.get("results_callback"),
                batch_size=kwargs.get("batch_size", Constants.RUST_RESULT_BATCH_SIZE),
                _flush_ms=kwargs.get("flush_ms", Constants.RUST_RESULT_FLUSH_MS),
                max_per_file=max_per_file,
                max_json_depth=max_json_depth,
                max_json_size=max_json_size,
                options=_build_rust_options(),
            )
        formatted_results = []
        skipped_results = list(excluded_roots)
        if raw_ret:
            matches_list, skipped_list = raw_ret
            # [Optim] results_callback이 제공된 경우, 결과는 이미 실시간으로 처리되었으므로
            # 여기서 전체 목록을 다시 정규화하는 중복 연산을 건너뜁니다.
            if not kwargs.get("results_callback"):
                for path, matches in matches_list:
                    result, file_skips, _sheet_skips = format_file_matches(
                        path, matches, kwargs.get("special_mode"))
                    skipped_results.extend(file_skips)
                    if result is not None:
                        formatted_results.append(result)

            for path, reason in skipped_list:
                skipped_results.append((path, format_skip_reason(reason)))
        return {"results": formatted_results, "skipped": skipped_results}
    except (IOError, OSError, RuntimeError) as e:
        logger.error(AppStrings.LOG_SCH_RUST_DIR_SEARCH_ERROR.format("SearchEngine.Directory", e))
        raise
    except BaseException as e:
        logger.critical(AppStrings.LOG_SCH_RUST_DIR_FATAL.format("SearchEngine.Directory", e), exc_info=True)
        raise
