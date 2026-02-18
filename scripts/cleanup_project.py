import os
import shutil
import glob


def cleanup():
    # Files to remove (Glob patterns supported)
    files_to_remove = [
        "crash_dump.txt",
        "debug_log*.txt",
        "*.log",
        "StringFinder.spec",
        "sf_engine.pyd",  # safe to remove from root if it exists in src
        "sf_engine.cp*.pyd",
    ]

    # Directories to remove
    dirs_to_remove = [
        "build",
        "dist",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".benchmarks",
        "tests/temp_test_dir",
        "tests/persist_v4",
    ]

    # Recursive directory patterns
    recursive_dirs = ["__pycache__"]

    print("--- Starting Cleanup ---")

    # Validating sf_engine.pyd existence in src before removing from root
    if os.path.exists("sf_engine.pyd") and not os.path.exists("src/sf_engine.pyd"):
        print("WARNING: sf_engine.pyd exists in root but NOT in src. Skipping root deletion to be safe.")
        if "sf_engine.pyd" in files_to_remove:
            files_to_remove.remove("sf_engine.pyd")

    # Remove Files
    for pattern in files_to_remove:
        for filepath in glob.glob(pattern):
            try:
                os.remove(filepath)
                print(f"Removed file: {filepath}")
            except Exception as e:
                print(f"Error removing {filepath}: {e}")

    # Remove Directories
    for dirname in dirs_to_remove:
        if os.path.exists(dirname):
            try:
                shutil.rmtree(dirname)
                print(f"Removed directory: {dirname}")
            except Exception as e:
                print(f"Error removing {dirname}: {e}")

    # Recursive Directories
    for root, dirs, files in os.walk("."):
        for r_dir in recursive_dirs:
            if r_dir in dirs:
                path = os.path.join(root, r_dir)
                try:
                    shutil.rmtree(path)
                    print(f"Removed recursive directory: {path}")
                except Exception as e:
                    print(f"Error removing {path}: {e}")

    print("--- Cleanup Complete ---")


if __name__ == "__main__":
    cleanup()
