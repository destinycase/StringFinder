"""Run a one-shot, privacy-preserving performance diagnostic on a folder.

A fresh high-entropy query is generated for each report and is not exported.
Only aggregate counts, timings, resource usage, and result-contract counters
are exported.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import platform
import random
import secrets
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from core.search_engine import is_hidden_path, search_in_file, use_search_settings_snapshot  # noqa: E402
from core.skip_reason_codes import TEMPLATE_NAMES, decode_skip_reason  # noqa: E402
from sf_utils.app_strings import AppStrings  # noqa: E402
from sf_utils.constants import Constants  # noqa: E402
from sf_utils.version_helper import get_app_version  # noqa: E402

ONE_GIB_BYTES = 1024**3
DEFAULT_MAX_SECONDS = 7200
RESOURCE_SAMPLE_INTERVAL_SECONDS = 30
MAX_RESOURCE_SAMPLES_PER_SCENARIO = 600
SCENARIOS = (
    ("normal_no_match", False, False, False),
    ("normal_existence", False, True, False),
    ("miss_prevention_no_match", True, False, False),
    ("miss_prevention_existence", True, True, False),
)

SCENARIO_LABELS = {
    "normal_no_match": "일반 검색 · 매치 없음",
    "normal_existence": "일반 검색 · 존재만 확인",
    "miss_prevention_no_match": "누락 방지 검색 · 매치 없음",
    "miss_prevention_existence": "누락 방지 검색 · 존재만 확인",
}

LATENCY_BUCKETS = (0.001, 0.005, 0.025, 0.1, 1.0, 10.0)

SCENARIO_LABELS.update({
    "normal_no_match": "Normal search - no match",
    "normal_existence": "Normal search - existence only",
    "miss_prevention_no_match": "Miss-prevention search - no match",
    "miss_prevention_existence": "Miss-prevention search - existence only",
})


def _rss_mb() -> float | None:
    try:
        import psutil

        return round(psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024), 2)
    except (ImportError, OSError):
        return None


def _hardware() -> dict[str, object]:
    result: dict[str, object] = {"logical_cpus": os.cpu_count() or 1}
    try:
        import platform

        result["os"] = platform.platform()
        result["machine"] = platform.machine()
        result["processor"] = platform.processor()
    except Exception:
        pass
    try:
        import psutil

        result["memory_total_mb"] = round(psutil.virtual_memory().total / (1024 * 1024), 2)
    except (ImportError, OSError):
        pass
    return result


def _classify(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return "json"
    if suffix == ".xml":
        return "xml"
    if suffix in {".xlsx", ".xlsm", ".xls", ".xlsb"}:
        return "excel"
    return "text"


def _size_bucket(size: int) -> str:
    if size <= 64 * 1024:
        return "0-64KB"
    if size <= 10 * 1024 * 1024:
        return "64KB-10MB"
    if size <= 100 * 1024 * 1024:
        return "10MB-100MB"
    if size <= ONE_GIB_BYTES:
        return "100MB-1GiB"
    return ">1GiB"


def _new_diagnostic_query() -> str:
    """Create a high-entropy per-report query without a source-code literal."""
    return secrets.token_urlsafe(24)


def _unknown_skip_category(reason: object) -> str | None:
    """Classify recognizable ERR_UNKNOWN details without exporting raw text."""
    code, detail = decode_skip_reason(reason)
    if code != "ERR_UNKNOWN":
        return None
    text = (detail or str(reason or "")).casefold()
    categories = (
        ("permission_or_access", ("permission", "access denied", "winerror 5", "접근 거부", "권한이 없")),
        ("file_in_use", ("sharing violation", "winerror 32", "being used", "locked", "사용 중", "다른 프로세스")),
        ("not_found", ("file not found", "no such file", "winerror 2", "winerror 3", "찾을 수 없")),
        ("path_too_long", ("path too long", "filename too long", "winerror 206", "경로가 너무", "파일 이름이 너무")),
        ("empty_file", ("empty file", "file is empty", "빈 파일")),
        ("binary_or_nontext", ("binary", "non-text", "이진 파일", "바이너리")),
        ("encoding_or_decode", ("encoding", "decode", "codec", "invalid byte", "인코딩", "디코딩")),
        ("resource_pressure", ("too many open files", "resource temporarily unavailable", "out of memory", "memory error", "리소스 부족", "메모리 부족")),
        ("io_error", ("i/o error", "os error", "read error", "write error", "input/output", "입출력", "읽기 실패", "쓰기 실패")),
        ("format_or_parse", ("unsupported format", "invalid format", "malformed", "parse error", "not a zip", "손상된 형식", "구문 분석")),
    )
    for category, indicators in categories:
        if any(indicator in text for indicator in indicators):
            return category
    return "unclassified"


def _is_hidden_relative_to_root(root: Path, path: Path) -> bool:
    """Apply hidden-file and hidden-directory filtering without exposing paths."""
    try:
        relative_parts = path.relative_to(root).parts
    except ValueError:
        return False
    for depth in range(1, len(relative_parts) + 1):
        if is_hidden_path(str(root.joinpath(*relative_parts[:depth]))):
            return True
    return False


def _files(root: Path, *, exclude_hidden: bool = True) -> tuple[list[Path], int]:
    files: list[Path] = []
    hidden_files_excluded = 0
    for path in root.rglob("*"):
        if not path.is_file() or "$recycle.bin" in {part.lower() for part in path.parts}:
            continue
        if exclude_hidden and _is_hidden_relative_to_root(root, path):
            hidden_files_excluded += 1
            continue
        files.append(path)
    return files, hidden_files_excluded


def _diagnostic_search_policy() -> dict[str, object]:
    """Return only effective, non-sensitive settings that affect search work."""
    from sf_utils.config_manager import ConfigManager

    config = ConfigManager()
    advanced = config.get_advanced_settings()
    advanced_settings = {
        key: advanced.get(key, spec["default"])
        for key, spec in Constants.ADVANCED_SETTING_SPECS.items()
    }
    return {
        "exclude_hidden": config.get(Constants.CONFIG_KEY_EXCLUDE_HIDDEN, True) is True,
        "exclude_binary": config.get_exclude_binary(),
        "advanced_settings": advanced_settings,
    }


def _safe_extension(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix not in {
        ".bat", ".bin", ".c", ".cfg", ".cpp", ".csv", ".css", ".dat", ".db", ".dll",
        ".exe", ".go", ".h", ".html", ".hwp", ".ini", ".java", ".js", ".json", ".log",
        ".md", ".pak", ".pdf", ".ps1", ".py", ".rs", ".sh", ".sql", ".toml", ".ts",
        ".txt", ".xls", ".xlsb", ".xlsm", ".xlsx", ".xml", ".yaml", ".yml", ".zip",
    }:
        return "(other)"
    known_extensions = {
        ".bat", ".bin", ".c", ".cfg", ".cpp", ".csv", ".css", ".dat", ".db", ".dll",
        ".exe", ".go", ".h", ".html", ".hwp", ".ini", ".java", ".js", ".json", ".log",
        ".md", ".pak", ".pdf", ".ps1", ".py", ".rs", ".sh", ".sql", ".toml", ".ts",
        ".txt", ".xls", ".xlsb", ".xlsm", ".xlsx", ".xml", ".yaml", ".yml", ".zip",
    }
    return suffix if suffix in known_extensions else "(기타)"


def _anonymous_file_id(path: Path, size: int | None, salt: bytes) -> str:
    """Return an ID stable only within this report; never export the path or salt."""
    identity = f"{os.path.normcase(str(path))}|{size if size is not None else 'unknown'}".encode("utf-8", errors="replace")
    return hmac.new(salt, identity, hashlib.sha256).hexdigest()[:12]


def _skip_code(reason: object) -> str:
    code, _detail = decode_skip_reason(reason)
    if code != "ERR_UNKNOWN":
        return code
    text = str(reason or "")
    # Python-side skips are already localized. Match only fixed template text;
    # never place the raw reason (which may contain paths) in the report.
    for candidate, template_name in TEMPLATE_NAMES.items():
        template = str(getattr(AppStrings, template_name, ""))
        prefix = template.split("{", 1)[0].strip()
        if prefix and prefix in text:
            return candidate
    return "ERR_UNKNOWN"


def _new_metric() -> dict[str, object]:
    return {
        "files": 0,
        "nominal_bytes": 0,
        "elapsed_seconds": 0.0,
        "matches": 0,
        "skipped": 0,
        "errors": 0,
        "max_file_seconds": 0.0,
        "latency_buckets": [0] * (len(LATENCY_BUCKETS) + 1),
    }


def _resource_snapshot() -> dict[str, float | None]:
    result: dict[str, float | None] = {
        "rss_mb": None,
        "peak_rss_mb": None,
        "cpu_time_seconds": None,
        "system_memory_available_mb": None,
        "process_read_bytes": None,
        "process_write_bytes": None,
    }
    try:
        import psutil

        process = psutil.Process(os.getpid())
        memory = process.memory_info()
        result["rss_mb"] = round(memory.rss / (1024 * 1024), 2)
        peak = getattr(memory, "peak_wset", None)
        result["peak_rss_mb"] = round(peak / (1024 * 1024), 2) if peak else None
        result["cpu_time_seconds"] = round(sum(process.cpu_times()[:2]), 3)
        available = psutil.virtual_memory().available
        if isinstance(available, (int, float)):
            result["system_memory_available_mb"] = round(available / (1024 * 1024), 2)
        try:
            io = process.io_counters()
            result["process_read_bytes"] = int(io.read_bytes)
            result["process_write_bytes"] = int(io.write_bytes)
        except (AttributeError, OSError):
            pass
    except (ImportError, OSError, RuntimeError):
        pass
    return result


def _runtime_environment() -> dict[str, object]:
    info: dict[str, object] = {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "os": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "logical_cpus": os.cpu_count() or 1,
    }
    try:
        import psutil

        memory = psutil.virtual_memory()
        if isinstance(memory.total, (int, float)):
            info["memory_total_mb"] = round(memory.total / (1024 * 1024), 2)
        if isinstance(memory.available, (int, float)):
            info["memory_available_mb_at_start"] = round(memory.available / (1024 * 1024), 2)
    except (ImportError, OSError):
        pass
    engine = getattr(search_in_file, "__globals__", {}).get("sf_engine")
    info["rust_engine"] = {
        "available": bool(engine),
        "version": getattr(engine, "ENGINE_VERSION", None) if engine else None,
        "api_version": getattr(engine, "API_VERSION", None) if engine else None,
    }
    try:
        from importlib.metadata import PackageNotFoundError, version

        for package in ("python-calamine", "psutil", "PySide6"):
            try:
                info.setdefault("dependencies", {})[package] = version(package)
            except PackageNotFoundError:
                info.setdefault("dependencies", {})[package] = None
    except ImportError:
        pass
    return info


def _finalize_metrics(metrics: dict[str, dict[str, object]]) -> dict[str, dict[str, object]]:
    finalized: dict[str, dict[str, object]] = {}
    for key, metric in metrics.items():
        elapsed = float(metric["elapsed_seconds"])
        files = int(metric["files"])
        finalized[key] = {
            **metric,
            "elapsed_seconds": round(elapsed, 6),
            "nominal_input_bytes": int(metric["nominal_bytes"]),
            "nominal_mb_per_second": round(int(metric["nominal_bytes"]) / (1024 * 1024) / elapsed, 3)
            if elapsed
            else None,
            "files_per_second": round(files / elapsed, 3) if elapsed else None,
            "average_file_seconds": round(elapsed / files, 6) if files else None,
            "max_file_seconds": round(float(metric["max_file_seconds"]), 6),
            "latency_buckets": {
                "under_1ms": metric["latency_buckets"][0],
                "1_to_5ms": metric["latency_buckets"][1],
                "5_to_25ms": metric["latency_buckets"][2],
                "25_to_100ms": metric["latency_buckets"][3],
                "100ms_to_1s": metric["latency_buckets"][4],
                "1_to_10s": metric["latency_buckets"][5],
                "10s_or_more": metric["latency_buckets"][6],
            },
        }
        finalized[key].pop("nominal_bytes")
    return finalized










def run(
    root: Path,
    repeats: int = 5,
    progress_callback=None,
    cancel_event=None,
    max_seconds: int | None = DEFAULT_MAX_SECONDS,
) -> dict[str, object]:
    """Collect an actionable report without exporting file paths or contents."""
    wall_started = time.perf_counter()
    environment = _runtime_environment()
    search_policy = _diagnostic_search_policy()
    root = Path(root)
    inventory_started = time.perf_counter()
    paths, hidden_files_excluded = _files(
        root, exclude_hidden=bool(search_policy["exclude_hidden"])
    )
    inventory_seconds = time.perf_counter() - inventory_started
    metadata_started = time.perf_counter()
    salt = secrets.token_bytes(32)
    metadata: dict[Path, dict[str, object]] = {}
    inventory_types: Counter[str] = Counter()
    inventory_extensions: Counter[str] = Counter()
    inventory_sizes: Counter[str] = Counter()
    inventory_depths: Counter[str] = Counter()
    inventory_bytes = 0
    stat_failures = 0
    files_over_1gib = 0

    for path in paths:
        file_type = _classify(path)
        extension = _safe_extension(path)
        try:
            size: int | None = path.stat().st_size
        except OSError:
            size = None
            stat_failures += 1
        bucket = _size_bucket(size) if size is not None else "stat_failed"
        relative_depth = max(0, len(path.relative_to(root).parts) - 1)
        depth_bucket = "0" if relative_depth == 0 else "1" if relative_depth == 1 else "2-3" if relative_depth <= 3 else "4+"
        if size is not None:
            inventory_bytes += size
            inventory_sizes[bucket] += 1
            files_over_1gib += int(size > ONE_GIB_BYTES)
        inventory_types[file_type] += 1
        inventory_extensions[extension] += 1
        inventory_depths[depth_bucket] += 1
        metadata[path] = {
            "size": size,
            "type": file_type,
            "extension": extension,
            "size_bucket": bucket,
            "file_id": _anonymous_file_id(path, size, salt),
        }
    metadata_seconds = time.perf_counter() - metadata_started

    # A single randomized order is shared by every mode and repetition. If the
    # global time budget expires, all modes sample the same prefix rather than
    # whichever directories happen to be visited first by the filesystem.
    random.Random(secrets.randbits(64)).shuffle(paths)
    diagnostic_query = _new_diagnostic_query()

    benchmark_started = time.perf_counter()
    total_work = max(1, len(paths) * max(1, repeats) * len(SCENARIOS))
    completed_work = 0
    scenarios: list[dict[str, object]] = []
    stop_status: str | None = None
    budget_limited = False

    for scenario_index, (name, precise, existence_only, _unused) in enumerate(SCENARIOS):
        scenario_started = time.perf_counter()
        scenario_deadline = (
            None
            if max_seconds is None
            else benchmark_started + max_seconds * (scenario_index + 1) / len(SCENARIOS)
        )
        resource_before = _resource_snapshot()
        resource_samples: list[dict[str, float | int | None]] = [
            {
                "elapsed_seconds": 0.0,
                "rss_mb": resource_before.get("rss_mb"),
                "system_memory_available_mb": resource_before.get("system_memory_available_mb"),
                "cpu_cores_equivalent": None,
                "read_bytes_since_previous_sample": None,
                "write_bytes_since_previous_sample": None,
            }
        ]
        last_resource_sample_time = scenario_started
        last_resource_snapshot = resource_before
        resource_samples_truncated = False
        run_rows: list[dict[str, object]] = []
        type_metrics: dict[str, dict[str, object]] = defaultdict(_new_metric)
        extension_metrics: dict[str, dict[str, object]] = defaultdict(_new_metric)
        size_metrics: dict[str, dict[str, object]] = defaultdict(_new_metric)
        skip_codes: Counter[str] = Counter()
        unknown_skip_categories: Counter[str] = Counter()
        exception_types: Counter[str] = Counter()
        slow_file_stats: dict[str, dict[str, object]] = {}
        total_matches = total_match_files = total_skipped = total_errors = 0
        scenario_processed = 0
        scenario_timed_out = False

        for iteration in range(max(1, repeats)):
            iteration_started = time.perf_counter()
            processed = nominal_bytes = match_files = matches = skipped = errors = 0
            run_skip_codes: Counter[str] = Counter()
            run_unknown_skip_categories: Counter[str] = Counter()
            run_exception_types: Counter[str] = Counter()
            interrupted = False

            for path in paths:
                if cancel_event is not None and cancel_event.is_set():
                    stop_status = "cancelled"
                    interrupted = True
                    break
                if scenario_deadline is not None and time.perf_counter() >= scenario_deadline:
                    scenario_timed_out = True
                    budget_limited = True
                    interrupted = True
                    break

                item = metadata[path]
                file_size = item["size"]
                file_type = str(item["type"])
                extension = str(item["extension"])
                size_bucket = str(item["size_bucket"])
                elapsed = 0.0
                file_match_count = 0
                file_skip_code: str | None = None
                file_unknown_skip_category: str | None = None
                exception_name: str | None = None
                file_started = time.perf_counter()
                try:
                    with use_search_settings_snapshot(
                        cast(dict[str, Any], search_policy["advanced_settings"])
                    ):
                        result = search_in_file(
                            str(path),
                            diagnostic_query,
                            file_size=file_size,
                            use_complex_search=precise,
                            existence_only=existence_only,
                            exclude_binary=bool(search_policy["exclude_binary"]),
                        )
                    if isinstance(result, tuple) and result and result[0] == Constants.STATUS_SKIPPED:
                        skipped += 1
                        file_skip_code = _skip_code(result[1] if len(result) > 1 else "")
                        run_skip_codes[file_skip_code] += 1
                        skip_codes[file_skip_code] += 1
                        if file_skip_code == "ERR_UNKNOWN":
                            file_unknown_skip_category = _unknown_skip_category(
                                result[1] if len(result) > 1 else ""
                            )
                            if file_unknown_skip_category:
                                run_unknown_skip_categories[file_unknown_skip_category] += 1
                                unknown_skip_categories[file_unknown_skip_category] += 1
                    elif isinstance(result, tuple):
                        match_files += 1
                        try:
                            file_match_count = max(0, int(result[1] or 0))
                        except (IndexError, TypeError, ValueError):
                            file_match_count = 0
                        matches += file_match_count
                except Exception as exc:
                    skipped += 1
                    errors += 1
                    exception_name = type(exc).__name__
                    run_exception_types[exception_name] += 1
                    exception_types[exception_name] += 1
                    file_skip_code = "ERR_UNHANDLED_EXCEPTION"
                    run_skip_codes[file_skip_code] += 1
                    skip_codes[file_skip_code] += 1
                finally:
                    elapsed = time.perf_counter() - file_started
                    processed += 1
                    if file_size is not None:
                        nominal_bytes += int(file_size)
                    bucket_index = next((index for index, boundary in enumerate(LATENCY_BUCKETS) if elapsed < boundary), len(LATENCY_BUCKETS))
                    for metric in (type_metrics[file_type], extension_metrics[extension], size_metrics[size_bucket]):
                        metric["files"] += 1
                        metric["nominal_bytes"] += int(file_size or 0)
                        metric["elapsed_seconds"] += elapsed
                        metric["matches"] += file_match_count
                        metric["skipped"] += int(file_skip_code is not None)
                        metric["errors"] += int(exception_name is not None)
                        metric["max_file_seconds"] = max(float(metric["max_file_seconds"]), elapsed)
                        metric["latency_buckets"][bucket_index] += 1

                    if elapsed >= 0.1 or file_skip_code:
                        file_id = str(item["file_id"])
                        candidate = slow_file_stats.get(file_id)
                        if candidate is None and len(slow_file_stats) >= 100:
                            slowest_id = min(
                                slow_file_stats,
                                key=lambda candidate_id: float(slow_file_stats[candidate_id]["max_seconds"]),
                            )
                            if elapsed <= float(slow_file_stats[slowest_id]["max_seconds"]):
                                candidate = None
                            else:
                                del slow_file_stats[slowest_id]
                        if candidate is not None or file_id not in slow_file_stats:
                            if candidate is None and len(slow_file_stats) < 100:
                                candidate = {
                                    "file_id": file_id,
                                    "extension": extension,
                                    "file_type": file_type,
                                    "size_bucket": size_bucket,
                                    "size_bytes": file_size,
                                    "observations": 0,
                                    "elapsed_total_seconds": 0.0,
                                    "min_seconds": elapsed,
                                    "max_seconds": elapsed,
                                    "skipped": 0,
                                    "skip_reason_codes": {},
                                    "unknown_skip_categories": {},
                                    "exception_types": {},
                                }
                                slow_file_stats[file_id] = candidate
                            if candidate is not None:
                                candidate["observations"] += 1
                                candidate["elapsed_total_seconds"] += elapsed
                                candidate["min_seconds"] = min(float(candidate["min_seconds"]), elapsed)
                                candidate["max_seconds"] = max(float(candidate["max_seconds"]), elapsed)
                                candidate["skipped"] += int(file_skip_code is not None)
                                if file_skip_code:
                                    codes = candidate["skip_reason_codes"]
                                    codes[file_skip_code] = codes.get(file_skip_code, 0) + 1
                                if file_unknown_skip_category:
                                    categories = candidate["unknown_skip_categories"]
                                    categories[file_unknown_skip_category] = (
                                        categories.get(file_unknown_skip_category, 0) + 1
                                    )
                                if exception_name:
                                    types = candidate["exception_types"]
                                    types[exception_name] = types.get(exception_name, 0) + 1

                    completed_work += 1
                    scenario_processed += 1
                    if progress_callback:
                        progress_callback(completed_work, total_work, name, str(path))

                    sample_now = time.perf_counter()
                    if (
                        sample_now - last_resource_sample_time >= RESOURCE_SAMPLE_INTERVAL_SECONDS
                        and not resource_samples_truncated
                    ):
                        if len(resource_samples) < MAX_RESOURCE_SAMPLES_PER_SCENARIO:
                            snapshot = _resource_snapshot()
                            sample_elapsed = max(0.001, sample_now - last_resource_sample_time)
                            previous_cpu = last_resource_snapshot.get("cpu_time_seconds")
                            current_cpu = snapshot.get("cpu_time_seconds")
                            previous_read = last_resource_snapshot.get("process_read_bytes")
                            current_read = snapshot.get("process_read_bytes")
                            previous_write = last_resource_snapshot.get("process_write_bytes")
                            current_write = snapshot.get("process_write_bytes")
                            resource_samples.append(
                                {
                                    "elapsed_seconds": round(sample_now - scenario_started, 3),
                                    "rss_mb": snapshot.get("rss_mb"),
                                    "system_memory_available_mb": snapshot.get("system_memory_available_mb"),
                                    "cpu_cores_equivalent": round((current_cpu - previous_cpu) / sample_elapsed, 3)
                                    if current_cpu is not None and previous_cpu is not None else None,
                                    "read_bytes_since_previous_sample": current_read - previous_read
                                    if current_read is not None and previous_read is not None else None,
                                    "write_bytes_since_previous_sample": current_write - previous_write
                                    if current_write is not None and previous_write is not None else None,
                                }
                            )
                            last_resource_sample_time = sample_now
                            last_resource_snapshot = snapshot
                        else:
                            resource_samples_truncated = True

            elapsed_iteration = time.perf_counter() - iteration_started
            if processed or not paths or interrupted:
                run_rows.append(
                    {
                        "run": iteration + 1,
                        "complete": not interrupted and processed == len(paths),
                        "elapsed_seconds": round(elapsed_iteration, 6),
                        "files_processed": processed,
                        "nominal_input_bytes": nominal_bytes,
                        "files_per_second": round(processed / elapsed_iteration, 3) if elapsed_iteration else None,
                        "nominal_mb_per_second": round(nominal_bytes / (1024 * 1024) / elapsed_iteration, 3)
                        if elapsed_iteration
                        else None,
                        "match_files": match_files,
                        "matches": matches,
                        "skipped": skipped,
                        "errors": errors,
                        "skip_reason_codes": dict(run_skip_codes),
                        "unknown_skip_categories": dict(run_unknown_skip_categories),
                        "exception_types": dict(run_exception_types),
                        "verdict": "INCOMPLETE"
                        if interrupted
                        else "REVIEW"
                        if skipped or errors or matches or not paths
                        else "PASS",
                    }
                )
            total_matches += matches
            total_match_files += match_files
            total_skipped += skipped
            total_errors += errors
            if interrupted:
                break

        resource_after = _resource_snapshot()
        final_now = time.perf_counter()
        final_elapsed = final_now - scenario_started
        final_interval = max(0.001, final_now - last_resource_sample_time)
        previous_cpu = last_resource_snapshot.get("cpu_time_seconds")
        current_cpu = resource_after.get("cpu_time_seconds")
        previous_read = last_resource_snapshot.get("process_read_bytes")
        current_read = resource_after.get("process_read_bytes")
        previous_write = last_resource_snapshot.get("process_write_bytes")
        current_write = resource_after.get("process_write_bytes")
        final_sample: dict[str, float | int | None] = {
            "elapsed_seconds": round(final_elapsed, 3),
            "rss_mb": resource_after.get("rss_mb"),
            "system_memory_available_mb": resource_after.get("system_memory_available_mb"),
            "cpu_cores_equivalent": round((current_cpu - previous_cpu) / final_interval, 3)
            if current_cpu is not None and previous_cpu is not None else None,
            "read_bytes_since_previous_sample": current_read - previous_read
            if current_read is not None and previous_read is not None else None,
            "write_bytes_since_previous_sample": current_write - previous_write
            if current_write is not None and previous_write is not None else None,
        }
        if len(resource_samples) >= MAX_RESOURCE_SAMPLES_PER_SCENARIO:
            resource_samples[-1] = final_sample
        else:
            resource_samples.append(final_sample)
        scenario_wall = time.perf_counter() - scenario_started
        completed_repeats = sum(1 for row in run_rows if row["complete"])
        scenario_status = (
            stop_status
            if stop_status == "cancelled"
            else "timed_out"
            if scenario_timed_out
            else "completed"
            if completed_repeats == max(1, repeats)
            else "not_run"
        )
        samples = [float(row["elapsed_seconds"]) for row in run_rows if row["complete"]]
        sorted_samples = sorted(samples)
        p95 = sorted_samples[max(0, int(len(sorted_samples) * 0.95 + 0.999999) - 1)] if sorted_samples else None
        cpu_delta = None
        if resource_before["cpu_time_seconds"] is not None and resource_after["cpu_time_seconds"] is not None:
            cpu_delta = round(float(resource_after["cpu_time_seconds"]) - float(resource_before["cpu_time_seconds"]), 3)
        scenario: dict[str, object] = {
            "name": name,
            "display_name": SCENARIO_LABELS[name],
            "search_mode": "miss_prevention" if precise else "normal",
            "engine_route": "Python compatibility/search path" if precise else "Rust fast-search path (Excel uses its specialized parser)",
            "existence_only": existence_only,
            "status": scenario_status,
            "time_budget_seconds": round(
                max(0.0, scenario_deadline - scenario_started), 3
            ) if scenario_deadline is not None else None,
            "repeats_requested": max(1, repeats),
            "repeats_completed": completed_repeats,
            "elapsed_seconds": round(scenario_wall, 6),
            "run_time_statistics": {
                "mean_seconds": round(sum(samples) / len(samples), 6) if samples else None,
                "median_seconds": round(statistics.median(sorted_samples), 6) if sorted_samples else None,
                "min_seconds": round(min(samples), 6) if samples else None,
                "max_seconds": round(max(samples), 6) if samples else None,
                "p95_seconds": round(p95, 6) if p95 is not None else None,
                "standard_deviation_seconds": round(statistics.stdev(samples), 6) if len(samples) > 1 else 0.0,
                "coefficient_of_variation_percent": round(100 * statistics.stdev(samples) / statistics.mean(samples), 2)
                if len(samples) > 1 and statistics.mean(samples)
                else 0.0 if samples else None,
            },
            "runs": run_rows,
            "files_processed": sum(int(row["files_processed"]) for row in run_rows),
            "measurement_coverage_percent": round(
                min(100.0, 100 * scenario_processed / max(1, len(paths) * max(1, repeats))), 2
            ),
            "nominal_input_bytes": sum(int(row["nominal_input_bytes"]) for row in run_rows),
            "match_files": total_match_files,
            "matches": total_matches,
            "skipped": total_skipped,
            "errors": total_errors,
            "skip_reason_codes": dict(skip_codes),
            "unknown_skip_categories": dict(unknown_skip_categories),
            "exception_types": dict(exception_types),
            "file_type_metrics": _finalize_metrics(type_metrics),
            "extension_metrics": _finalize_metrics(extension_metrics),
            "size_bucket_metrics": _finalize_metrics(size_metrics),
            "slow_files_anonymous": sorted(
                [
                    {
                        **row,
                        "elapsed_total_seconds": round(float(row["elapsed_total_seconds"]), 6),
                        "average_seconds": round(float(row["elapsed_total_seconds"]) / int(row["observations"]), 6),
                        "min_seconds": round(float(row["min_seconds"]), 6),
                        "max_seconds": round(float(row["max_seconds"]), 6),
                    }
                    for row in slow_file_stats.values()
                ],
                key=lambda row: float(row["max_seconds"]),
                reverse=True,
            )[:50],
            "resource": {
                "before": resource_before,
                "after": resource_after,
                "samples_interval_seconds": RESOURCE_SAMPLE_INTERVAL_SECONDS,
                "samples": resource_samples,
                "samples_truncated": resource_samples_truncated,
                "process_cpu_seconds": cpu_delta,
                "process_cpu_cores_equivalent": round(cpu_delta / scenario_wall, 3)
                if cpu_delta is not None and scenario_wall >= 0.25
                else None,
            },
        }
        scenarios.append(scenario)
        if stop_status == "cancelled":
            break

    overall_status = stop_status or ("timed_out" if budget_limited else "completed")
    has_issues = any(item["skipped"] or item["errors"] or item["matches"] for item in scenarios)
    overall_verdict = "INCOMPLETE" if overall_status != "completed" else "REVIEW" if has_issues or not paths else "PASS"
    wall_seconds = time.perf_counter() - wall_started
    benchmark_seconds = time.perf_counter() - benchmark_started
    return {
        "diagnostic_version": 5,
        "app_version": get_app_version(),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": overall_status,
        "verdict": overall_verdict,
        "workload": {
            "query": "randomized 192-bit negative-match query (query text redacted)",
            "match_behavior": "A fresh high-entropy query is generated per report and is expected not to occur in the target. Any match requires review.",
            "execution_model": "Files are measured sequentially through search_in_file; this is not end-to-end parallel directory-search timing.",
            "file_order": "Randomized once per report and shared by all scenarios and repeats; paths and random seed are not exported.",
            "scenario_scheduling": "The total time budget is divided into cumulative equal time slices so later scenarios receive measurement time even when earlier scenarios are slow.",
            "query_strategy": "One cryptographically random 192-bit URL-safe token per report; the token and random source are not exported.",
        },
        "target": {
            "root_type": "directory",
            "file_count": len(paths),
            "total_bytes": inventory_bytes,
            "files_over_1gib": files_over_1gib,
            "types": dict(inventory_types),
            "extensions": dict(inventory_extensions),
            "size_buckets": dict(inventory_sizes),
            "directory_depth_buckets": dict(inventory_depths),
            "stat_failures": stat_failures,
            "hidden_files_excluded": hidden_files_excluded,
        },
        "timing": {
            "inventory_seconds": round(inventory_seconds, 6),
            "metadata_seconds": round(metadata_seconds, 6),
            "benchmark_seconds": round(benchmark_seconds, 6),
            "total_wall_seconds": round(wall_seconds, 6),
            "budget_seconds_excluding_inventory": max_seconds,
        },
        "diagnostic_config": {
            "repeats": max(1, repeats),
            "scenario_count": len(SCENARIOS),
            "max_seconds": max_seconds,
            "total_work_units": total_work,
            "completed_work_units": completed_work,
            "completion_percent": round(100 * completed_work / total_work, 2) if total_work else 100.0,
            "scenario_time_slicing": True,
            "resource_sample_interval_seconds": RESOURCE_SAMPLE_INTERVAL_SECONDS,
        },
        "search_policy": search_policy,
        "environment": environment,
        "scenarios": scenarios,
        "privacy": {
            "paths_included": False,
            "contents_included": False,
            "query_included": False,
            "file_ids": "HMAC-SHA256 pseudonyms salted per report; correlate repeats only within this report.",
        },
    }


def _duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "not measured"
    value = max(0.0, float(seconds))
    if value < 1:
        return f"{value * 1000:.2f}ms"
    if value < 60:
        return f"{value:.2f}s"
    total = int(value)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}h {minutes}m {secs}s" if hours else f"{minutes}m {secs}s"


def _format_bytes(byte_count: int | None) -> str:
    if byte_count is None:
        return "unknown"
    value = max(0, int(byte_count))
    for unit, divisor in (("GiB", 1024**3), ("MiB", 1024**2), ("KiB", 1024)):
        if value >= divisor:
            return f"{value / divisor:,.2f} {unit}"
    return f"{value:,} bytes"




def _markdown(report: dict[str, object]) -> str:
    target = report["target"]
    timing = report["timing"]
    config = report["diagnostic_config"]
    env = report["environment"]
    verdict = {"PASS": "PASS", "REVIEW": "REVIEW REQUIRED", "INCOMPLETE": "INCOMPLETE"}.get(
        str(report.get("verdict")), str(report.get("verdict"))
    )
    lines = [
        "# StringFinder Performance Diagnostic",
        "",
        f"**Overall verdict:** {verdict}  |  status: `{report['status']}`  |  app: `{report['app_version']}`",
        "",
        f"- Created (UTC): `{report['created_at_utc']}`",
        f"- Target: **{target['file_count']:,} files**, {_format_bytes(target['total_bytes'])}",
        f"- Progress: **{config['completed_work_units']:,}/{config['total_work_units']:,} units ({config['completion_percent']}%)**; repeats {config['repeats']}; budget {_duration(config['max_seconds'])}",
        f"- Time: total {_duration(timing['total_wall_seconds'])}; inventory {_duration(timing['inventory_seconds'])}; metadata {_duration(timing['metadata_seconds'])}; measured {_duration(timing['benchmark_seconds'])}",
        "",
        "## Interpretation",
        "",
        "A fresh random 192-bit query is intended to be absent. Any match makes that scenario REVIEW REQUIRED.",
        "This diagnostic calls `search_in_file` sequentially; it is not the wall time of parallel folder search.",
        "Throughput is nominal input size divided by elapsed time, not guaranteed physical disk throughput.",
        "All scenarios share one randomized file order and receive cumulative equal time slices, so a timeout still attempts every mode.",
        f"Process and system resources are sampled every {config['resource_sample_interval_seconds']} seconds; samples contain no paths or file contents.",
    ]
    if not target["file_count"]:
        lines.append("No searchable files were found; timing conclusions are invalid.")
    if env.get("memory_available_mb_at_start") and env.get("memory_total_mb"):
        pct = 100 * float(env["memory_available_mb_at_start"]) / float(env["memory_total_mb"])
        if pct < 10:
            lines.append(f"WARNING: only {pct:.1f}% of system memory was available at start.")
    policy = report["search_policy"]
    lines.extend(["", "## Effective search settings", "", f"- Hidden files/folders excluded: `{policy['exclude_hidden']}`; excluded file count: {target['hidden_files_excluded']:,}", f"- Binary files excluded: `{policy['exclude_binary']}`", "", "| Advanced setting | Effective value |", "|---|---:|"])
    for key, value in sorted(policy["advanced_settings"].items()):
        lines.append(f"| `{key}` | {value} |")
    lines.extend(["", "## Scenario summary", "", "| Scenario | Status | Coverage | Runs | Mean | Median | P95 | Files/s | Matches | Skipped | Errors |", "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"])
    for item in report["scenarios"]:
        stats = item["run_time_statistics"]
        lines.append(
            f"| {item['display_name']} | {item['status']} | {item['measurement_coverage_percent']}% | {item['repeats_completed']}/{item['repeats_requested']} | "
            f"{_duration(stats['mean_seconds'])} | {_duration(stats['median_seconds'])} | {_duration(stats['p95_seconds'])} | "
            f"{item['runs'][0]['files_per_second'] if item['runs'] else '—'} | {item['matches']:,} | {item['skipped']:,} | {item['errors']:,} |"
        )
    for item in report["scenarios"]:
        if item["matches"]:
            lines.append(f"WARNING: {item['display_name']} produced unexpected diagnostic-query matches ({item['matches']}).")
    lines.extend(["", "## Skip and exception codes", ""])
    for item in report["scenarios"]:
        if item["skip_reason_codes"] or item["exception_types"]:
            lines.append(f"- **{item['display_name']}**: skips `{item['skip_reason_codes'] or {}}`; exceptions `{item['exception_types'] or {}}`")
            if item.get("unknown_skip_categories"):
                lines.append(
                    f"  - Recognized ERR_UNKNOWN clues (raw details omitted): `{item['unknown_skip_categories']}`"
                )
    if not any(item["skip_reason_codes"] or item["exception_types"] for item in report["scenarios"]):
        lines.append("No skips or exceptions were recorded.")
    lines.extend(["", "## Per-format work profile", "", "| Scenario | Format | Files | Time | Average/file | Skipped |", "|---|---|---:|---:|---:|---:|"])
    for item in report["scenarios"]:
        for file_type, metric in sorted(item["file_type_metrics"].items()):
            lines.append(
                f"| {item['display_name']} | {file_type} | {metric['files']:,} | {_duration(metric['elapsed_seconds'])} | "
                f"{_duration(metric['average_file_seconds'])} | {metric['skipped']:,} |"
            )
    lines.extend(["", "## Per-size work profile", "", "| Scenario | Size range | Files | Time | Average/file | Skipped |", "|---|---|---:|---:|---:|---:|"])
    for item in report["scenarios"]:
        for size_bucket, metric in sorted(item["size_bucket_metrics"].items()):
            lines.append(
                f"| {item['display_name']} | {size_bucket} | {metric['files']:,} | {_duration(metric['elapsed_seconds'])} | "
                f"{_duration(metric['average_file_seconds'])} | {metric['skipped']:,} |"
            )
    lines.extend(["", "## Resource samples", "", "| Scenario | Samples | Peak sampled RSS | Lowest available system memory | Highest sampled CPU use | Sampled read | Sampled write |", "|---|---:|---:|---:|---:|---:|---:|"])
    for item in report["scenarios"]:
        points = item["resource"]["samples"]
        rss_values = [float(point["rss_mb"]) for point in points if point.get("rss_mb") is not None]
        available_values = [float(point["system_memory_available_mb"]) for point in points if point.get("system_memory_available_mb") is not None]
        cpu_values = [float(point["cpu_cores_equivalent"]) for point in points if point.get("cpu_cores_equivalent") is not None]
        read_values = [int(point["read_bytes_since_previous_sample"]) for point in points if point.get("read_bytes_since_previous_sample") is not None]
        write_values = [int(point["write_bytes_since_previous_sample"]) for point in points if point.get("write_bytes_since_previous_sample") is not None]
        lines.append(
            f"| {item['display_name']} | {len(points)} | {_format_bytes(int(max(rss_values) * 1024 * 1024)) if rss_values else 'unavailable'} | "
            f"{_format_bytes(int(min(available_values) * 1024 * 1024)) if available_values else 'unavailable'} | "
            f"{max(cpu_values):.2f} cores | {_format_bytes(sum(read_values)) if read_values else 'unavailable'} | "
            f"{_format_bytes(sum(write_values)) if write_values else 'unavailable'} |"
        )
    lines.extend(
        [
            "",
            "## Dataset profile",
            "",
            f"- Files larger than 1 GiB: **{target['files_over_1gib']:,}**",
            "",
            "| Dimension | Value | Files |",
            "|---|---|---:|",
        ]
    )
    for dimension, label in (("types", "type"), ("extensions", "extension"), ("size_buckets", "size"), ("directory_depth_buckets", "depth")):
        for key, count in sorted(target[dimension].items()):
            lines.append(f"| {label} | {key} | {count:,} |")
    lines.extend(["", "## Environment", "", f"- OS: `{env.get('os')}`; Python: `{env.get('python_implementation')} {env.get('python_version')}`", f"- CPU: `{env.get('processor') or env.get('machine')}`; logical CPUs: {env.get('logical_cpus')}", f"- Memory: {env.get('memory_total_mb', 'unknown')} MiB total; {env.get('memory_available_mb_at_start', 'unknown')} MiB available at start", f"- Rust engine: `{env.get('rust_engine')}`; dependencies: `{env.get('dependencies', {})}`", "", "## Privacy and limitations", "", "Paths, filenames, file contents, sheet names, and the generated query are not exported. Anonymous file IDs are report-scoped HMAC pseudonyms. Exact file sizes and environment/resource data are included."])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a one-shot local StringFinder performance diagnostic")
    parser.add_argument("folder", type=Path, help="diagnostic target folder")
    parser.add_argument("--repeats", type=int, default=5, choices=range(1, 6), help="runs per scenario (default: 5)")
    parser.add_argument("--output", type=Path, default=Path("stringfinder_diagnostic.json"))
    parser.add_argument(
        "--max-seconds",
        type=int,
        default=DEFAULT_MAX_SECONDS,
        help=f"maximum benchmark time after file enumeration (default: {DEFAULT_MAX_SECONDS}s; 0 disables)",
    )
    args = parser.parse_args()
    if not args.folder.is_dir():
        parser.error(f"Folder does not exist: {args.folder}")
    report = run(args.folder, args.repeats, max_seconds=None if args.max_seconds == 0 else max(1, args.max_seconds))
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    args.output.with_suffix(".md").write_text(_markdown(report), encoding="utf-8")
    print(f"Diagnostic report written: {args.output} and {args.output.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
