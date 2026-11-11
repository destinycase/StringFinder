"""Centralized configuration defaults and default-version metadata.

Keep user-facing setting defaults here so a default change is explicit and can
be migrated without overwriting values that users intentionally chose.
"""

from typing import TypedDict


class SettingDefaults(TypedDict):
    include_junctions: bool
    search_encoding: str
    allow_duplicate_json_keys: bool
    max_total_matches: int
    max_per_file_matches: int
    max_json_dom_size: int
    max_json_depth: int


CONFIG_SCHEMA_VERSION = 5

DEFAULTS: SettingDefaults = {
    "include_junctions": False,
    "search_encoding": "auto",
    "allow_duplicate_json_keys": False,
    "max_total_matches": 500_000,
    "max_per_file_matches": 10_000,
    "max_json_dom_size": 1024,
    "max_json_depth": 20_000,
}

# Increment only when the shipped default for that setting changes.
DEFAULT_VERSIONS = {
    "include_junctions": 1,
    "search_encoding": 1,
    "allow_duplicate_json_keys": 1,
    "max_total_matches": 1,
    "max_per_file_matches": 2,
    # The stored value is now a shared per-file limit; reset old JSON/XML-only
    # custom values once so they are not unexpectedly applied to every format.
    "max_json_dom_size": 4,
    "max_json_depth": 1,
}
