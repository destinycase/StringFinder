import os
import sys
import unittest
import logging

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.search_engine import search_in_files_batch
from sf_utils.logger import logger

logger.setLevel(logging.CRITICAL)


class TestAuditRustBridge(unittest.TestCase):
    def test_huge_pattern(self):
        """[Rust Bridge] Huge Pattern String (10MB)"""
        print("\n[Rust Bridge] Testing Huge Pattern...")

        # 1MB pattern
        huge_pat = "A" * 1024 * 1024

        try:
            # Should fail gracefully (MemoryError or RegexTooBig) or handle it
            # But NEVER Segfault/Panic
            # We pass a dummy file list
            search_in_files_batch([], huge_pat, None)
        except Exception:
            pass

    def test_toxic_inputs(self):
        """[Rust Bridge] Toxic Inputs (Surrogates, Nulls)"""
        print("\n[Rust Bridge] Testing Toxic Inputs...")

        toxic_patterns = [
            "\x00",  # Null char
            "\ud800",  # Lone surrogate check
        ]

        for pat in toxic_patterns:
            try:
                search_in_files_batch([], pat, None)
            except Exception as e:
                print(f"Handled toxic input '{repr(pat)}': {e}")


if __name__ == "__main__":
    unittest.main()
