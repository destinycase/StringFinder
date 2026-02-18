import pytest
import os
import json
import time
from core.search_cache import HybridSearchCache


@pytest.fixture
def cache_dir(tmp_path):
    d = tmp_path / "cache"
    d.mkdir()
    return str(d)


def test_robust_load_from_disk(cache_dir):
    """Verify that a single corrupted entry does not prevent loading valid entries."""
    cache = HybridSearchCache(cache_dir, persist=True)

    # Create a dummy cache file with mixed content
    valid_file = os.path.join(cache_dir, "valid.txt")
    with open(valid_file, "w") as f:
        f.write("content")

    cache_data = {
        "version": "4",
        "result_cache": {},
        "file_cache": {
            # Valid Entry
            f"{valid_file}|query": {"mtime": 100.0, "size": 10, "ctime": 100.0, "results": [], "skipped": []},
            # Corrupted Entry (Missing 'results')
            f"{os.path.join(cache_dir, 'corrupt.txt')}|query": {"mtime": 100.0, "size": 10},
            # Invalid Type Entry (results is not list)
            f"{os.path.join(cache_dir, 'invalid.txt')}|query": {"mtime": 100.0, "size": 10, "results": "not-a-list"},
            # Non-existent file entry (Should be skipped by existence check)
            f"{os.path.join(cache_dir, 'ghost.txt')}|query": {"mtime": 100.0, "size": 10, "results": []},
        },
    }

    with open(cache.cache_v3_path, "w", encoding="utf-8") as f:
        json.dump(cache_data, f)

    # Load
    cache.load_from_disk()

    # Assert
    # Valid entry should be present
    assert len(cache.file_cache) == 1
    assert (valid_file, "query") in cache.file_cache

    # others should be skipped


def test_directory_signature_hashing(cache_dir):
    """Verify that directory signature uses robust hashing."""
    cache = HybridSearchCache(cache_dir)

    # Create directory structure
    d = os.path.join(cache_dir, "subdir")
    os.makedirs(d)

    f1 = os.path.join(d, "file1.txt")
    f2 = os.path.join(d, "file2.txt")

    with open(f1, "w") as f:
        f.write("a")
    with open(f2, "w") as f:
        f.write("b")

    # Get signature 1
    path_meta = cache._get_paths_metadata([d])
    sig1 = path_meta[d]["sig"]

    # Modify mtime of one file
    time.sleep(0.01)
    os.utime(f1, None)

    # Get signature 2
    path_meta2 = cache._get_paths_metadata([d])
    sig2 = path_meta2[d]["sig"]

    assert sig1 != sig2

    # Verify hash stability (same state = same hash)
    path_meta3 = cache._get_paths_metadata([d])
    sig3 = path_meta3[d]["sig"]
    assert sig2 == sig3

    # Verify content change (size)
    with open(f1, "w") as f:
        f.write("aa")
    path_meta4 = cache._get_paths_metadata([d])
    sig4 = path_meta4[d]["sig"]
    assert sig3 != sig4


def test_directory_signature_collision_avoidance(cache_dir):
    """
    Test that previously colliding states (swapped file sizes in strict sum mode)
    now produce different hashes due to sorted (name, meta) hashing.
    """
    cache = HybridSearchCache(cache_dir)
    d = os.path.join(cache_dir, "collision_test")
    os.makedirs(d)

    f1 = os.path.join(d, "A.txt")
    f2 = os.path.join(d, "B.txt")

    # State 1: A=10 bytes, B=20 bytes
    with open(f1, "wb") as f:
        f.write(b"x" * 10)
    with open(f2, "wb") as f:
        f.write(b"x" * 20)

    # Force same mtime for simplicity (or ignore mtime in this thought experiment, but code uses it)
    # We rely on size difference.
    # Sum(size) = 30.

    meta1 = cache._get_paths_metadata([d])[d]["sig"]

    # State 2: A=20 bytes, B=10 bytes
    # Sum(size) = 30. Old logic (Sum) might collide if mtime is also manipulated or ignored.
    # New logic: Hash("A...10") vs Hash("A...20") -> different.

    with open(f1, "wb") as f:
        f.write(b"x" * 20)
    with open(f2, "wb") as f:
        f.write(b"x" * 10)

    meta2 = cache._get_paths_metadata([d])[d]["sig"]

    assert meta1 != meta2
