from contextlib import contextmanager
from contextvars import ContextVar
import logging
import os
import threading
import unicodedata
from typing import Any, Dict, List, Optional, Tuple, cast

from sf_utils.app_strings import AppStrings
from sf_utils.config_manager import ConfigManager
from sf_utils.constants import Constants
from core.search_query import validate_search_query
from core.skip_reason_codes import (
    SKIP_CODE_UNKNOWN,
    SKIP_CODE_DECODE_PARTIAL,
    SKIP_CODE_EXCEL_PARTIAL,
    SKIP_CODE_FILE_MATCH_LIMIT,
    SKIP_CODE_JSON_DEPTH_LIMIT,
    SKIP_CODE_ROOT_JUNCTION,
    build_skip_reason,
    is_coded_reason,
)
from core.skip_reason_formatter import _log_raw_skip_detail

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
        number = _parse_int(settings.get(key, default))
        return default if number is None else max(1, number)

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


def _build_rust_options(**values: Any) -> Any:
    """Rust 검색 옵션 객체(``SearchOptions``)를 만듭니다. 엔진을 불러올 때 있는지 확인합니다."""
    values.setdefault("include_junctions", _get_adv_setting(Constants.CONFIG_KEY_INCLUDE_JUNCTIONS, False) is True)
    values.setdefault("encoding", _get_adv_setting(Constants.CONFIG_KEY_SEARCH_ENCODING, "auto"))
    if values.get("max_total_matches") is None:
        values.pop("max_total_matches", None)
    return cast(Any, sf_engine).SearchOptions(**values)


def _parse_int(value: Any) -> Optional[int]:
    """정수로 바꿀 수 있으면 그 값을, 아니면 ``None``을 돌려줍니다. 양수 제한값 함수들이 함께 씁니다."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _positive_int_or_none(value: Any) -> Optional[int]:
    number = _parse_int(value)
    return number if number is not None and number > 0 else None


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
    # 앱은 옵션 객체(SearchOptions)와 종류·코드가 붙은 결과 객체(SearchMatch)를 주는 엔진만 씁니다(7.2.6).
    missing_api = [name for name in ("SearchOptions", "SearchMatch", "search_dir") if not hasattr(sf_engine, name)]
    if engine_version < REQUIRED_API_VERSION or missing_api:
        _err_msg = AppStrings.ERROR_RUST_API_VERSION.format(REQUIRED_API_VERSION, engine_version)
        if missing_api:
            _err_msg += f" (missing: {', '.join(missing_api)})"
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
    logger.warning(AppStrings.LOG_SYS_RUST_LOAD_FAIL)
except Exception as e:
    HAS_RUST_ENGINE = False
    _RUST_ENGINE_ERROR = f"{type(e).__name__}: {e}"
    logger.warning(AppStrings.LOG_SYS_RUST_LOAD_FAIL)

SearchMatch = Tuple[Any, ...]
SearchResult = Tuple[str, int, List[SearchMatch]]


# Rust UE 현지화 archive 결과 표식입니다. 뒤에 [Namespace, Key, 원문, 번역문] JSON 배열이 옵니다.
RUST_MATCH_MARKER_ARCHIVE_FIELDS = "ARCHIVE_FIELDS|"

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
    """엔진 결과 객체(``SearchMatch``)를 화면용 행, 바이너리 일치 수, 읽지 못한 시트 목록으로 바꿉니다.

    엔진이 붙여 주는 종류(``kind``)만 보고 나눕니다(7.2.6). 표식 글자를 다시 해석하지 않습니다.
    """
    from core import search_mode as sm

    res: List[Any] = []
    bin_cnt = 0
    sheet_skips: List[Tuple[str, str]] = []
    mode_kind = sm.kind(special_mode)
    for m in matches:
        kind = m.kind
        if kind == "match":
            line, content, offset, length = m.line, m.content, m.offset, m.length
            if mode_kind == sm.KIND_XML and "\t" in content:
                # "/a/b/@c\t값" → ("a > b > @c", "값")
                tag_path, _, value = content.partition("\t")
                res.append((line, tag_path.lstrip("/").replace("/", " > "), value, offset, length))
            elif mode_kind == sm.KIND_JSON and ("\t" in content or content.startswith("JSON_FIELDS|")):
                parts = _split_json_match_content(content)
                position = "" if line == 0 else line
                res.append((position, json_path_for_display(parts[0]), parts[1] if len(parts) > 1 else "", offset, length))
            elif mode_kind == sm.KIND_ARCHIVE and (fields := _archive_fields_from_rust(content)) is not None:
                res.append((line, *fields, offset, length))
            elif mode_kind == sm.KIND_EXCEL:
                sheet, cell, value = _split_excel_match_content(content)
                res.append((line, sheet, cell, value, offset, length))
            else:
                res.append((line, content, offset, length))
        elif kind == "long_line":
            res.append((m.line, AppStrings.MSG_LONG_LINE_PREVIEW.format(m.detail or ""), m.offset, m.length))
        elif kind == "truncated":
            res.append((-1, AppStrings.MSG_MATCH_LIMIT_PER_FILE.format(
                _get_adv_setting(Constants.CONFIG_KEY_MAX_PER_FILE_MATCHES, Constants.DEFAULT_MAX_PER_FILE_MATCHES)
            ), None, None))
        elif kind == "binary":
            try:
                bin_cnt += int(m.detail)
            except (TypeError, ValueError):
                bin_cnt += 1
        elif kind == "sheet_error":
            sheet_name, _, detail = str(m.detail or "").partition("|")
            _log_raw_skip_detail(f"Excel sheet {sheet_name.strip()}", detail)
            sheet_skips.append((sheet_name.strip(), AppStrings.EXCEL_DETAIL_SHEET_FAILURE))
        # "partial"(일부만 검색)은 파일 단위 안내로, "error"는 파일을 건너뛴 사유로 따로 처리합니다.
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

    엔진의 보통 일치(kind == "match")는 표식이 아니므로 표식 검사에서 뺍니다. 일치가 많을 때 검사가 빨라집니다.
    주의: 종류는 엔진(types.rs)이 줄 내용의 글자 모양으로 정합니다. 그래서 내용이 "ERR_…|"로 시작하는 실제
    일치 줄은 지금 오류로 분류됩니다(7.2.1부터 있던 문제, 엔진 수정 제안서에 기록).
    """
    return [m for m in matches or [] if m.kind != "match"]


def _extract_marker_skip_reason(matches: Any) -> Optional[str]:
    """파일 전체를 건너뛴 오류(``kind == "error"``)가 있으면 그 사유 코드를 돌려줍니다."""
    for m in _metadata_items(matches):
        if m.kind == "error" and m.code:
            return build_skip_reason(m.code, m.detail or "")
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
    for candidate in (value, _get_adv_setting(key, default)):
        number = _parse_int(candidate)
        if number is not None:
            return max(1, number)
    return default


def _extract_partial_skip_reason(matches: Any) -> Optional[str]:
    """일부만 검색한 파일의 안내를 코드 형식(한 줄에 하나)으로 만듭니다. 문장은 보여 줄 때 만듭니다."""
    reasons: List[str] = []
    failed_sheets: List[str] = []
    for m in _metadata_items(matches):
        kind, code, detail = m.kind, m.code, m.detail
        if kind == "sheet_error":
            sheet, _, raw_error = str(detail or "").partition("|")
            _log_raw_skip_detail(f"Excel sheet {sheet}", raw_error)
            failed_sheets.append(sheet)
        elif kind == "truncated":
            limit = _positive_limit(detail, Constants.CONFIG_KEY_MAX_PER_FILE_MATCHES,
                                    Constants.DEFAULT_MAX_PER_FILE_MATCHES)
            reasons.append(build_skip_reason(SKIP_CODE_FILE_MATCH_LIMIT, limit))
        elif kind == "partial" and code == "DECODE_PARTIAL":
            reasons.append(build_skip_reason(SKIP_CODE_DECODE_PARTIAL, detail or ""))
        elif kind == "partial" and code == "JSON_DEPTH_LIMIT":
            limit = _positive_limit(detail, Constants.CONFIG_KEY_MAX_JSON_DEPTH, Constants.DEFAULT_MAX_JSON_DEPTH)
            reasons.append(build_skip_reason(SKIP_CODE_JSON_DEPTH_LIMIT, limit))
    if failed_sheets:
        reasons.append(build_skip_reason(SKIP_CODE_EXCEL_PARTIAL, ", ".join(dict.fromkeys(failed_sheets))))
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


def get_rust_mode_bits(special_mode: Optional[str], exclude_binary: bool = False) -> int:
    """검색 옵션에 따른 Rust 엔진 모드 비트 플래그를 생성하여 반환합니다."""
    bits = Constants.RUST_MODE_NORMAL
    if exclude_binary:
        bits |= Constants.RUST_MODE_EXCLUDE_BINARY
    if _get_adv_setting(Constants.CONFIG_KEY_ALLOW_DUPLICATE_JSON_KEYS, False):
        bits |= Constants.RUST_MODE_ALLOW_DUPLICATE_JSON_KEYS

    if not special_mode:
        return bits
    from core import search_mode

    mode_kind = search_mode.kind(special_mode)
    if mode_kind == search_mode.KIND_XML:
        bits |= Constants.RUST_MODE_XML
    elif mode_kind == search_mode.KIND_JSON:
        bits |= Constants.RUST_MODE_JSON
    elif mode_kind == search_mode.KIND_ARCHIVE:
        bits |= Constants.RUST_MODE_ARCHIVE
    if search_mode.is_exact(special_mode):
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
                    junction_reason = build_skip_reason(SKIP_CODE_ROOT_JUNCTION, "")
                    if (path, junction_reason) not in excluded_roots:
                        excluded_roots.append((path, junction_reason))
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
        # search_dir가 있는지는 엔진을 불러올 때 확인합니다(7.2.6).
        search_dir_func = cast(Any, sf_engine).search_dir
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
        raw_ret = search_dir_func(root_paths=search_paths, pattern=rust_pattern, options=rust_options)
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
                reason_text = str(reason or "")
                if not is_coded_reason(reason_text):
                    reason_text = build_skip_reason(SKIP_CODE_UNKNOWN, reason_text)
                skipped_results.append((path, reason_text))
        return {"results": formatted_results, "skipped": skipped_results}
    except (IOError, OSError, RuntimeError) as e:
        logger.error(AppStrings.LOG_SCH_RUST_DIR_SEARCH_ERROR.format("SearchEngine.Directory", e))
        raise
    except BaseException as e:
        logger.critical(AppStrings.LOG_SCH_RUST_DIR_FATAL.format("SearchEngine.Directory", e), exc_info=True)
        raise
