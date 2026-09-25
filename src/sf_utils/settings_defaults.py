"""Centralized configuration defaults and default-version metadata.

Keep user-facing setting defaults here so a default change is explicit and can
be migrated without overwriting values that users intentionally chose.
"""

CONFIG_SCHEMA_VERSION = 5

DEFAULTS = {
    "max_total_matches": 500_000,
    "max_per_file_matches": 10_000,
    "max_json_dom_size": 1024,
    "max_small_file_size": 10,
    "json_mmap_threshold": 5,
    "timeout_worker_hang": 600,
    # Retained only as a legacy Rust API argument; existence checks no longer cap cells.
    "max_json_depth": 20_000,
    "excel_max_concurrency": 2,
    "excel_serialization_threshold_mb": 20,
}

# Increment only when the shipped default for that setting changes.
DEFAULT_VERSIONS = {
    "max_total_matches": 1,
    "max_per_file_matches": 2,
    # The stored value is now a shared per-file limit; reset old JSON/XML-only
    # custom values once so they are not unexpectedly applied to every format.
    "max_json_dom_size": 4,
    "max_small_file_size": 1,
    "json_mmap_threshold": 1,
    "timeout_worker_hang": 1,
    "max_json_depth": 1,
    "excel_max_concurrency": 1,
    "excel_serialization_threshold_mb": 1,
}
