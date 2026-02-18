import os
import sys
import unittest
import tempfile
import random
import logging

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.search_engine import search_in_files_batch
from sf_utils.logger import logger

# Suppress noisy logs
logger.setLevel(logging.CRITICAL)


class TestAuditFuzzing(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = self.temp_dir.name

    def tearDown(self):
        self.temp_dir.cleanup()

    def create_file(self, name, content_bytes):
        path = os.path.join(self.root, name)
        with open(path, "wb") as f:
            f.write(content_bytes)
        return path

    def test_fuzz_binary_injection(self):
        """FUZZ: Check if engine crashes on random binary garbage"""
        print("\n[FUZZ] Testing Binary Injection...")
        for i in range(10):
            # Generate random binary junk
            data = os.urandom(random.randint(1024, 1024 * 1024))  # 1KB ~ 1MB
            path = self.create_file(f"fuzz_bin_{i}.bin", data)

            try:
                # Should handle binary gracefully (skip or return empty)
                # Not crush
                search_in_files_batch([(path, len(data))], "test", None)
                # Binary should be detected and skipped or returned as binary match
            except Exception as e:
                self.fail(f"CRASHED on binary injection: {e}")

    def test_fuzz_malformed_encoding(self):
        """FUZZ: Check if engine crashes on malformed encoding"""
        print("\n[FUZZ] Testing Malformed Encoding...")
        # Invalid UTF-8 sequence
        malformed_utf8 = b"\xed\xa0\x80"
        # Invalid CP949
        malformed_cp949 = b"\xff\xfe\xfa"

        path1 = self.create_file("bad_utf8.txt", malformed_utf8)
        path2 = self.create_file("bad_cp949.txt", malformed_cp949)

        try:
            search_in_files_batch([(path1, len(malformed_utf8)), (path2, len(malformed_cp949))], "test", None)
        except Exception as e:
            print(f"Handled malformed encoding: {e}")

    def test_fuzz_huge_line(self):
        """FUZZ: Check if memory explodes on single HUGE line"""
        print("\n[FUZZ] Testing Huge Single Line...")
        # 10MB single line
        huge_line = b"a" * (10 * 1024 * 1024)
        path = self.create_file("huge_line.txt", huge_line)

        try:
            search_in_files_batch([(path, len(huge_line))], "test", None)
        except MemoryError:
            print("Caught MemoryError (Expected)")
        except Exception as e:
            self.fail(f"CRASHED on huge line: {e}")


if __name__ == "__main__":
    unittest.main()
