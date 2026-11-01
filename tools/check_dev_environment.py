"""Validate the minimum local toolchain required to build StringFinder."""

from __future__ import annotations

import importlib.util
import argparse
from pathlib import Path
import shutil
import sys


def check_native_engine() -> bool:
    """Fail CI rather than silently skipping the native search contracts."""
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "src"))
    try:
        from core import search_engine
        from sf_utils._version import VERSION

        engine = search_engine.sf_engine
        return bool(
            search_engine.HAS_RUST_ENGINE
            and engine is not None
            and engine.API_VERSION >= search_engine.REQUIRED_API_VERSION
            and engine.ENGINE_VERSION == VERSION
            and Path(engine.__file__).resolve() == root / "src/rust_engine/sf_engine.pyd"
        )
    except (ImportError, AttributeError, OSError, TypeError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-engine", action="store_true", help="Require the canonical freshly built native engine")
    args = parser.parse_args()
    checks = {
        "python": sys.version_info >= (3, 12),
        "cargo": shutil.which("cargo") is not None,
        "rustc": shutil.which("rustc") is not None,
        "PySide6": importlib.util.find_spec("PySide6") is not None,
        "PyInstaller": importlib.util.find_spec("PyInstaller") is not None,
    }
    if args.require_engine:
        checks["native engine version/path/API"] = check_native_engine()
    for name, ok in checks.items():
        print(f"[{'OK' if ok else 'MISSING'}] {name}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
