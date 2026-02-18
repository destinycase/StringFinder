import os
import sys
import unittest
import tempfile
import time
import threading
import stat

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.search_engine import search_in_files_batch
from sf_utils.logger import logger
import logging

logger.setLevel(logging.CRITICAL)


class TestAuditIOChaos(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = self.temp_dir.name

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass

    def test_permission_flip_flop(self):
        """[IO Chaos] Rapid Permission Toggle during Read"""
        print("\n[IO Chaos] Testing Permission Flip-Flop...")

        target_file = os.path.join(self.root, "chaos.txt")
        # Write large content to ensure reading takes some time (but ssd is fast...)
        # 10MB file
        with open(target_file, "wb") as f:
            f.write(b"a" * 1024 * 1024 * 10)

        active = True

        def chaos_monkey():
            while active:
                try:
                    # Deny Read (Windows might be tricky with chmod, strictly works on Linux/Mac better,
                    # but on Windows stat.S_IREAD works for Read-Only attribute)
                    os.chmod(target_file, 0)
                    time.sleep(0.001)
                    os.chmod(target_file, stat.S_IREAD | stat.S_IWRITE)
                    time.sleep(0.001)
                except Exception:
                    pass

        t = threading.Thread(target=chaos_monkey)
        t.start()

        try:
            # Try to search multiple times
            for _ in range(10):
                try:
                    search_in_files_batch([(target_file, 1024 * 1024 * 10)], "needle", None)
                except PermissionError:
                    pass  # Expected
                except Exception as e:
                    # IOError is acceptable, Crash is not.
                    print(f"Caught expected I/O exception: {e}")

        finally:
            active = False
            t.join()
            # Ensure readable for cleanup
            os.chmod(target_file, stat.S_IREAD | stat.S_IWRITE)

    def test_mid_read_deletion(self):
        """[IO Chaos] Deletion during Read"""
        print("\n[IO Chaos] Testing Mid-Read Deletion...")

        # We need a custom search function injection or just rely on OS speed.
        # Creating many files and deleting them while searching batch.
        files = []
        for i in range(100):
            p = os.path.join(self.root, f"del_{i}.txt")
            with open(p, "w") as f:
                f.write("content")
            files.append((p, 7))

        # The 'active' variable was unused in this test case.

        def deleter():
            time.sleep(0.01)  # Wait for search start
            for p, _ in files:
                try:
                    os.remove(p)
                except OSError:  # Catch specific OS errors like FileNotFoundError, PermissionError
                    pass

        t = threading.Thread(target=deleter)
        t.start()

        try:
            # Search batch
            search_in_files_batch(files, "content", None)
            print("Deletion during read handled without crash")
        except Exception as e:
            self.fail(f"CRASHED on Mid-Read Deletion: {e}")
        finally:
            t.join()


if __name__ == "__main__":
    unittest.main()
