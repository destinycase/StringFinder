import ctypes
from contextlib import contextmanager
from contextvars import ContextVar
import logging
import os
import re
import time
import unicodedata
from os.path import splitext
from typing import Any, Dict, List, Literal, Optional, Tuple, Union, cast

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
    SKIP_CODE_DECODING,
    SKIP_CODE_ENCODING,
    SKIP_CODE_EXCEL_PANIC,
    SKIP_CODE_EXCEL_PROCESS,
    SKIP_CODE_ARCHIVE_FORMAT,
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


def _max_search_file_size_bytes() -> int:
    """Return the configured per-file size cap, bounded to the supported 1 GiB range."""
    try:
        configured_mb = int(
            _get_adv_setting(
                Constants.CONFIG_KEY_MAX_SEARCH_FILE_SIZE_MB,
                Constants.DEFAULT_MAX_SEARCH_FILE_SIZE_MB,
            )
        )
    except (TypeError, ValueError):
        configured_mb = Constants.DEFAULT_MAX_SEARCH_FILE_SIZE_MB
    configured_mb = min(
        max(1, configured_mb),
        Constants.SETTING_MAX_SEARCH_FILE_SIZE_MB,
    )
    return configured_mb * 1024 * 1024


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
    """Build the named Rust options object when the new extension is available.

    Older locally installed extensions do not expose ``SearchOptions`` yet.
    Returning ``None`` lets callers use the plain keyword arguments while a
    rebuilt extension is rolled out.
    """
    options_type = getattr(sf_engine, "SearchOptions", None)
    if options_type is None:
        return None
    include_junctions = _get_adv_setting(Constants.CONFIG_KEY_INCLUDE_JUNCTIONS, False) is True
    encoding = _get_adv_setting(Constants.CONFIG_KEY_SEARCH_ENCODING, "auto")
    if hasattr(options_type, "encoding"):
        values.setdefault("include_junctions", include_junctions)
        values.setdefault("encoding", encoding)
    elif include_junctions or encoding != "auto":
        raise RuntimeError("The Rust engine must be rebuilt to support junction and encoding options")
    return options_type(**values)


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


def _memory_guard_result(
    file_path: str,
    document_kind: Literal["json", "xml"],
    engine_kind: Literal["rust", "python"],
) -> Optional[Tuple[str, str]]:
    """Prevent an unsafe structured parse without conflating file and system limits."""
    try:
        from sf_utils.resource_guard import (
            estimate_structured_memory_bytes,
            memory_pressure_detected,
            memory_pressure_message,
            memory_snapshot,
            projected_memory_pressure_reason,
        )

        snapshot = memory_snapshot()
        if memory_pressure_detected(snapshot):
            logger.warning(
                "Structured search skipped for %s due to memory pressure (%s)",
                file_path,
                memory_pressure_message(snapshot),
            )
            return (Constants.STATUS_SKIPPED, AppStrings.ERROR_MEMORY_CRITICAL)
        try:
            file_size = os.path.getsize(file_path)
        except OSError:
            # The regular open/read path provides the localized I/O reason.
            return None
        if file_size > _max_search_file_size_bytes():
            # Let the established parser-size check return its more specific
            # file-size reason before applying a projected-memory reason.
            return None
        estimate = estimate_structured_memory_bytes(file_size, document_kind, engine_kind)
        projection_reason = projected_memory_pressure_reason(snapshot, estimate)
        if projection_reason:
            logger.warning(
                "Structured search skipped for %s due to projected memory budget "
                "(engine=%s, document=%s, estimate=%dMB, reason=%s, %s)",
                file_path,
                engine_kind,
                document_kind,
                estimate // (1024 * 1024),
                projection_reason,
                memory_pressure_message(snapshot),
            )
            return (
                Constants.STATUS_SKIPPED,
                format_skip_reason(_build_skip_reason(SKIP_CODE_RESOURCE_BUDGET, estimate)),
            )
    except Exception as exc:
        # Resource checks must never make an otherwise readable file fail.
        logger.debug("Memory preflight check failed for %s: %s", file_path, exc)
    return None

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
FileInfo = Tuple[str, int]


RUST_MATCH_MARKER_BINARY = "__SF_BINARY_MATCH__|"
RUST_MATCH_MARKER_LONG_LINE = "__SF_LONG_LINE__|"
RUST_MATCH_MARKER_TRUNCATED = "__SF_TRUNCATED__"
RUST_MATCH_MARKER_JSON_DEPTH_LIMIT = "__SF_JSON_DEPTH_LIMIT__|"
# Rust UE 현지화 archive 결과 표식입니다. 뒤에 [Namespace, Key, 원문, 번역문] JSON 배열이 옵니다.
RUST_MATCH_MARKER_ARCHIVE_FIELDS = "ARCHIVE_FIELDS|"
RUST_MATCH_MARKER_EXCEL_SHEET_ERROR = "__SF_EXCEL_SHEET_ERR__|"
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


def _known_rust_skip_reason(error: Any) -> Optional[str]:
    """Rust가 ``코드|세부`` 형식으로 보낸 건너뜀 사유(파일 크기 초과 등)이면 번역한 문구를 돌려줍니다."""
    raw = str(error)
    code, separator, _detail = raw.partition("|")
    if separator and code in _SKIP_REASON_TEMPLATE_NAMES:
        return format_skip_reason(raw)
    return None


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
    """Split current tab-delimited and legacy pipe-delimited Excel results."""
    if "\t" in content:
        parts = content.split("\t", 2)
    elif " | " in content:
        # The old protocol did not escape pipes in sheet names. Splitting from
        # the right preserves such names whenever cell and value are present.
        parts = content.rsplit(" | ", 2)
    else:
        parts = [content]
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
    for m in matches:
        try:
            # Rust 엔진은 (라인, 내용, 오프셋, 길이)를 반환합니다.
            c = _rust_match_field(m, 1, "content", "")
        except (IndexError, TypeError):
            # 이전 객체 스타일 매치에 대한 폴백 처리입니다.
            c = getattr(m, "content", "")

        # 내부 마커 처리 (LONG_LINE 포함)
        try:
            marker_line = _rust_match_field(m, 0, "line")
            marker_offset = _rust_match_field(m, 2, "offset")
            marker_length = _rust_match_field(m, 3, "length")
        except (IndexError, TypeError):
            marker_line, marker_offset, marker_length = None, None, None

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
            if c.startswith(RUST_MATCH_MARKER_JSON_DEPTH_LIMIT):
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
            elif c.startswith("ERR_") and "|" in c:
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
            # search_directory_fast 경로에서는 search_in_xml_special 등이
            # 호출되지 않으므로 이 함수에서 직접 분리해야 함.
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
                    json_path = pts[0].lstrip("/").replace("/", ".")
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


def _extract_marker_skip_reason(matches: Any) -> Optional[str]:
    """엑셀 마커 전용 결과뿐만 아니라, 모든 모드의 일반 오류를 스킵 사유로 추출합니다."""
    for m in matches:
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

    for match in matches or []:
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


def _has_recycle_bin_component(path: str) -> bool:
    return any(
        component.casefold() == "$recycle.bin"
        for component in re.split(r"[\\/]+", os.fspath(path))
        if component
    )


def is_recycle_bin_path(path: str) -> bool:
    """Return whether a file belongs to the Windows recycle bin or its root alias."""
    if _has_recycle_bin_component(path):
        return True
    if os.name != "nt":
        return False

    normalized = os.path.normpath(path)
    parent = os.path.dirname(normalized)
    drive, tail = os.path.splitdrive(parent)
    parent_parts = [part for part in re.split(r"[\\/]+", tail) if part]
    if not drive or len(parent_parts) != 1:
        return False

    alias_target = os.path.join(f"{drive}{os.sep}", "$RECYCLE.BIN", parent_parts[0])
    if not os.path.isdir(alias_target):
        return False
    try:
        return _has_recycle_bin_component(os.path.realpath(path))
    except OSError:
        return False


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


def detect_encoding_quickly(data: bytes) -> str:
    """바이트 데이터의 BOM 및 패턴 기반 빠른 인코딩 감지를 수행합니다."""
    requested = _get_adv_setting(Constants.CONFIG_KEY_SEARCH_ENCODING, "auto")
    if requested in ("utf-8", "cp949", "utf-16-le", "utf-16-be"):
        for bom, declared in ((b"\xef\xbb\xbf", "utf-8"), (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")):
            if data.startswith(bom) and requested != declared:
                raise UnicodeError("File BOM conflicts with the selected encoding")
        if requested == "utf-8" and data.startswith(b"\xef\xbb\xbf"):
            return "utf-8-sig"
        if requested.startswith("utf-16") and data.startswith(b"\xff\xfe" if requested.endswith("le") else b"\xfe\xff"):
            return "utf-16"
        return cast(str, requested)
    if not data:
        return Constants.ENC_UTF8
    if data.startswith(b"\xef\xbb\xbf"):
        return Constants.ENC_UTF8_SIG
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        # utf-16 사용 시 BOM에 따라 LE/BE 자동 감지 및 BOM 제거 수행
        return "utf-16"
    
    # UTF-16 No-BOM 휴리스틱 감지
    # 1. 시그니처 기반 (JSON/XML 등)
    if len(data) >= 2:
        if data.startswith(b"\x00{") or data.startswith(b"\x00<") or data.startswith(b"\x00["):
            return "utf-16-be"
        if data[0:2] in [b"{\x00", b"<\x00", b"[\x00"]:
            return "utf-16-le"
            
    # 2. 분포 기반 (NUL 바이트가 한쪽 인덱스에 압도적으로 많은지 확인)
    # Share the Rust detector's NUL-distribution thresholds, including short files.
    if len(data) >= 4:
        sample = data[:min(len(data), 2048)]
        even_nuls = sum(1 for i in range(0, len(sample), 2) if sample[i] == 0)
        odd_nuls = sum(1 for i in range(1, len(sample), 2) if sample[i] == 0)
        total_pairs = len(sample) // 2
        if total_pairs >= 2:
            if even_nuls > total_pairs * 0.7 and odd_nuls <= total_pairs * 0.1:
                return "utf-16-be"
            if odd_nuls > total_pairs * 0.7 and even_nuls <= total_pairs * 0.1:
                return "utf-16-le"

    try:
        data.decode(Constants.ENC_UTF8)
        return Constants.ENC_UTF8
    except UnicodeDecodeError:
        pass
    # A long Korean UTF-16 prefix need not have many NULs. Accept only an
    # unambiguous, strictly decoded Hangul-dominant sample; never override UTF-8.
    sample = data[:2048]
    sample = sample[:len(sample) // 2 * 2]
    candidates = []
    for encoding in ("utf-16-le", "utf-16-be"):
        try:
            decoded = sample.decode(encoding, errors="strict")
        except UnicodeDecodeError:
            continue
        if len(decoded) >= 16 and sum("\uac00" <= c <= "\ud7a3" for c in decoded) >= len(decoded) * 0.8:
            candidates.append(encoding)
    if len(candidates) == 1:
        try:
            sample.decode(Constants.ENC_CP949, errors="strict")
        except UnicodeDecodeError:
            return candidates[0]
        # Valid CP949 Korean bytes can also look like Hangul in UTF-16 BE.
        # Preserve the existing interpretation when both encodings are valid.
    # [L-03 Fix] EUC-KR 검사 제거 — CP949가 EUC-KR의 상위집합이므로 중복 처리 불필요
    try:
        decoded_cp = data.decode(Constants.ENC_CP949)
        if re.search("[\uac00-\ud7a3]", decoded_cp):
            return Constants.ENC_CP949
    except UnicodeDecodeError:
        pass
    return Constants.ENC_UTF8


def _check_excel_signature(file_path: str) -> Tuple[bool, Optional[str]]:
    """엑셀 파일의 이진 시그니처가 올바른지 확인합니다."""
    try:
        with open(file_path, "rb") as f:
            header = f.read(8)
            if header.startswith(b"\xd0\xcf\x11\xe0"):
                return True, None
            if header.startswith(b"PK\x03\x04"):
                return True, None
            return False, AppStrings.ERROR_EXCEL_SIGNATURE
    except (IOError, OSError, PermissionError) as e:
        return False, AppStrings.ERROR_EXCEL_ACCESS.format(_localize_io_error_detail(e))
    except Exception as e:
        logger.debug(AppStrings.ERROR_EXCEL_PROCESS.format(e))
        return False, AppStrings.ERROR_EXCEL_PROCESS.format(
            _localize_internal_error_detail("Excel signature", e)
        )


def search_in_excel_special(
    file_path: str,
    search_string: str,
    exact_match: bool = False,
    stop_event=None,
) -> Optional[Union[SearchResult, SkippedResult]]:
    """엑셀 파일 내의 텍스트를 검색하는 특수 엔진을 실행합니다."""
    validate_search_query(search_string)
    signature_ok, signature_error = _check_excel_signature(file_path)
    if not signature_ok:
        return (Constants.STATUS_SKIPPED, signature_error or AppStrings.ERROR_EXCEL_SIGNATURE)
    try:
        mode_bits = Constants.RUST_MODE_NORMAL | Constants.RUST_MODE_EXCEL
        if exact_match:
            mode_bits |= Constants.RUST_MODE_EXACT
        max_per_file, max_json_depth, max_json_size = _get_rust_search_limits()
        results = sf_engine.search_file(  # type: ignore
            str(file_path),
            search_string,
            mode_bits,
            max_per_file=max_per_file,
            max_json_depth=max_json_depth,
            max_json_size=max_json_size,
            **_rust_policy_kwargs(),
        )
        if results:
            error_reason = _extract_marker_skip_reason(results)
            if error_reason:
                return (Constants.STATUS_SKIPPED, error_reason)
            partial_reason = _extract_partial_skip_reason(results)
            processed, _binary_count, sheet_skips = _normalize_rust_matches(
                results,
                Constants.MODE_EXCEL,
            )
            sheet_errors = [
                AppStrings.ERROR_SEARCH_EXCEL_SHEET.format(sheet_name, detail)
                for sheet_name, detail in sheet_skips
            ]
            for sheet_error in sheet_errors:
                logger.warning("[%s] %s", file_path, sheet_error)
            if processed:
                if partial_reason:
                    processed.append((-2, partial_reason, "", ""))
                return (file_path, _visible_match_count(processed), processed)
            if sheet_errors:
                return (Constants.STATUS_SKIPPED, sheet_errors[0])
        # Rust가 결과를 내지 않으면 결과가 없는 것입니다(다른 검색 경로는 없음).
        return None
    except Exception as e:
        logger.error(AppStrings.LOG_SCH_RUST_EXCEL_FAIL.format(file_path, e))
        known_reason = _known_rust_skip_reason(e)
        if known_reason:
            return (Constants.STATUS_SKIPPED, known_reason)
        raw_reason = str(e)
        if raw_reason.startswith((f"{SKIP_CODE_EXCEL_PROCESS}|", f"{SKIP_CODE_EXCEL_PANIC}|")):
            return (Constants.STATUS_SKIPPED, format_skip_reason(raw_reason))
        return (
            Constants.STATUS_SKIPPED,
            AppStrings.ERROR_SEARCH_EXCEL.format(
                file_path,
                _localize_internal_error_detail("Excel", e),
            ),
        )
    return None


def search_in_json_special(
    file_path: str,
    search_string: str,
    exact_match: bool = False,
    stop_event=None,
    _rust_only: bool = False,
) -> Optional[Union[SearchResult, SkippedResult]]:
    """
    JSON 특수 검색을 수행합니다.
    """
    validate_search_query(search_string)
    initial_engine: Literal["rust", "python"] = "rust"
    memory_result = _memory_guard_result(file_path, "json", initial_engine)
    if memory_result:
        return memory_result
    try:
        mode_bits = Constants.RUST_MODE_JSON
        if _get_adv_setting(Constants.CONFIG_KEY_ALLOW_DUPLICATE_JSON_KEYS, False):
            mode_bits |= Constants.RUST_MODE_ALLOW_DUPLICATE_JSON_KEYS
        if exact_match:
            mode_bits |= Constants.RUST_MODE_EXACT
        max_per_file, max_json_depth, max_json_size = _get_rust_search_limits()
        results = sf_engine.search_file(  # type: ignore
            str(file_path),
            search_string,
            mode_bits,
            max_per_file=max_per_file,
            max_json_depth=max_json_depth,
            max_json_size=max_json_size,
            **_rust_policy_kwargs(),
        )
        if results:
            skip_reason = _extract_marker_skip_reason(results)
            if skip_reason:
                return (Constants.STATUS_SKIPPED, skip_reason)
            partial_reason = _extract_partial_skip_reason(results)
            processed: List[SearchMatch] = []
            for m in results:
                content = _rust_match_field(m, 1, "content", "")
                line = _rust_match_field(m, 0, "line", 1)
                if content == RUST_MATCH_MARKER_TRUNCATED and line == 0:
                    processed.append((-1, AppStrings.MSG_MATCH_LIMIT_PER_FILE.format(
                        _get_adv_setting(Constants.CONFIG_KEY_MAX_PER_FILE_MATCHES, Constants.DEFAULT_MAX_PER_FILE_MATCHES)
                    ), "", ""))
                    continue
                if line == 0 and content.startswith(RUST_MATCH_MARKER_JSON_DEPTH_LIMIT):
                    continue
                if line == 0:
                    line = ""
                parts = _split_json_match_content(content)
                # JSON 경로 정규화: /를 .으로 변경하고 시작 / 문자를 제거합니다.
                json_path = parts[0].lstrip("/").replace("/", ".")
                val = parts[1] if len(parts) > 1 else ""
                processed.append((line, json_path, val, _rust_match_field(m, 2, "offset"), _rust_match_field(m, 3, "length")))
            if partial_reason:
                processed.append((-2, partial_reason, "", None, None))
            return (file_path, _visible_match_count(processed), processed)
        if _rust_only:
            return None
    except Exception as e:
        logger.error(AppStrings.LOG_SCH_RUST_JSON_FAIL.format(file_path, e))
        known_reason = _known_rust_skip_reason(e)
        if known_reason:
            return (Constants.STATUS_SKIPPED, known_reason)
        # [Policy] 자동 폴백 중단
        reason = str(e)
        if reason.startswith((f"{SKIP_CODE_JSON_PARSE}|", f"{SKIP_CODE_DECODING}|", f"{SKIP_CODE_ENCODING}|")):
            return (Constants.STATUS_SKIPPED, format_skip_reason(reason))
        return (
            Constants.STATUS_SKIPPED,
            AppStrings.ERROR_JSON_PARSE.format(_localize_json_error_detail(reason)),
        )
    return None


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


def search_in_archive_special(
    file_path: str,
    search_string: str,
    exact_match: bool = False,
    stop_event=None,
) -> Optional[Union[SearchResult, SkippedResult]]:
    """UE 현지화 archive의 원문·번역문·Key 값을 검색합니다.

    결과 행은 ``(위치, Namespace, Key, 원문, 번역문, offset, length)``입니다.
    archive의 구조 필드는 하나씩만 있어야 하므로 JSON 중복 키 설정과 관계없이 중복 키를 허용하지 않습니다.
    """
    validate_search_query(search_string)
    initial_engine: Literal["rust", "python"] = "rust"
    memory_result = _memory_guard_result(file_path, "json", initial_engine)
    if memory_result:
        return memory_result
    max_per_file = _positive_limit(
        None,
        Constants.CONFIG_KEY_MAX_PER_FILE_MATCHES,
        Constants.DEFAULT_MAX_PER_FILE_MATCHES,
    )
    limit_row = (-1, AppStrings.MSG_MATCH_LIMIT_PER_FILE.format(max_per_file), "", "", "", None, None)
    try:
        mode_bits = Constants.RUST_MODE_ARCHIVE
        if exact_match:
            mode_bits |= Constants.RUST_MODE_EXACT
        rust_max_per_file, max_json_depth, max_json_size = _get_rust_search_limits()
        results = sf_engine.search_file(  # type: ignore
            str(file_path),
            search_string,
            mode_bits,
            max_per_file=rust_max_per_file,
            max_json_depth=max_json_depth,
            max_json_size=max_json_size,
            **_rust_policy_kwargs(),
        )
    except Exception as e:
        logger.error(AppStrings.LOG_SCH_RUST_ARCHIVE_FAIL.format(file_path, e))
        known_reason = _known_rust_skip_reason(e)
        if known_reason:
            return (Constants.STATUS_SKIPPED, known_reason)
        reason = str(e)
        if reason.startswith((
            f"{SKIP_CODE_ARCHIVE_FORMAT}|",
            f"{SKIP_CODE_JSON_PARSE}|",
            f"{SKIP_CODE_DECODING}|",
            f"{SKIP_CODE_ENCODING}|",
        )):
            return (Constants.STATUS_SKIPPED, format_skip_reason(reason))
        return (
            Constants.STATUS_SKIPPED,
            AppStrings.ERROR_UNEXPECTED_FILE.format(
                file_path, _localize_internal_error_detail("UE localization archive", e)
            ),
        )
    if not results:
        return None
    skip_reason = _extract_marker_skip_reason(results)
    if skip_reason:
        return (Constants.STATUS_SKIPPED, skip_reason)
    processed: List[Any] = []
    for m in results:
        content = str(_rust_match_field(m, 1, "content", ""))
        line = _rust_match_field(m, 0, "line", 0)
        if content == RUST_MATCH_MARKER_TRUNCATED and line == 0:
            processed.append(limit_row)
            continue
        fields = _archive_fields_from_rust(content)
        if fields is None:
            continue
        processed.append((line, *fields, None, None))
    if not processed:
        return None
    return (file_path, _visible_match_count(processed), processed)


def search_in_xml_special(
    file_path: str,
    search_string: str,
    exact_match: bool = False,
    stop_event=None,
) -> Optional[Union[SearchResult, SkippedResult]]:
    """
    XML 특수 검색을 수행합니다.
    """
    validate_search_query(search_string)
    initial_engine: Literal["rust", "python"] = "rust"
    memory_result = _memory_guard_result(file_path, "xml", initial_engine)
    if memory_result:
        return memory_result
    try:
        mode_bits = Constants.RUST_MODE_XML
        if exact_match:
            mode_bits |= Constants.RUST_MODE_EXACT
        max_per_file, max_json_depth, max_json_size = _get_rust_search_limits()
        results = sf_engine.search_file(  # type: ignore
            str(file_path),
            search_string,
            mode_bits,
            max_per_file=max_per_file,
            max_json_depth=max_json_depth,
            max_json_size=max_json_size,
            **_rust_policy_kwargs(),
        )
        if results:
            skip_reason = _extract_marker_skip_reason(results)
            if skip_reason:
                return (Constants.STATUS_SKIPPED, skip_reason)
            processed, _, _ = _normalize_rust_matches(results, Constants.MODE_XML)
            partial_reason = _extract_partial_skip_reason(results)
            if partial_reason:
                processed.append((-2, partial_reason, None, None))
            return (file_path, _visible_match_count(processed), processed)
    except Exception as e:
        logger.error(AppStrings.LOG_SCH_RUST_XML_FAIL.format(file_path, e))
        known_reason = _known_rust_skip_reason(e)
        if known_reason:
            return (Constants.STATUS_SKIPPED, known_reason)
        # [Policy] 자동 폴백 중단
        reason = str(e)
        if reason.startswith((f"{SKIP_CODE_XML_PARSE}|", f"{SKIP_CODE_XML_UNSUPPORTED_DTD}|", f"{SKIP_CODE_DECODING}|", f"{SKIP_CODE_ENCODING}|")):
            return (Constants.STATUS_SKIPPED, format_skip_reason(reason))
        return (
            Constants.STATUS_SKIPPED,
            format_skip_reason(_build_skip_reason(SKIP_CODE_XML_PARSE, reason)),
        )
    return None


def search_in_file(
    file_path: str,
    search_string: str,
    file_size: Optional[int] = None,
    special_mode: Optional[str] = None,
    stop_event=None,
    **kwargs,
) -> Optional[Union[SearchResult, SkippedResult]]:
    """파일 하나를 Rust 엔진으로 검색합니다. Excel·값만 검색 형식은 해당 경로로 보냅니다.

    검색 방식은 하나입니다(모든 형식이 Rust의 같은 비교 기준을 씀). Rust가 결과를 내지 않으면
    결과가 없는 것이며, 다른 인코딩으로 다시 검색하는 Python 대체 경로는 없습니다(CP949·UTF-16 등은
    Rust가 직접 디코딩).
    """
    validate_search_query(search_string)
    if is_recycle_bin_path(file_path):
        return None
    search_string_nfc = normalize_unicode(search_string)
    ext = splitext(file_path)[1].lower()
    if ext in EXCEL_EXTS:
        is_exact = bool(special_mode and Constants.MODE_EXACT in special_mode)
        return search_in_excel_special(file_path, search_string_nfc, is_exact, stop_event=stop_event)
    if special_mode:
        is_exact = Constants.MODE_EXACT in special_mode
        if Constants.MODE_XML in special_mode and ext in (".xml", ".sf_xml"):
            return search_in_xml_special(file_path, search_string_nfc, is_exact, stop_event=stop_event)
        elif Constants.MODE_JSON in special_mode and ext == ".json":
            return search_in_json_special(file_path, search_string_nfc, is_exact, stop_event=stop_event)
        elif Constants.MODE_ARCHIVE in special_mode and ext == Constants.EXT_ARCHIVE:
            return search_in_archive_special(file_path, search_string_nfc, is_exact, stop_event=stop_event)
    if file_size is None:
        try:
            file_size = os.path.getsize(file_path)
        except (OSError, IOError) as e:
            logger.debug(AppStrings.LOG_SCH_BINARY_CHECK_ERROR.format(file_path, e))
            return (
                Constants.STATUS_SKIPPED,
                AppStrings.ERROR_FILE_ACCESS_BINARY.format(_localize_io_error_detail(e)),
            )
    if file_size == 0:
        return (Constants.STATUS_SKIPPED, AppStrings.SKIP_EMPTY_FILE)
    if not HAS_RUST_ENGINE:
        return (
            Constants.STATUS_SKIPPED,
            AppStrings.ERROR_UNEXPECTED_FILE.format(
                file_path, _localize_internal_error_detail("Rust file search", _RUST_ENGINE_ERROR)
            ),
        )
    try:
        exclude_binary = bool(kwargs.get("exclude_binary", False))
        mode_bits = get_rust_mode_bits(special_mode, exclude_binary=exclude_binary)
        started = time.time()
        max_per_file, max_json_depth, max_json_size = _get_rust_search_limits()
        rust_results = sf_engine.search_file(  # type: ignore
            str(file_path),
            search_string_nfc,
            mode_bits,
            stop_event=stop_event,
            max_per_file=max_per_file,
            max_json_depth=max_json_depth,
            max_json_size=max_json_size,
            **_rust_policy_kwargs(),
        )
        elapsed = time.time() - started
        if not rust_results:
            return None
        skip_reason = _extract_marker_skip_reason(rust_results)
        if skip_reason:
            return (Constants.STATUS_SKIPPED, skip_reason)
        normalize_started = time.time()
        normalized, binary_count, _ = _normalize_rust_matches(rust_results, special_mode)
        if len(rust_results) > 100 or elapsed > 0.1:
            logger.debug(AppStrings.LOG_SCH_RUST_PERF_STATS.format(
                len(rust_results), elapsed, time.time() - normalize_started))
        if binary_count > 0:
            return (
                file_path,
                binary_count,
                [(1, AppStrings.MSG_BINARY_MATCH.format(binary_count), None, None)],
            )
        if normalized:
            return (file_path, len(normalized), normalized)
        return None
    except Exception as e:
        known_reason = _known_rust_skip_reason(e)
        if known_reason:
            return (Constants.STATUS_SKIPPED, known_reason)
        logger.error(AppStrings.LOG_SCH_RUST_ENGINE_ERROR.format(file_path, e), exc_info=True)
        return (
            Constants.STATUS_SKIPPED,
            AppStrings.ERROR_UNEXPECTED_FILE.format(
                file_path,
                _localize_internal_error_detail("Rust file search", e),
            ),
        )


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
                    skip_reason = _extract_marker_skip_reason(matches)
                    if skip_reason:
                        skipped_results.append((path, skip_reason))
                        continue
                    partial_reason = _extract_partial_skip_reason(matches)
                    if partial_reason:
                        skipped_results.append((path, partial_reason))
                    match_tuples, marker_binary_count, _ = _normalize_rust_matches(
                        matches, kwargs.get("special_mode"))
                    if marker_binary_count > 0:
                        formatted_results.append(
                            (
                                path,
                                marker_binary_count,
                                [(1, AppStrings.MSG_BINARY_MATCH.format(marker_binary_count), None, None)],
                            )
                        )
                    elif match_tuples:
                        formatted_results.append((path, _visible_match_count(match_tuples), match_tuples))
            
            for path, reason in skipped_list:
                skipped_results.append((path, format_skip_reason(reason)))
        return {"results": formatted_results, "skipped": skipped_results}
    except (IOError, OSError, RuntimeError) as e:
        logger.error(AppStrings.LOG_SCH_RUST_DIR_SEARCH_ERROR.format("SearchEngine.Directory", e))
        raise
    except BaseException as e:
        logger.critical(AppStrings.LOG_SCH_RUST_DIR_FATAL.format("SearchEngine.Directory", e), exc_info=True)
        raise


def search_files_list_fast(
    file_list: List[str],
    search_string: str,
    special_mode: Optional[str] = None,
    stop_event=None,
    progress_callback=None,
    **kwargs,
) -> Dict[str, List]:
    """Rust 엔진을 사용하여 파일 목록을 고속으로 검색합니다."""
    validate_search_query(search_string)
    if not file_list:
        return {"results": [], "skipped": []}
    try:
        rust_pattern = normalize_unicode(search_string)
        search_func = getattr(sf_engine, "search_files_list", None)
        if not search_func:
            # Rust API에서 인수 부락 시 타입 예외 발생 (없는 결과 반환 등)
            raise AttributeError(AppStrings.LOG_SYS_SF_ENGINE_NOT_FOUND.format("search_files_list"))
        exclude_hidden = bool(kwargs.get("exclude_hidden", False))
        # 파일 리스트 검색 시에도 바이너리 제외 옵션을 존중하도록 처리합니다.
        exclude_binary = bool(kwargs.get("exclude_binary", True))
        mode_bits = get_rust_mode_bits(special_mode, exclude_binary=exclude_binary)
        max_per_file, max_json_depth, max_json_size = _get_rust_search_limits()
        rust_options = _build_rust_options(
            mode_bits=mode_bits,
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
        )
        if rust_options is not None:
            raw_ret = search_func(file_list=file_list, search_string=rust_pattern, options=rust_options)
        else:
            raw_ret = search_func(
                file_list=file_list,
                search_string=rust_pattern,
                mode_bits=mode_bits,
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
        skipped_results = []
        if raw_ret:
            matches_list, skipped_list = raw_ret
            # [Optim] results_callback이 제공된 경우 중복 처리 건너뜁니다.
            if not kwargs.get("results_callback"):
                for path, matches in matches_list:
                    skip_reason = _extract_marker_skip_reason(matches)
                    if skip_reason:
                        skipped_results.append((path, skip_reason))
                        continue
                    partial_reason = _extract_partial_skip_reason(matches)
                    if partial_reason:
                        skipped_results.append((path, partial_reason))
                    match_tuples, marker_binary_count, _ = _normalize_rust_matches(
                        matches, special_mode)
                    if marker_binary_count > 0:
                        formatted_results.append(
                            (
                                path,
                                marker_binary_count,
                                [(1, AppStrings.MSG_BINARY_MATCH.format(marker_binary_count), None, None)],
                            )
                        )
                    elif match_tuples:
                        formatted_results.append((path, _visible_match_count(match_tuples), match_tuples))
            
            for path, reason in skipped_list:
                skipped_results.append((path, format_skip_reason(reason)))
        return {"results": formatted_results, "skipped": skipped_results}
    except BaseException as e:
        logger.critical(AppStrings.LOG_SCH_RUST_FILELIST_FATAL.format("SearchEngine.FileList", e), exc_info=True)
        raise
