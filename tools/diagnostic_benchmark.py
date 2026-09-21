"""Run a one-shot, privacy-preserving performance diagnostic on a folder.

The fixed query is intentionally not written to the report. Only aggregate
counts, timings, resource usage, and result-contract counters are exported.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from core.search_engine import search_in_file  # noqa: E402
from sf_utils.constants import Constants  # noqa: E402
from sf_utils.version_helper import get_app_version  # noqa: E402

FIXED_QUERY = "StringFinderDiagnosticNeedle"
SCENARIOS = (
    ("normal_no_match", False, False, False),
    ("normal_existence", False, True, False),
    ("miss_prevention_no_match", True, False, False),
    ("miss_prevention_existence", True, True, False),
)


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


def _files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*") if path.is_file() and "$recycle.bin" not in {p.lower() for p in path.parts}]


def run(root: Path, repeats: int, progress_callback=None, cancel_event=None, max_seconds: int = 1800) -> dict[str, object]:
    root = Path(root)
    started = time.perf_counter()
    paths = _files(root)
    file_types = Counter(_classify(path) for path in paths)
    size_buckets = Counter()
    total_bytes = 0
    for path in paths:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        total_bytes += size
        size_buckets["0-64KB" if size <= 64 * 1024 else "64KB-10MB" if size <= 10 * 1024 * 1024 else "10MB-100MB" if size <= 100 * 1024 * 1024 else "100MB+"] += 1

    scenarios: list[dict[str, object]] = []
    total_work = max(1, len(paths) * max(1, repeats) * len(SCENARIOS))
    completed_work = 0
    for name, precise, existence_only, _ in SCENARIOS:
        samples: list[float] = []
        runs: list[dict[str, object]] = []
        match_files = matches = skipped = 0
        rss_before = _rss_mb()
        scenario_start = time.perf_counter()
        for iteration in range(max(1, repeats)):
            iteration_start = time.perf_counter()
            iteration_match_files = iteration_matches = iteration_skipped = 0
            iteration_errors = 0
            for path in paths:
                if cancel_event is not None and cancel_event.is_set():
                    raise RuntimeError("DIAGNOSTIC_CANCELLED")
                if time.perf_counter() - started > max_seconds:
                    raise RuntimeError("DIAGNOSTIC_TIMEOUT")
                try:
                    result = search_in_file(
                        str(path), FIXED_QUERY, use_complex_search=precise, existence_only=existence_only
                    )
                except Exception:
                    skipped += 1
                    iteration_skipped += 1
                    iteration_errors += 1
                    continue
                if not result:
                    continue
                if isinstance(result, tuple) and result and result[0] == Constants.STATUS_SKIPPED:
                    skipped += 1
                    iteration_skipped += 1
                elif isinstance(result, tuple):
                    match_files += 1
                    iteration_match_files += 1
                    try:
                        match_count = int(result[1] or 0)
                        matches += match_count
                        iteration_matches += match_count
                    except (IndexError, TypeError, ValueError):
                        pass
                completed_work += 1
                if progress_callback:
                    progress_callback(completed_work, total_work, name, str(path))
            elapsed = round(time.perf_counter() - iteration_start, 6)
            samples.append(elapsed)
            runs.append({
                "run": iteration + 1,
                "elapsed_seconds": elapsed,
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
            }
        )

    return {
        "diagnostic_version": 1,
        "app_version": get_app_version(),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "fixed_query": "<redacted>",
        "target": {"root_type": "directory", "file_count": len(paths), "total_bytes": total_bytes, "types": dict(file_types), "size_buckets": dict(size_buckets)},
        "hardware": _hardware(),
        "scenarios": scenarios,
        "total_elapsed_seconds": round(time.perf_counter() - started, 6),
    }


def _markdown(report: dict[str, object]) -> str:
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a one-shot local StringFinder performance diagnostic")
    parser.add_argument("folder", type=Path, help="diagnostic target folder")
    parser.add_argument("--repeats", type=int, default=5, choices=range(1, 6), help="runs per scenario (default: 5)")
    parser.add_argument("--output", type=Path, default=Path("stringfinder_diagnostic.json"))
    args = parser.parse_args()
    if not args.folder.is_dir():
        parser.error(f"Folder does not exist: {args.folder}")
    report = run(args.folder, args.repeats)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    args.output.with_suffix(".md").write_text(_markdown(report), encoding="utf-8")
    print(f"Diagnostic report written: {args.output} and {args.output.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
