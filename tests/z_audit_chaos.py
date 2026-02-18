import os
import sys
import unittest
import time
import random
import threading
from PySide6.QtCore import QCoreApplication

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.worker import SearchWorker

# Dummy app for QObject signals
if not QCoreApplication.instance():
    app = QCoreApplication([])


class TestAuditChaos(unittest.TestCase):
    def test_chaos_rapid_start_stop(self):
        """CHAOS: Rapid Start/Stop cycles to trigger race conditions"""
        print("\n[CHAOS] 50 Rapid Start/Stop Cycles...")

        errors = []

        for i in range(50):
            worker = SearchWorker(
                {
                    "search_paths": ["."],  # Scan project dir
                    "search_string": "def",
                    "extensions": ["py"],
                    "cache_enabled": False,
                }
            )

            # Hook signals to check if they fire after stop
            def on_error(msg):
                errors.append(f"Error in cycle {i}: {msg}")

            worker.signals.error.connect(on_error)

            # Start
            threading.Thread(target=worker.run).start()

            # Random delay 0.001 ~ 0.05s (Critical timing for race condition)
            time.sleep(random.uniform(0.001, 0.05))

            # Stop
            worker.is_running = False

            if i % 10 == 0:
                print(f"Cycle {i}...", end=" ", flush=True)

        print("Done.")

        if errors:
            self.fail(f"Chaos test failed with {len(errors)} errors: {errors[0]}...")


if __name__ == "__main__":
    unittest.main()
