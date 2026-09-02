"""Runtime language selection for StringFinder string resources."""

from __future__ import annotations

import copy
import json
import os
import threading
from typing import Any

from sf_utils.app_strings import AppStrings
from sf_utils.english_strings import ENGLISH_STRINGS


DEFAULT_LANGUAGE = "ko"
SUPPORTED_LANGUAGES = ("ko", "en")
LANGUAGE_LABELS = {"ko": "한국어", "en": "English"}
LANGUAGE_CONFIG_KEY = "language"

_language_lock = threading.RLock()
_current_language = DEFAULT_LANGUAGE
_korean_strings = {
    name: copy.deepcopy(value)
    for name, value in vars(AppStrings).items()
    if name.isupper() and isinstance(value, (str, list, tuple))
}


def normalize_language(language: Any) -> str:
    """Return a supported two-letter language code."""
    normalized = str(language or "").strip().lower().replace("_", "-")
    if normalized.startswith("en"):
        return "en"
    if normalized.startswith("ko"):
        return "ko"
    return DEFAULT_LANGUAGE


def set_language(language: Any) -> str:
    """Apply translated resources to ``AppStrings`` and return the language code."""
    global _current_language
    normalized = normalize_language(language)
    with _language_lock:
        for name, korean_value in _korean_strings.items():
            value = ENGLISH_STRINGS.get(name, korean_value) if normalized == "en" else korean_value
            setattr(AppStrings, name, copy.deepcopy(value))
        _current_language = normalized
    return normalized


def get_language() -> str:
    with _language_lock:
        return _current_language


def get_korean_strings() -> dict[str, Any]:
    """Return a detached resource snapshot for translation contract tests."""
    with _language_lock:
        return copy.deepcopy(_korean_strings)


def load_saved_language() -> str:
    """Read only the language field before the regular configuration layer is imported."""
    app_data = os.getenv("APPDATA")
    if app_data:
        config_dir = os.path.join(app_data, "StringFinder")
    else:
        config_dir = os.path.join(os.path.expanduser("~"), ".stringfinder")
    config_path = os.path.join(config_dir, "config.json")

    try:
        with open(config_path, "r", encoding="utf-8") as config_file:
            config = json.load(config_file)
        if isinstance(config, dict):
            return normalize_language(config.get(LANGUAGE_CONFIG_KEY))
    except (OSError, ValueError, TypeError):
        pass
    return DEFAULT_LANGUAGE


def apply_saved_language() -> str:
    """Load and apply the persisted language before importing the application UI."""
    return set_language(load_saved_language())
