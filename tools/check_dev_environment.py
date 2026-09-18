"""Validate the minimum local toolchain required to build StringFinder."""

from __future__ import annotations

import importlib.util
import shutil
import sys


def main() -> int:
    checks = {
        "python": sys.version_info >= (3, 12),
        "cargo": shutil.which("cargo") is not None,
        "rustc": shutil.which("rustc") is not None,
        "PySide6": importlib.util.find_spec("PySide6") is not None,
        "PyInstaller": importlib.util.find_spec("PyInstaller") is not None,
    }
    for name, ok in checks.items():
        print(f"[{'OK' if ok else 'MISSING'}] {name}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
