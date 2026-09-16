"""Packaging guards; deliberately independent of the application UI."""

import os
from pathlib import Path
import subprocess
import sys


def isolated_packaging_path():
    """Do not let unrelated tools on PATH supply DLLs to PyInstaller."""
    windows = Path(os.environ["SystemRoot"])
    return os.pathsep.join(str(p) for p in (
        windows / "System32", windows, Path(sys.executable).parent,
        Path(sys.prefix) / "DLLs", Path(sys.prefix) / "Scripts",
    ))


def verify_qt_icu(executable):
    """Reject a bundled ICU whose exports do not satisfy QtCore's imports."""
    import ntpath
    import pefile
    from PyInstaller.archive.readers import CArchiveReader

    archive = CArchiveReader(str(executable))
    core = next(n for n in archive.toc if ntpath.basename(n).lower() == "qt6core.dll")
    qt = pefile.PE(data=archive.extract(core))
    for dependency in qt.DIRECTORY_ENTRY_IMPORT:
        name = dependency.dll.decode().lower()
        if not name.startswith("icu"):
            continue
        for member in archive.toc:
            if ntpath.basename(member).lower() != name:
                continue
            dll = pefile.PE(data=archive.extract(member))
            exports = {s.name for s in dll.DIRECTORY_ENTRY_EXPORT.symbols}
            missing = [s.name for s in dependency.imports if s.name and s.name not in exports]
            if missing:
                raise RuntimeError(f"Incompatible bundled {member}: missing {missing[:3]}")


def smoke_test_executable(executable):
    """Check frozen Qt/theme/engine initialization without loading user sessions."""
    import psutil

    env = os.environ.copy()
    env["PATH"] = isolated_packaging_path()
    for key in tuple(env):
        if key.startswith(("QT_", "QML", "PYTHON")):
            env.pop(key)
    process = subprocess.Popen(
        [str(Path(executable).resolve()), "--smoke-test"], env=env,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        code = process.wait(timeout=45)
    except subprocess.TimeoutExpired:
        # Only terminate the test process tree that this function created.
        try:
            for child in psutil.Process(process.pid).children(recursive=True):
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
        finally:
            process.kill()
            process.wait()
        raise RuntimeError("Frozen application smoke test timed out") from None
    if code != 0:
        raise RuntimeError(f"Frozen application smoke test failed: exit code {code}")
