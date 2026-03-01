import hashlib
from pathlib import Path

def get_file_hash(path: str) -> str:
    sha256_hash = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()

def test_engine_binary_consistency():
    """
    Ensure sf_engine binary is considered a Single Source of Truth (SSOT).
    There should only be one copy of sf_engine.pyd (or .so) in the src/ directory.
    If multiple exist, they MUST have the exact same hash (though zero duplication is strongly preferred).
    """
    src_dir = Path("src")
    if not src_dir.exists():
        return  # Nothing to test if src/ doesn't exist

    engine_files = list(src_dir.rglob("sf_engine.pyd")) + list(src_dir.rglob("sf_engine.so"))
    
    # Exclude files in target/ directory, which are cargo build artifacts
    engine_files = [f for f in engine_files if "target" not in f.parts]
    
    if not engine_files:
        return  # No engine built, that's fine for some CI environments

    # Collect hashes
    file_hashes = {}
    for f in engine_files:
        path_str = str(f)
        file_hashes[path_str] = get_file_hash(path_str)

    unique_hashes = set(file_hashes.values())
    
    # Asserting SSOT
    assert len(unique_hashes) <= 1, (
        f"Multiple sf_engine binaries found with differing hashes! "
        f"This violates the SSOT constraint.\nDetails: {file_hashes}"
    )
    
    # Enforce strict SSOT: exactly one engine binary outside target/
    assert len(engine_files) == 1, (
        "Multiple sf_engine binaries found. This violates strict SSOT. "
        f"Found: {list(file_hashes.keys())}"
    )
