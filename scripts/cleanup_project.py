import os
import shutil
import glob
import stat

SSOT_ENGINE_PYD = os.path.normcase(os.path.abspath(os.path.join("src", "rust_engine", "sf_engine.pyd")))
SSOT_ENGINE_SO = os.path.normcase(os.path.abspath(os.path.join("src", "rust_engine", "sf_engine.so")))


def _force_remove_readonly(func, path, _):
    """_force_remove_readonly 함수."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _is_target_artifact(path: str) -> bool:
    parts = {p.lower() for p in os.path.normpath(path).split(os.sep)}
    return "target" in parts


def _clean_non_ssot_engine_binaries():
    keep_paths = {SSOT_ENGINE_PYD, SSOT_ENGINE_SO}
    for root, _, files in os.walk("."):
        for file in files:
            if not (file.startswith("sf_engine.pyd") or file.startswith("sf_engine.so")):
                continue
            path = os.path.join(root, file)
            if _is_target_artifact(path):
                continue
            norm_path = os.path.normcase(os.path.abspath(path))
            if file in {"sf_engine.pyd", "sf_engine.so"} and norm_path in keep_paths:
                continue
            try:
                os.remove(path)
                print(f"Removed non-SSOT engine binary: {path}")
            except Exception as e:
                print(f"Error removing non-SSOT binary {path}: {e}")


def cleanup():
    files_to_remove = [
        "crash_dump.txt",
        "debug_log*.txt",
        "*.log",
        "StringFinder.spec",
        "sf_engine.pyd",
        "sf_engine.cp*.pyd",
        "src/*.pyd.old_*",
        ".coverage",
        "coverage.xml",
    ]

    dirs_to_remove = [
        "build",
        "dist",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".benchmarks",
        "tests/temp_test_dir",
        "tests/persist_v4",
        "temp_appdata_check",
        "string_finder.egg-info",
        "*.egg-info",
        "src/*.egg-info",
        "src/rust_engine/target",
        "htmlcov",
    ]

    recursive_dirs = ["__pycache__"]

    print("--- Starting Cleanup ---")

    files_to_remove.extend(
        [
            ".tmp_*",
            "*.tmp",
        ]
    )

    for pattern in files_to_remove:
        for filepath in glob.glob(pattern):
            try:
                os.remove(filepath)
                print(f"Removed file: {filepath}")
            except Exception as e:
                print(f"Error removing {filepath}: {e}")

    _clean_non_ssot_engine_binaries()

    for dir_pattern in dirs_to_remove:
        matched_dirs = glob.glob(dir_pattern)
        if not matched_dirs and os.path.exists(dir_pattern):
            matched_dirs = [dir_pattern]
        for dirname in matched_dirs:
            if not os.path.isdir(dirname):
                continue
            try:
                shutil.rmtree(dirname, onerror=_force_remove_readonly)
                print(f"Removed directory: {dirname}")
            except Exception as e:
                print(f"Error removing {dirname}: {e}")

    for root, dirs, files in os.walk("."):
        for r_dir in recursive_dirs:
            if r_dir in dirs:
                path = os.path.join(root, r_dir)
                try:
                    shutil.rmtree(path, onerror=_force_remove_readonly)
                    print(f"Removed recursive directory: {path}")
                except Exception as e:
                    print(f"Error removing {path}: {e}")

    print("--- Cleanup Complete ---")


if __name__ == "__main__":
    cleanup()
