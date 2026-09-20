"""Centralized configuration defaults and default-version metadata.

Keep user-facing setting defaults here so a default change is explicit and can
be migrated without overwriting values that users intentionally chose.
"""

CONFIG_SCHEMA_VERSION = 4

DEFAULTS = {
    "max_total_matches": 500_000,
    "max_per_file_matches": 10_000,
    "max_json_dom_size": 1024,
    "max_small_file_size": 10,
    "json_mmap_threshold": 5,
    "timeout_worker_hang": 600,
    "max_check_cells": 500_000,
    "max_json_depth": 20_000,
}

# Increment only when the shipped default for that setting changes.
DEFAULT_VERSIONS = {
    "max_total_matches": 1,
    "max_per_file_matches": 2,
    "max_json_dom_size": 3,
    "max_small_file_size": 1,
    "json_mmap_threshold": 1,
    "timeout_worker_hang": 1,
    "max_check_cells": 1,
    "max_json_depth": 1,
}
