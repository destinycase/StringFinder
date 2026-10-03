"""Packaging guards; deliberately independent of the application UI."""

import os
from pathlib import Path
import subprocess
import sys


def is_path_within(path, root):
    """Compare resolved directories, never string prefixes or lexical links."""
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except (OSError, ValueError, RuntimeError):
        return False


def checked_cleanup_path(path, root):
    """Refuse root deletion, links and paths outside the project."""
    candidate = Path(path)
    if (candidate.is_symlink() or candidate.is_junction()
            or not is_path_within(candidate, root)
            or candidate.resolve() == Path(root).resolve()):
        raise ValueError(f"Unsafe cleanup target: {candidate}")
    return str(candidate.resolve())


def walk_project_tree(directory, project_root, excluded=()):
    """Walk only real project directories; prune links before descending."""
    start = Path(directory)
    if start.is_symlink() or start.is_junction() or not is_path_within(start, project_root):
        raise ValueError(f"Unsafe project tree: {start}")
    excluded_paths = {Path(path).resolve() for path in excluded}
    for folder, directories, files in os.walk(start, topdown=True, followlinks=False):
        directories[:] = [name for name in directories
                          if not (Path(folder, name).is_symlink()
                                  or Path(folder, name).is_junction())
                          and is_path_within(Path(folder, name), project_root)
                          and Path(folder, name).resolve() not in excluded_paths]
        yield folder, directories, files


def verify_project_payload(executable, project_root):
    """Verify packaged application code and its one canonical Rust binary."""
    import marshal
    import types
    from PyInstaller.archive.readers import CArchiveReader

    root = Path(project_root).resolve()
    archive = CArchiveReader(str(executable))
    pyz = archive.open_embedded_archive(next(name for name, entry in archive.toc.items() if entry[-1] == "z"))

    def signature(code):
        return (code.co_code, tuple(signature(value) if isinstance(value, types.CodeType) else value
                                   for value in code.co_consts), code.co_names, code.co_varnames,
                code.co_freevars, code.co_cellvars, code.co_flags, code.co_argcount,
                code.co_posonlyargcount, code.co_kwonlyargcount, code.co_exceptiontable)

    def check_code(code, source):
        if not source.is_file() or not is_path_within(source, root):
            raise RuntimeError(f"Non-project application source: {source}")
        expected = compile(source.read_text(encoding="utf-8-sig"), code.co_filename, "exec")
        if signature(code) != signature(expected):
            raise RuntimeError(f"Packaged application differs from project source: {source}")

    for name in pyz.toc:
        if name.startswith(("core.", "ui.", "sf_utils.", "tools.")) or name in {"core", "ui", "sf_utils"}:
            source = root / ("" if name.startswith("tools.") else "src") / Path(*name.split("."))
            source = source.with_suffix(".py") if source.with_suffix(".py").is_file() else source / "__init__.py"
            check_code(pyz.extract(name), source)
    check_code(marshal.loads(archive.extract("sf_main")), root / "src" / "sf_main.py")
    engine_members = [name for name in archive.toc if Path(name.replace("\\", "/")).name == "sf_engine.pyd"]
    if len(engine_members) != 1 or engine_members[0].replace("\\", "/") != "rust_engine/sf_engine.pyd":
        raise RuntimeError(f"Unexpected packaged Rust engines: {engine_members}")
    engine = root / "src" / "rust_engine" / "sf_engine.pyd"
    if not is_path_within(engine, root) or archive.extract(engine_members[0]) != engine.read_bytes():
        raise RuntimeError("Packaged Rust engine differs from the project binary")


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
