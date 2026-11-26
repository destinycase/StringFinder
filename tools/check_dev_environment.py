"""Validate the minimum local toolchain required to build StringFinder."""

from __future__ import annotations

import importlib.util
import argparse
import os
from pathlib import Path
import shutil
import sys
import tempfile


def current_engine_source_hash(root: Path) -> str:
    """지금 Rust 소스의 지문(tools/engine_source_hash.py). 스크립트로 실행해도 불러올 수 있게 경로로 읽습니다."""
    spec = importlib.util.spec_from_file_location("engine_source_hash", root / "tools" / "engine_source_hash.py")
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module.engine_source_hash(root / "src" / "rust_engine")


def _release_logs_in(folder: str) -> None:
    """임시 폴더를 지울 수 있게, 그 안에 파일을 연 로그 처리기를 닫습니다(Windows는 열린 파일을 못 지움)."""
    import logging

    loggers = [logging.getLogger()] + [logging.getLogger(n) for n in list(logging.root.manager.loggerDict)]
    prefix = os.path.normcase(os.path.abspath(folder))
    for log in loggers:
        for handler in list(getattr(log, "handlers", [])):
            name = getattr(handler, "baseFilename", "")
            if name and os.path.normcase(os.path.abspath(name)).startswith(prefix):
                handler.close()
                log.removeHandler(handler)


def check_native_engine() -> bool:
    """Fail CI rather than silently skipping the native search contracts.

    엔진을 불러오면 로거가 데이터 폴더에 로그 파일을 만듭니다. 작업자·검수자·CI가 돌리는 도구가 실제
    %APPDATA%\\StringFinder에 흔적을 남기지 않게, 제품 모듈을 불러오기 전에 데이터 폴더를 임시 폴더로 돌리고
    끝나면 지웁니다(7.7.8 정리, 7.6.5 빌드 기동 검사와 같은 방식).
    """
    root = Path(__file__).resolve().parents[1]
    saved = {key: os.environ.get(key) for key in ("APPDATA", "LOCALAPPDATA")}
    data_dir = tempfile.mkdtemp(prefix="sf_envcheck_")
    os.environ["APPDATA"] = data_dir
    os.environ["LOCALAPPDATA"] = data_dir
    try:
        return _check_native_engine(root)
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        _release_logs_in(data_dir)
        shutil.rmtree(data_dir, ignore_errors=True)


def _check_native_engine(root: Path) -> bool:
    sys.path.insert(0, str(root / "src"))
    try:
        from core import search_engine

        engine = search_engine.sf_engine
        # 엔진 버전은 앱 버전과 따로 매깁니다(7.7.6). 엔진이 지금 Rust 소스로 빌드됐는지는 소스 지문으로 봅니다.
        return bool(
            search_engine.HAS_RUST_ENGINE
            and engine is not None
            and engine.API_VERSION >= search_engine.REQUIRED_API_VERSION
            and getattr(engine, "SOURCE_HASH", None) == current_engine_source_hash(root)
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
        checks["native engine source-hash/path/API"] = check_native_engine()
    for name, ok in checks.items():
        print(f"[{'OK' if ok else 'MISSING'}] {name}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
