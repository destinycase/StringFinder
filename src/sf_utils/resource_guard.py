"""Runtime resource checks used to keep large searches within safe limits."""

from __future__ import annotations

import os
from dataclasses import dataclass
from numbers import Real
from typing import Dict, Literal, Mapping, Optional

import psutil

from sf_utils.constants import Constants


MemoryPressureReason = Literal["low_available_memory", "process_tree_limit"]
ProjectedMemoryPressureReason = Literal[
    "projected_available_memory",
    "projected_process_tree_limit",
]
StructuredDocumentKind = Literal["json", "xml"]
StructuredEngineKind = Literal["rust", "python"]


@dataclass(frozen=True)
class MemoryLimits:
    """Calculated memory thresholds for one device."""

    reserve_bytes: int
    process_limit_bytes: int


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


def calculate_memory_limits(total_memory: int) -> MemoryLimits:
    """Return adaptive reserve and process limits for the installed RAM size."""
    total = max(0, int(total_memory))
    if total == 0:
        return MemoryLimits(reserve_bytes=0, process_limit_bytes=0)

    proportional_reserve = total * Constants.MEMORY_RESERVE_PERCENT // 100
    reserve = max(Constants.MIN_AVAILABLE_MEMORY_BYTES, proportional_reserve)
    reserve = min(reserve, Constants.MAX_AVAILABLE_MEMORY_RESERVE_BYTES, total)

    proportional_process_limit = (
        total * Constants.PROCESS_MEMORY_THRESHOLD_PERCENT // 100
    )
    process_limit = min(
        proportional_process_limit,
        Constants.MAX_PROCESS_MEMORY_BYTES,
    )
    return MemoryLimits(
        reserve_bytes=reserve,
        process_limit_bytes=process_limit,
    )


def _snapshot_values(snapshot: Mapping[str, object]) -> tuple[int, int, int, bool]:
    available_raw = snapshot.get("available")
    total_raw = snapshot.get("total")
    counters_are_valid = isinstance(available_raw, Real) and isinstance(total_raw, Real)
    available = _as_int(available_raw)
    total = _as_int(total_raw)
    process_rss = max(0, _as_int(snapshot.get("process_rss")))
    default_valid = int(counters_are_valid and total > 0 and available >= 0)
    valid = (
        bool(snapshot.get("valid", default_valid))
        and counters_are_valid
        and total > 0
        and available >= 0
    )
    return available, total, process_rss, valid


def memory_pressure_reason(
    snapshot: Mapping[str, object] | None = None,
) -> Optional[MemoryPressureReason]:
    """Return the current system-wide stop reason, if any."""
    values = memory_snapshot() if snapshot is None else snapshot
    available, total, process_rss, valid = _snapshot_values(values)
    if not valid:
        # Some test hosts and restricted Windows environments expose no usable
        # virtual-memory counters. Unknown telemetry must not abort a search.
        return None

    limits = calculate_memory_limits(total)
    if available < limits.reserve_bytes:
        return "low_available_memory"
    if process_rss > 0 and process_rss >= limits.process_limit_bytes:
        return "process_tree_limit"
    return None


def memory_pressure_detected(snapshot: Dict[str, int] | None = None) -> bool:
    """Return whether current pressure requires stopping the whole search."""
    return memory_pressure_reason(snapshot) is not None


def estimate_structured_memory_bytes(
    file_size: int,
    document_kind: StructuredDocumentKind,
    engine_kind: StructuredEngineKind,
) -> int:
    """Conservatively estimate incremental memory for one structured document."""
    size = max(0, int(file_size))
    profile = (document_kind.casefold(), engine_kind.casefold())
    if profile in {("json", "rust"), ("xml", "rust")}:
        # mmap/read buffer, decoded UTF-8 buffer, and parser/search workspace.
        return (size * 5 + 1) // 2 + 64 * 1024 * 1024
    if profile == ("xml", "python"):
        # Python text representation plus Expat handler/path state.
        return size * 4 + 64 * 1024 * 1024
    if profile == ("json", "python"):
        # Decoded text and the Python object graph created by json.loads().
        return size * 6 + 128 * 1024 * 1024
    raise ValueError(f"Unsupported structured-memory profile: {profile!r}")


def projected_memory_pressure_reason(
    snapshot: Mapping[str, object] | None,
    required_bytes: int,
) -> Optional[ProjectedMemoryPressureReason]:
    """Return a file-local reason when one projected allocation is unsafe."""
    values = memory_snapshot() if snapshot is None else snapshot
    available, total, process_rss, valid = _snapshot_values(values)
    required = max(0, int(required_bytes))
    if not valid or required == 0:
        return None

    limits = calculate_memory_limits(total)
    if available - required < limits.reserve_bytes:
        return "projected_available_memory"
    if process_rss + required >= limits.process_limit_bytes:
        return "projected_process_tree_limit"
    return None


def memory_pressure_message(snapshot: Dict[str, int] | None = None) -> str:
    """Create a diagnostic message suitable for logging."""
    values = memory_snapshot() if snapshot is None else snapshot
    available, total, process_rss, valid = _snapshot_values(values)
    limits = calculate_memory_limits(total)
    system_percent = _as_int(values.get("system_percent"))
    return (
        f"available={available // (1024 * 1024)}MB, "
        f"reserve={limits.reserve_bytes // (1024 * 1024)}MB, "
        f"process_tree_rss={process_rss // (1024 * 1024)}MB, "
        f"process_limit={limits.process_limit_bytes // (1024 * 1024)}MB, "
        f"system={system_percent}%, valid={int(valid)}"
    )
