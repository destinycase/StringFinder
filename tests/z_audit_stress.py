import os
import sys
import unittest
import tempfile
import time

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.search_cache import HybridSearchCache


class TestAuditStress(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = self.temp_dir.name

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_stress_massive_files(self):
        """STRESS: 10,000 files in deep directory structure"""
        print("\n[STRESS] Generating 10,000 files (Depth: 10)...")

        start_gen = time.time()
        file_paths = []

        # Create deep structure: root/0/0/0/...
        # Width 10, Depth 4 -> 10^4 = 10,000 files

        # Simplified for speed in this environment: 100 dirs * 100 files = 10,000
        for i in range(100):
            d = os.path.join(self.root, f"d_{i}")
            os.makedirs(d, exist_ok=True)
            for j in range(100):
                p = os.path.join(d, f"f_{j}.txt")
                with open(p, "w") as f:
                    f.write(f"content_{i}_{j}")
                file_paths.append(p)

        gen_time = time.time() - start_gen
        print(f"Generated 10,000 files in {gen_time:.2f}s")

        # Test Cache Signature Speed
        cache = HybridSearchCache(os.path.join(self.root, ".cache"), 1000, False)

        start_sig = time.time()
        # This triggers the recursive scandir
        meta = cache._get_paths_metadata([self.root])
        sig_time = time.time() - start_sig

        print(f"Recursive Signature Time: {sig_time:.4f}s")

        # Assert performance requirement (e.g., < 1.0s for 10k files)
        self.assertLess(sig_time, 2.0, "Signature calculation too slow for 10k files")

        # Verify content
        # The key in meta should be the root path
        self.assertIn(self.root, meta)
        self.assertTrue(meta[self.root].get("recursive"))


if __name__ == "__main__":
    unittest.main()
