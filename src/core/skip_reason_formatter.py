"""Formatting helpers for localized skip reasons."""

import re
from typing import Any


def format_excel_panic_reason(detail: Any, *, app_strings: Any, constants: Any, logger: Any) -> str:
    """Convert internal Excel panic diagnostics into a localized user message."""
    raw_detail = str(detail or "").strip()
    if raw_detail:
        logger.warning(app_strings.LOG_SCH_EXCEL_ENGINE_PANIC.format(raw_detail))
    format_name = ""
    engine_detail = raw_detail
    if "|" in raw_detail:
        candidate, engine_detail = raw_detail.split("|", 1)
        if candidate.strip().lower() in constants.EXT_EXCEL:
            format_name = candidate.strip().upper()
        else:
            engine_detail = raw_detail
    elif raw_detail.lower() in constants.EXT_EXCEL:
        format_name = raw_detail.upper()
        engine_detail = ""

    range_error = re.fullmatch(
        r"range start index (\d+) out of range for slice of length (\d+)",
        engine_detail.strip(), flags=re.IGNORECASE,
    )
    if range_error:
        localized_detail = app_strings.EXCEL_DETAIL_RANGE_OUT_OF_BOUNDS.format(
            format_name or "Excel", *range_error.groups())
    elif format_name:
        localized_detail = app_strings.EXCEL_DETAIL_ENGINE_FAILURE.format(format_name)
    else:
        localized_detail = app_strings.EXCEL_DETAIL_UNKNOWN_FORMAT
    return app_strings.ERROR_EXCEL_PANIC.format(localized_detail)
