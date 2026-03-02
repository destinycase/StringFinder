"""
Benchmark history skip-reason sanitization tests.
"""

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import scripts.benchmark_performance as benchmark_performance  # noqa: E402


def test_sanitize_skip_reason_mojibake_with_size() -> None:
    raw = "[??混獰] ???敧 ??胳浘 ?ル崌?? 1073741841 bytes"
    sanitized = benchmark_performance._sanitize_skip_reason_text(raw)
    assert sanitized == "[Error] File too large: 1073741841 bytes"


def test_sanitize_skip_reason_readable_text_passthrough() -> None:
    raw = "[Error] File too large: 512 bytes"
    sanitized = benchmark_performance._sanitize_skip_reason_text(raw)
    assert sanitized == raw


def test_summarize_skip_reasons_uses_sanitized_key() -> None:
    summary = benchmark_performance._summarize_skip_reasons(
        {"[??混獰] ???敧 ??胳浘 ?ル崌?? 1073741841 bytes": 1}
    )
    assert summary == "[Error] File too large: 1073741841 bytes (1)"
