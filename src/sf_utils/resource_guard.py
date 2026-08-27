"""Runtime resource checks used to keep large searches within safe limits."""

from __future__ import annotations

import os
from numbers import Real
from typing import Dict

import psutil

from sf_utils.constants import Constants


def _process_tree_rss_bytes() -> int:
    """Return RSS for the application process and its current child processes."""
    try:
        process = psutil.Process(os.getpid())
        processes = [process, *process.children(recursive=True)]
    except (psutil.Error, OSError):
        return 0

    rss = 0
    for candidate in processes:
        try:
            rss += candidate.memory_info().rss
        except (psutil.Error, OSError):
            # A worker may exit while the snapshot is being collected.
            continue
    return rss


def _as_int(value: object, default: int = 0) -> int:
    return int(float(value)) if isinstance(value, Real) else default


def memory_snapshot() -> Dict[str, int]:
    """Return memory values used by the safety policy."""
    vm = psutil.virtual_memory()
    available_raw = getattr(vm, "available", None)
    total_raw = getattr(vm, "total", None)
    values_are_valid = isinstance(available_raw, Real) and isinstance(total_raw, Real)
    available = _as_int(available_raw) if values_are_valid else 0
    total = _as_int(total_raw) if values_are_valid else 0
    system_percent_raw = getattr(vm, "percent", 0)
    system_percent = _as_int(system_percent_raw)
    return {
        "available": available,
        "total": total,
        "process_rss": _process_tree_rss_bytes(),
        "system_percent": system_percent,
        "valid": int(values_are_valid and total > 0 and available >= 0),
    }


def memory_pressure_detected(snapshot: Dict[str, int] | None = None) -> bool:
    """Return whether the application is approaching a configured memory limit."""
    values = snapshot or memory_snapshot()
    if not values.get("valid", int(values.get("total", 0) > 0 and values.get("available", 0) >= 0)):
        # Some test hosts and restricted Windows environments expose no usable
        # virtual-memory counters. Unknown telemetry must not abort a search.
        return False
    minimum_available = Constants.MIN_AVAILABLE_MEMORY_BYTES
    process_limit = int(values["total"] * Constants.PROCESS_MEMORY_THRESHOLD_PERCENT / 100)
    return (
        values["available"] < minimum_available
        or (values["process_rss"] > 0 and values["process_rss"] >= process_limit)
    )


def memory_pressure_message(snapshot: Dict[str, int] | None = None) -> str:
    """Create a diagnostic message suitable for logging."""
    values = snapshot or memory_snapshot()
    return (
        f"available={values['available'] // (1024 * 1024)}MB, "
        f"process_tree_rss={values['process_rss'] // (1024 * 1024)}MB, "
        f"system={values['system_percent']}%"
    )
