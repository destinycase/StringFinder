import os
import shutil
import threading
import time

import sf_engine


def test_progress_callback(count):
    print(f"[Progress] Searched: {count} files")


def test_search():
    print("\n--- Testing search_dir ---")
    test_dir = "verify_test_dir"
    os.makedirs(test_dir, exist_ok=True)
    for i in range(100):
        with open(f"{test_dir}/file_{i}.txt", "w", encoding="utf-8") as f:
            f.write(f"This is a test file number {i}\nTarget keyword here.")
    with open(f"{test_dir}/test.json", "w", encoding="utf-8") as f:
        f.write('{"key": "value", "nested": {"target": "Target keyword"}}')
    try:
        matches, skipped = sf_engine.search_dir(
            [test_dir],
            "Target keyword",
            extensions=None,
            special_mode="json",
            filename_filter=None,
            exclude_hidden=False,
            stop_event=None,
            progress_callback=test_progress_callback,
        )
        print(f"Found {len(matches)} matches, {len(skipped)} skipped.")
        for path, hit_list in matches:
            print(f"Match in {path}: {len(hit_list)} hits")
    except Exception as e:
        print(f"Search failed: {e}")
    finally:
        shutil.rmtree(test_dir)


def test_cancellation():
    print("\n--- Testing cancellation ---")
    stop_event = threading.Event()
    search_path = os.environ.get("WINDIR", "C:/Windows")

    def run_search():
        print(f"Starting long search in {search_path}...")
        try:
            sf_engine.search_dir(
                [search_path],
                "non_existent_unique_string_12345",
                stop_event=stop_event,
                progress_callback=lambda c: None,  # 진행 콜백 출력 비활성화
            )
            print("Search thread finished naturally (or stopped).")
        except Exception as e:
            print(f"Search thread finished with exception: {e}")

    t = threading.Thread(target=run_search)
    t.start()
    time.sleep(1.5)
    print("Setting stop event now...")
    start_stop = time.time()
    stop_event.set()
    t.join(timeout=5)
    end_stop = time.time()
    if t.is_alive():
        print("FAILED: Search thread is still alive after 5 seconds!")
    else:
        print(f"SUCCESS: Search thread stopped in {end_stop - start_stop:.2f} seconds.")


if __name__ == "__main__":
    test_search()
    test_cancellation()
