"""Run a one-shot, privacy-preserving performance diagnostic on a folder.

The fixed query is intentionally not written to the report. Only aggregate
counts, timings, resource usage, and result-contract counters are exported.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import platform
import secrets
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from core.search_engine import search_in_file  # noqa: E402
from core.skip_reason_codes import TEMPLATE_NAMES, decode_skip_reason  # noqa: E402
from sf_utils.app_strings import AppStrings  # noqa: E402
from sf_utils.constants import Constants  # noqa: E402
from sf_utils.version_helper import get_app_version  # noqa: E402

FIXED_QUERY = "StringFinderDiagnosticNeedle"
DEFAULT_MAX_SECONDS = 7200
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
    return "100MB+"


def _files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*") if path.is_file() and "$recycle.bin" not in {p.lower() for p in path.parts}]


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


def _duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "측정 안 됨"
    value = max(0.0, float(seconds))
    if value < 1:
        return f"{value * 1000:.2f}ms"
    if value < 60:
        return f"{value:.2f}초"
    total = int(value)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}시간 {minutes}분 {secs}초" if hours else f"{minutes}분 {secs}초"


def _format_bytes(byte_count: int | None) -> str:
    if byte_count is None:
        return "정보 없음"
    value = max(0, int(byte_count))
    for unit, divisor in (("GiB", 1024**3), ("MiB", 1024**2), ("KiB", 1024)):
        if value >= divisor:
            return f"{value / divisor:,.2f} {unit}"
    return f"{value:,} bytes"


def _run_legacy(
    root: Path,
    repeats: int,
    progress_callback=None,
    cancel_event=None,
    max_seconds: int | None = DEFAULT_MAX_SECONDS,
) -> dict[str, object]:
    root = Path(root)
    inventory_started = time.perf_counter()
    paths = _files(root)
    inventory_elapsed = round(time.perf_counter() - inventory_started, 6)
    # File enumeration can be slow on network folders.  It should be reported,
    # but must not consume the budget intended for the actual benchmark.
    started = time.perf_counter()
    file_types = Counter(_classify(path) for path in paths)
    size_buckets = Counter()
    total_bytes = 0
    stat_failures = 0
    metadata: dict[Path, tuple[int | None, str, str | None]] = {}
    for path in paths:
        try:
            size = path.stat().st_size
        except OSError:
            stat_failures += 1
            metadata[path] = (None, _classify(path), None)
            continue
        total_bytes += size
        bucket = _size_bucket(size)
        size_buckets[bucket] += 1
        metadata[path] = (size, _classify(path), bucket)

    scenarios: list[dict[str, object]] = []
    total_work = len(paths) * max(1, repeats) * len(SCENARIOS)
    completed_work = 0
    for name, precise, existence_only, _ in SCENARIOS:
        samples: list[float] = []
        runs: list[dict[str, object]] = []
        match_files = matches = skipped = 0
        scenario_type_metrics: dict[str, dict[str, object]] = defaultdict(
            lambda: {"files": 0, "bytes": 0, "elapsed_seconds": 0.0, "matches": 0, "skipped": 0, "errors": 0}
        )
        scenario_size_metrics: dict[str, dict[str, object]] = defaultdict(
            lambda: {"files": 0, "bytes": 0, "elapsed_seconds": 0.0, "matches": 0, "skipped": 0, "errors": 0}
        )
        slowest_files: list[dict[str, object]] = []
        rss_before = _rss_mb()
        scenario_start = time.perf_counter()
        for iteration in range(max(1, repeats)):
            iteration_start = time.perf_counter()
            iteration_match_files = iteration_matches = iteration_skipped = 0
            iteration_errors = 0
            iteration_bytes = 0
            iteration_files = 0
            for path in paths:
                if cancel_event is not None and cancel_event.is_set():
                    raise RuntimeError("DIAGNOSTIC_CANCELLED")
                if max_seconds is not None and time.perf_counter() - started > max_seconds:
                    raise RuntimeError("DIAGNOSTIC_TIMEOUT")
                file_started = time.perf_counter()
                file_size, file_type, file_bucket = metadata.get(path, (None, _classify(path), None))
                file_matches = 0
                file_skipped = 0
                file_errors = 0
                try:
                    result = search_in_file(
                        str(path),
                        FIXED_QUERY,
                        file_size=file_size,
                        use_complex_search=precise,
                        existence_only=existence_only,
                    )
                except Exception:
                    skipped += 1
                    iteration_skipped += 1
                    iteration_errors += 1
                    file_skipped = 1
                    file_errors = 1
                else:
                    if isinstance(result, tuple) and result and result[0] == Constants.STATUS_SKIPPED:
                        skipped += 1
                        iteration_skipped += 1
                        file_skipped = 1
                    elif isinstance(result, tuple):
                        match_files += 1
                        iteration_match_files += 1
                        try:
                            match_count = int(result[1] or 0)
                            matches += match_count
                            iteration_matches += match_count
                            file_matches = match_count
                        except (IndexError, TypeError, ValueError):
                            pass
                finally:
                    elapsed = time.perf_counter() - file_started
                    iteration_files += 1
                    if file_size is not None:
                        iteration_bytes += file_size
                    for key, value in ((file_type, scenario_type_metrics), (file_bucket, scenario_size_metrics)):
                        if key is None:
                            continue
                        metric = value[key]
                        metric["files"] += 1
                        metric["bytes"] += file_size or 0
                        metric["elapsed_seconds"] += elapsed
                        metric["matches"] += file_matches
                        metric["skipped"] += file_skipped
                        metric["errors"] += file_errors
                    slowest_files.append(
                        {
                            "elapsed_seconds": round(elapsed, 6),
                            "file_type": file_type,
                            "size_bucket": file_bucket,
                            "size_bytes": file_size,
                        }
                    )
                    if len(slowest_files) > 25:
                        slowest_files.sort(key=lambda item: float(item["elapsed_seconds"]), reverse=True)
                        del slowest_files[25:]
                    # Every attempted file advances progress, including files
                    # that fail or return no matches.
                    completed_work += 1
                    if progress_callback:
                        progress_callback(completed_work, total_work, name, str(path))
            elapsed = round(time.perf_counter() - iteration_start, 6)
            samples.append(elapsed)
            runs.append({
                "run": iteration + 1,
                "elapsed_seconds": elapsed,
                "files_processed": iteration_files,
                "bytes_processed": iteration_bytes,
                "files_per_second": round(iteration_files / elapsed, 3) if elapsed else None,
                "mb_per_second": round(iteration_bytes / (1024 * 1024) / elapsed, 3) if elapsed else None,
                "match_files": iteration_match_files,
                "matches": iteration_matches,
                "skipped": iteration_skipped,
                "errors": iteration_errors,
                "verdict": "PASS" if iteration_errors == 0 and iteration_skipped == 0 else "REVIEW",
            })
        rss_after = _rss_mb()
        scenarios.append(
            {
                "name": name,
                "repeats": max(1, repeats),
                "elapsed_seconds": round(time.perf_counter() - scenario_start, 6),
                "sample_seconds": samples,
                "runs": runs,
                "match_files": match_files,
                "matches": matches,
                "skipped": skipped,
                "rss_before_mb": rss_before,
                "rss_after_mb": rss_after,
                "engine_path": "python" if precise else "rust",
                "existence_only": existence_only,
                "file_type_metrics": {
                    key: {
                        **metric,
                        "elapsed_seconds": round(float(metric["elapsed_seconds"]), 6),
                        "files_per_second": round(int(metric["files"]) / float(metric["elapsed_seconds"]), 3)
                        if metric["elapsed_seconds"]
                        else None,
                    }
                    for key, metric in scenario_type_metrics.items()
                },
                "size_bucket_metrics": {
                    key: {
                        **metric,
                        "elapsed_seconds": round(float(metric["elapsed_seconds"]), 6),
                        "files_per_second": round(int(metric["files"]) / float(metric["elapsed_seconds"]), 3)
                        if metric["elapsed_seconds"]
                        else None,
                    }
                    for key, metric in scenario_size_metrics.items()
                },
                "slowest_files_anonymous": sorted(
                    slowest_files,
                    key=lambda item: float(item["elapsed_seconds"]),
                    reverse=True,
                ),
            }
        )

    return {
        "diagnostic_version": 2,
        "app_version": get_app_version(),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "fixed_query": "<redacted>",
        "target": {"root_type": "directory", "file_count": len(paths), "total_bytes": total_bytes, "types": dict(file_types), "size_buckets": dict(size_buckets)},
        "inventory_elapsed_seconds": inventory_elapsed,
        "inventory_stat_failures": stat_failures,
        "diagnostic_config": {
            "repeats": max(1, repeats),
            "scenario_count": len(SCENARIOS),
            "max_seconds": max_seconds,
            "total_work_units": total_work,
            "completed_work_units": completed_work,
        },
        "hardware": _hardware(),
        "scenarios": scenarios,
        "total_elapsed_seconds": round(time.perf_counter() - started, 6),
    }


def _markdown_legacy(report: dict[str, object]) -> str:
    target = report["target"]
    lines = ["# StringFinder 성능 진단 리포트", "", f"- 앱 버전: `{report['app_version']}`", f"- 생성 시각(UTC): `{report['created_at_utc']}`", "", "## 대상 집계", "", f"- 파일 수: {target['file_count']}", f"- 총 용량: {target['total_bytes']} bytes", f"- 형식별 파일 수: `{target['types']}`", f"- 크기 구간별 파일 수: `{target['size_buckets']}`", "", "## 시나리오", "", "| 시나리오 | 반복 | 샘플 시간(초) | 매치 파일 | 매치 수 | 스킵 |", "|---|---:|---|---:|---:|---:|"]
    for item in report["scenarios"]:
        lines.append(f"| {item['name']} | {item['repeats']} | {item['sample_seconds']} | {item['match_files']} | {item['matches']} | {item['skipped']} |")
        lines.append("")
        lines.append("회차별 판정:")
        for run in item["runs"]:
            lines.append(f"- {run['run']}회: `{run['verdict']}` / {run['elapsed_seconds']}s / 매치 {run['matches']} / 스킵 {run['skipped']} / 오류 {run['errors']}")
    lines.extend(["", "검색어·파일 경로·파일 내용은 리포트에 저장하지 않았습니다.", ""])
    return "\n".join(lines)


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
    root = Path(root)
    inventory_started = time.perf_counter()
    paths = _files(root)
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

    benchmark_started = time.perf_counter()
    total_work = max(1, len(paths) * max(1, repeats) * len(SCENARIOS))
    completed_work = 0
    scenarios: list[dict[str, object]] = []
    stop_status: str | None = None

    for name, precise, existence_only, _unused in SCENARIOS:
        scenario_started = time.perf_counter()
        resource_before = _resource_snapshot()
        run_rows: list[dict[str, object]] = []
        type_metrics: dict[str, dict[str, object]] = defaultdict(_new_metric)
        extension_metrics: dict[str, dict[str, object]] = defaultdict(_new_metric)
        size_metrics: dict[str, dict[str, object]] = defaultdict(_new_metric)
        skip_codes: Counter[str] = Counter()
        exception_types: Counter[str] = Counter()
        slow_file_stats: dict[str, dict[str, object]] = {}
        total_matches = total_match_files = total_skipped = total_errors = 0

        for iteration in range(max(1, repeats)):
            iteration_started = time.perf_counter()
            processed = nominal_bytes = match_files = matches = skipped = errors = 0
            run_skip_codes: Counter[str] = Counter()
            run_exception_types: Counter[str] = Counter()
            interrupted = False

            for path in paths:
                if cancel_event is not None and cancel_event.is_set():
                    stop_status = "cancelled"
                    interrupted = True
                    break
                if max_seconds is not None and time.perf_counter() - benchmark_started >= max_seconds:
                    stop_status = "timed_out"
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
                exception_name: str | None = None
                file_started = time.perf_counter()
                try:
                    result = search_in_file(
                        str(path),
                        FIXED_QUERY,
                        file_size=file_size,
                        use_complex_search=precise,
                        existence_only=existence_only,
                    )
                    if isinstance(result, tuple) and result and result[0] == Constants.STATUS_SKIPPED:
                        skipped += 1
                        file_skip_code = _skip_code(result[1] if len(result) > 1 else "")
                        run_skip_codes[file_skip_code] += 1
                        skip_codes[file_skip_code] += 1
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
                                if exception_name:
                                    types = candidate["exception_types"]
                                    types[exception_name] = types.get(exception_name, 0) + 1

                    completed_work += 1
                    if progress_callback:
                        progress_callback(completed_work, total_work, name, str(path))

            elapsed_iteration = time.perf_counter() - iteration_started
            if processed or not paths:
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
        scenario_wall = time.perf_counter() - scenario_started
        completed_repeats = sum(1 for row in run_rows if row["complete"])
        scenario_status = stop_status or ("completed" if completed_repeats == max(1, repeats) else "not_run")
        samples = [float(row["elapsed_seconds"]) for row in run_rows if row["complete"]]
        sorted_samples = sorted(samples)
        p95 = sorted_samples[max(0, int(len(sorted_samples) * 0.95 + 0.999999) - 1)] if sorted_samples else None
        cpu_delta = None
        if resource_before["cpu_time_seconds"] is not None and resource_after["cpu_time_seconds"] is not None:
            cpu_delta = round(float(resource_after["cpu_time_seconds"]) - float(resource_before["cpu_time_seconds"]), 3)
        scenario = {
            "name": name,
            "display_name": SCENARIO_LABELS[name],
            "search_mode": "miss_prevention" if precise else "normal",
            "engine_route": "Python compatibility/search path" if precise else "Rust fast-search path (Excel uses its specialized parser)",
            "existence_only": existence_only,
            "status": scenario_status,
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
            "nominal_input_bytes": sum(int(row["nominal_input_bytes"]) for row in run_rows),
            "match_files": total_match_files,
            "matches": total_matches,
            "skipped": total_skipped,
            "errors": total_errors,
            "skip_reason_codes": dict(skip_codes),
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
                "process_cpu_seconds": cpu_delta,
                "process_cpu_cores_equivalent": round(cpu_delta / scenario_wall, 3)
                if cpu_delta is not None and scenario_wall >= 0.25
                else None,
            },
        }
        scenarios.append(scenario)
        if stop_status:
            break

    overall_status = stop_status or "completed"
    has_issues = any(item["skipped"] or item["errors"] or item["matches"] for item in scenarios)
    overall_verdict = "INCOMPLETE" if stop_status else "REVIEW" if has_issues or not paths else "PASS"
    wall_seconds = time.perf_counter() - wall_started
    benchmark_seconds = time.perf_counter() - benchmark_started
    return {
        "diagnostic_version": 3,
        "app_version": get_app_version(),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": overall_status,
        "verdict": overall_verdict,
        "workload": {
            "query": "fixed negative-match query (query text redacted)",
            "match_behavior": "The fixed query is intended not to match; this run primarily measures negative-search cost.",
            "execution_model": "Files are measured sequentially through search_in_file; this is not end-to-end parallel directory-search timing.",
        },
        "target": {
            "root_type": "directory",
            "file_count": len(paths),
            "total_bytes": inventory_bytes,
            "types": dict(inventory_types),
            "extensions": dict(inventory_extensions),
            "size_buckets": dict(inventory_sizes),
            "directory_depth_buckets": dict(inventory_depths),
            "stat_failures": stat_failures,
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
        },
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


def _markdown_corrupt_legacy(report: dict[str, object]) -> str:
    target = report["target"]
    timing = report["timing"]
    environment = report["environment"]
    config = report["diagnostic_config"]
    verdict_label = {"PASS": "정상 완료", "REVIEW": "검토 필요", "INCOMPLETE": "부분 완료"}.get(
        str(report.get("verdict")), str(report.get("verdict"))
    )
    lines = [
        "# StringFinder 성능 진단 결과",
        "",
        f"**종합 판정: {verdict_label}** · 상태 `{report['status']}` · 앱 `{report['app_version']}`",
        "",
        f"- 실행 시각(UTC): `{report['created_at_utc']}`",
        f"- 총 경과: **{_duration(timing['total_wall_seconds'])}** (파일 열거 {_duration(timing['inventory_seconds'])}, 메타데이터 {_duration(timing['metadata_seconds'])}, 검색 측정 {_duration(timing['benchmark_seconds'])})",
        f"- 대상: **{target['file_count']:,}개 파일**, {_format_bytes(target['total_bytes'])}",
        f"- 진행률: **{config['completed_work_units']:,}/{config['total_work_units']:,} 작업 단위 ({config['completion_percent']}%)** · 반복 {config['repeats']}회 · 제한 {_duration(config['max_seconds'])}",
        "",
        "## 먼저 볼 요약",
        "",
    ]
    available_start = environment.get("memory_available_mb_at_start")
    total_memory = environment.get("memory_total_mb")
    if isinstance(available_start, (int, float)) and isinstance(total_memory, (int, float)) and total_memory > 0:
        available_percent = 100 * available_start / total_memory
        if available_percent < 10:
            lines.append(
                f"- **측정 환경 주의:** 시작 시 가용 메모리가 {available_percent:.1f}%로 낮았습니다. "
                "메모리 압박이 검색 시간에 영향을 주었을 수 있습니다."
            )
    scenarios = report["scenarios"]
    if not scenarios:
        lines.append("검색 시나리오가 시작되기 전에 진단이 중단되어 대상 인벤토리만 기록했습니다.")
    else:
        if target["file_count"] == 0:
            lines.append("- 대상 폴더에서 검색 가능한 파일을 찾지 못했습니다. 측정 표본이 없어 결과 판정은 검토 필요입니다.")
        fastest_complete = [
            item for item in scenarios if item["status"] == "completed" and item["run_time_statistics"]["mean_seconds"] is not None
        ]
        if fastest_complete:
            fastest = min(fastest_complete, key=lambda item: float(item["run_time_statistics"]["mean_seconds"]))
            slowest = max(fastest_complete, key=lambda item: float(item["run_time_statistics"]["mean_seconds"]))
            lines.append(
                f"- 완료된 모드 중 회당 평균: 가장 짧은 `{fastest['display_name']}` {_duration(fastest['run_time_statistics']['mean_seconds'])}; "
                f"가장 긴 `{slowest['display_name']}` {_duration(slowest['run_time_statistics']['mean_seconds'])}."
            )
        matched_scenarios = [item for item in scenarios if item["matches"]]
        for item in matched_scenarios:
            lines.append(
                f"- `{item['display_name']}`에서 고정 검색어 매치 {item['matches']:,}건을 찾았습니다. "
                "음성 매치 비용을 대표하지 않을 수 있으므로 해당 시나리오는 검토가 필요합니다."
            )
        issue_scenarios = [item for item in scenarios if item["skipped"] or item["errors"]]
        if issue_scenarios:
            for item in issue_scenarios:
                lines.append(
                    f"- `{item['display_name']}`: 스킵 {item['skipped']:,}건, 예외 {item['errors']:,}건. "
                    f"스킵 코드는 `{item['skip_reason_codes']}`를 확인하세요."
                )
        else:
            lines.append("- 현재까지 완료된 시나리오에서 스킵이나 예외가 기록되지 않았습니다.")
        bottleneck_scenario = next((item for item in scenarios if item["search_mode"] == "miss_prevention" and item["status"] == "completed"), None)
        if bottleneck_scenario:
            metrics = bottleneck_scenario["file_type_metrics"]
            total = sum(float(metric["elapsed_seconds"]) for metric in metrics.values())
            if total > 0 and metrics:
                dominant_type, dominant_metric = max(metrics.items(), key=lambda pair: float(pair[1]["elapsed_seconds"]))
                share = 100 * float(dominant_metric["elapsed_seconds"]) / total
                lines.append(
                    f"- 누락 방지 검색의 가장 큰 형식별 시간 비중: `{dominant_type}` {share:.1f}% "
                    f"({ _duration(dominant_metric['elapsed_seconds']) })."
                )
    lines.extend(
        [
            "",
            "> **해석 주의:** 고정 검색어는 의도적으로 매치가 없도록 설계되어 부정 검색 비용을 측정합니다. "
            "파일을 순차 처리하므로 앱의 병렬 폴더 검색 총시간과 같지 않습니다. MB/s는 실제 읽기량이 아니라 "
            "처리 대상 파일 크기 합산 기준의 참고값입니다. 존재 확인에서 파일이 스킵되면 해당 속도 비교는 동등한 검색 범위가 아닙니다.",
            "",
            "## 시나리오별 결과",
            "",
        "| 시나리오 | 상태 | 완료 반복 | 회당 평균 | 중앙값 | 최장 | 파일/초 | 매치 | 스킵 | 예외 | 판정 |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for item in scenarios:
        stats = item["run_time_statistics"]
        files_per_second = None
        completed_runs = [row for row in item["runs"] if row["complete"]]
        if completed_runs:
            values = [row["files_per_second"] for row in completed_runs if row["files_per_second"] is not None]
            files_per_second = round(sum(values) / len(values), 1) if values else None
        status_label = "완료" if item["status"] == "completed" else "시간 초과" if item["status"] == "timed_out" else "취소/미실행"
        verdict = "INCOMPLETE" if item["status"] != "completed" else "REVIEW" if item["skipped"] or item["errors"] or target["file_count"] == 0 else "PASS"
        lines.append(
            f"| {item['display_name']} | {status_label} | {item['repeats_completed']}/{item['repeats_requested']} | "
            f"{_duration(stats['mean_seconds'])} | {_duration(stats['median_seconds'])} | {_duration(stats['max_seconds'])} | "
            f"{files_per_second if files_per_second is not None else '—'} | {item['matches']:,} | {item['skipped']:,} | {item['errors']:,} | `{verdict}` |"
        )
    for item in scenarios:
        lines.extend(["", f"### {item['display_name']}", ""])
        lines.append(
            f"처리 경로: {item['engine_route']} · 반복별 완료 시간: "
            + (", ".join(_duration(row["elapsed_seconds"]) for row in item["runs"]) or "없음")
        )
        stats = item["run_time_statistics"]
        lines.append(
            f"측정 변동: 표준편차 {_duration(stats['standard_deviation_seconds'])} · "
            f"변동계수 {stats['coefficient_of_variation_percent']}% · P95 {_duration(stats['p95_seconds'])}"
        )
        lines.extend(["", "| 형식 | 파일 시도 | 소요 | 시간 비중 | 평균/파일 | 최대 단일 파일 | 스킵 | 예외 |", "|---|---:|---:|---:|---:|---:|---:|---:|"])
        metrics = item["file_type_metrics"]
        total_seconds = sum(float(metric["elapsed_seconds"]) for metric in metrics.values())
        for file_type, metric in sorted(metrics.items(), key=lambda pair: float(pair[1]["elapsed_seconds"]), reverse=True):
            share = 100 * float(metric["elapsed_seconds"]) / total_seconds if total_seconds else 0
            lines.append(
                f"| {file_type} | {metric['files']:,} | {_duration(metric['elapsed_seconds'])} | {share:.1f}% | "
                f"{float(metric['average_file_seconds'] or 0) * 1000:.2f}ms | {_duration(metric['max_file_seconds'])} | "
                f"{metric['skipped']:,} | {metric['errors']:,} |"
            )
        extension_metrics = item["extension_metrics"]
        if extension_metrics:
            lines.extend(["", "확장자별 상세:", "", "| 확장자 | 파일 시도 | 소요 | 평균/파일 | 최대 단일 파일 | 명목 MB/s | 스킵 |", "|---|---:|---:|---:|---:|---:|---:|"])
            for extension, metric in sorted(
                extension_metrics.items(), key=lambda pair: float(pair[1]["elapsed_seconds"]), reverse=True
            ):
                lines.append(
                    f"| {extension} | {metric['files']:,} | {_duration(metric['elapsed_seconds'])} | "
                    f"{float(metric['average_file_seconds'] or 0) * 1000:.2f}ms | "
                    f"{_duration(metric['max_file_seconds'])} | {metric['nominal_mb_per_second'] or '—'} | {metric['skipped']:,} |"
                )
        resource = item["resource"]
        before = resource["before"]
        after = resource["after"]
        rss_before = before["rss_mb"]
        rss_after = after["rss_mb"]
        available_before = before["system_memory_available_mb"]
        available_after = after["system_memory_available_mb"]
        io_read = None
        io_write = None
        if before["process_read_bytes"] is not None and after["process_read_bytes"] is not None:
            io_read = int(after["process_read_bytes"]) - int(before["process_read_bytes"])
        if before["process_write_bytes"] is not None and after["process_write_bytes"] is not None:
            io_write = int(after["process_write_bytes"]) - int(before["process_write_bytes"])
        lines.append(
            "\n리소스 변화: 프로세스 CPU "
            f"{resource['process_cpu_seconds'] if resource['process_cpu_seconds'] is not None else '정보 없음'}초"
            + (f" (~{resource['process_cpu_cores_equivalent']} 코어 사용량)" if resource["process_cpu_cores_equivalent"] is not None else "")
            + f" · RSS {rss_before if rss_before is not None else '—'} → {rss_after if rss_after is not None else '—'} MiB"
            + f" · 시스템 가용 메모리 {available_before if available_before is not None else '—'} → {available_after if available_after is not None else '—'} MiB"
            + f" · 프로세스 I/O 읽기/쓰기 {io_read if io_read is not None else '—'}/{io_write if io_write is not None else '—'} bytes"
        )
        if item["skip_reason_codes"]:
            lines.extend(["", f"스킵 코드별 누계: `{item['skip_reason_codes']}`"])
        if item["exception_types"]:
            lines.extend(["", f"예외 유형별 누계(메시지/경로 제외): `{item['exception_types']}`"])
        slow = item["slow_files_anonymous"][:10]
        if slow:
            lines.extend(["", "느린 파일 상위 10개 (익명 ID는 이 리포트 안에서만 같은 파일을 연결):", "", "| 익명 ID | 형식 | 크기 | 측정 횟수 | 평균 | 최장 | 스킵 코드 |", "|---|---|---:|---:|---:|---:|---|"])
            for observation in slow:
                size = _format_bytes(observation["size_bytes"])
                lines.append(
                    f"| `{observation['file_id']}` | {observation['extension']} | {size} | "
                    f"{observation['observations']}회 | {_duration(observation['average_seconds'])} | "
                    f"{_duration(observation['max_seconds'])} | {observation['skip_reason_codes'] or '—'} |"
                )
        lines.extend(["", "반복별 세부:", ""])
        for row in item["runs"]:
            lines.append(
                f"- {row['run']}회: `{row['verdict']}` · {row['files_processed']:,}개 파일 · "
                f"{_duration(row['elapsed_seconds'])} · {row['files_per_second'] or 0:.1f}파일/초 · "
                f"매치 {row['matches']:,} · 스킵 {row['skipped']:,} · 예외 {row['errors']:,}"
            )
    lines.extend(
        [
            "",
            "## 대상 폴더 구성",
            "",
            "| 구분 | 항목 | 파일 수 |",
            "|---|---|---:|",
        ]
    )
    for dimension, label in (("types", "형식"), ("extensions", "확장자"), ("size_buckets", "크기"), ("directory_depth_buckets", "폴더 깊이")):
        for key, count in sorted(target[dimension].items(), key=lambda pair: pair[0]):
            lines.append(f"| {label} | {key} | {count:,} |")
    lines.extend(
        [
            "",
            f"- 파일 정보 조회 실패: {target['stat_failures']:,}건",
            "",
            "## 실행 환경과 리소스",
            "",
            f"- OS: `{environment['os']}` · Python: `{environment['python_implementation']} {environment['python_version']}`",
            f"- CPU: `{environment['processor'] or environment['machine']}` · 논리 코어 {environment['logical_cpus']}개",
            f"- 메모리: 전체 {environment.get('memory_total_mb', '정보 없음')} MiB · 진단 시작 시 가용 {environment.get('memory_available_mb_at_start', '정보 없음')} MiB",
            f"- Rust 엔진: `{environment['rust_engine']}` · 의존성: `{environment.get('dependencies', {})}`",
            "",
            "## 측정값과 개인정보 안내",
            "",
            "- `명목 MB/s`와 `명목 입력 바이트`는 처리한 파일 크기를 합산한 값이며 실제 디스크 읽기량과 다를 수 있습니다(특히 조기 종료 검색).",
            "- 리포트에는 검색어·파일 경로·파일명·파일 내용·시트명이 포함되지 않습니다. 익명 파일 ID는 보고서별 비밀 salt로 생성되어 이 리포트 내부에서만 같은 파일 관측치를 연결합니다.",
            "- CPU/메모리 값은 각 시나리오 시작·종료 시점의 표본이며, Windows 최고 작업 집합 값은 프로세스 시작 후 누적 최고치일 수 있습니다.",
            "",
        ]
    )
    return "\n".join(lines)


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
        "The fixed query is intended to be absent. Any match makes that scenario REVIEW REQUIRED.",
        "This diagnostic calls `search_in_file` sequentially; it is not the wall time of parallel folder search.",
        "Throughput is nominal input size divided by elapsed time, not guaranteed physical disk throughput.",
    ]
    if not target["file_count"]:
        lines.append("No searchable files were found; timing conclusions are invalid.")
    if env.get("memory_available_mb_at_start") and env.get("memory_total_mb"):
        pct = 100 * float(env["memory_available_mb_at_start"]) / float(env["memory_total_mb"])
        if pct < 10:
            lines.append(f"WARNING: only {pct:.1f}% of system memory was available at start.")
    lines.extend(["", "## Scenario summary", "", "| Scenario | Status | Runs | Mean | Median | P95 | Files/s | Matches | Skipped | Errors |", "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"])
    for item in report["scenarios"]:
        stats = item["run_time_statistics"]
        lines.append(
            f"| {item['display_name']} | {item['status']} | {item['repeats_completed']}/{item['repeats_requested']} | "
            f"{_duration(stats['mean_seconds'])} | {_duration(stats['median_seconds'])} | {_duration(stats['p95_seconds'])} | "
            f"{item['runs'][0]['files_per_second'] if item['runs'] else '—'} | {item['matches']:,} | {item['skipped']:,} | {item['errors']:,} |"
        )
    for item in report["scenarios"]:
        if item["matches"]:
            lines.append(f"WARNING: {item['display_name']} produced fixed query match {item['matches']}.")
    lines.extend(["", "## Skip and exception codes", ""])
    for item in report["scenarios"]:
        if item["skip_reason_codes"] or item["exception_types"]:
            lines.append(f"- **{item['display_name']}**: skips `{item['skip_reason_codes'] or {}}`; exceptions `{item['exception_types'] or {}}`")
    if not any(item["skip_reason_codes"] or item["exception_types"] for item in report["scenarios"]):
        lines.append("No skips or exceptions were recorded.")
    lines.extend(["", "## Dataset profile", "", "| Dimension | Value | Files |", "|---|---|---:|"])
    for dimension, label in (("types", "type"), ("extensions", "extension"), ("size_buckets", "size"), ("directory_depth_buckets", "depth")):
        for key, count in sorted(target[dimension].items()):
            lines.append(f"| {label} | {key} | {count:,} |")
    lines.extend(["", "## Environment", "", f"- OS: `{env.get('os')}`; Python: `{env.get('python_implementation')} {env.get('python_version')}`", f"- CPU: `{env.get('processor') or env.get('machine')}`; logical CPUs: {env.get('logical_cpus')}", f"- Memory: {env.get('memory_total_mb', 'unknown')} MiB total; {env.get('memory_available_mb_at_start', 'unknown')} MiB available at start", f"- Rust engine: `{env.get('rust_engine')}`; dependencies: `{env.get('dependencies', {})}`", "", "## Privacy and limitations", "", "Paths, filenames, file contents, sheet names, and the fixed query are not exported. Anonymous file IDs are report-scoped HMAC pseudonyms. Exact file sizes and environment/resource data are included."])
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
