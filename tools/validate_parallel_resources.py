"""Bounded, isolated native-RSS and cancellation checks (not an OOM guarantee).

Run from the repository: python tools/validate_parallel_resources.py
Only generated fixtures are searched. Child processes have a 90-second timeout.
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import psutil

ROOT = Path(__file__).resolve().parents[1]
MIB = 1024 * 1024


def make_data(root):
    from openpyxl import Workbook

    for kind in ("json", "xml", "xlsx"):
        folder = root / kind
        folder.mkdir()
        first = folder / f"0.{kind}"
        if kind == "xlsx":
            book = Workbook(write_only=True)
            sheet = book.create_sheet("QA")
            for index in range(40000):
                sheet.append([f"row-{index}-" + "x" * 240] * 4)
            sheet.append(["needle"])
            book.save(first)
            book.close()
        else:
            unit = b'"' + b"x" * 1024 + b'",' if kind == "json" else b"<v>" + b"x" * 1024 + b"</v>"
            with first.open("wb") as stream:
                stream.write(b"[" if kind == "json" else b"<root>")
                for _ in range(32 * MIB // len(unit)):
                    stream.write(unit)
                stream.write(b'"needle"]' if kind == "json" else b"<v>needle</v></root>")
        for index in range(1, 8):
            shutil.copyfile(first, folder / f"{index}.{kind}")


def child(root, kind, budget, cancel):
    sys.path.insert(0, str(ROOT / "src"))
    from rust_engine import sf_engine
    from sf_utils.constants import Constants

    mode = {"json": Constants.RUST_MODE_JSON, "xml": Constants.RUST_MODE_XML,
            "xlsx": Constants.RUST_MODE_EXCEL}[kind]
    event = threading.Event()
    samples = []
    sampling_done = threading.Event()

    def sample():
        process = psutil.Process()
        while not sampling_done.is_set():
            samples.append(process.memory_info().rss)
            sampling_done.wait(0.005)

    sampler = threading.Thread(target=sample)
    sampler.start()
    timer = threading.Timer(0.05, event.set) if cancel else None
    if timer:
        timer.start()
    start = time.perf_counter()
    try:
        options = sf_engine.SearchOptions(mode_bits=mode, stop_event=event,
                                         structured_memory_budget=budget * MIB)
        results, skipped = sf_engine.search_dir([str(root / kind)], "needle", options=options)
        elapsed = time.perf_counter() - start
    finally:
        if timer:
            timer.cancel()
            timer.join()
        sampling_done.set()
        sampler.join()
    files = {Path(path).name for path, _ in results}
    assert len(files) == len(results), "Duplicate file results"
    assert all(len(matches) == 1 and matches[0].kind == "match" for _, matches in results)
    if cancel:
        assert not skipped, skipped
        # A follow-up call must not inherit cancellation or a leaked reservation.
        options = sf_engine.SearchOptions(mode_bits=mode, structured_memory_budget=budget * MIB)
        again, skipped_again = sf_engine.search_dir([str(root / kind)], "needle", options=options)
        assert len(again) == 8 and not skipped_again
    elif budget == 1 and kind != "xlsx":
        assert results == [] and len(skipped) == 8
        assert all(reason.startswith("ERR_RESOURCE_BUDGET|") for _, reason in skipped)
    else:
        assert files == {f"{i}.{kind}" for i in range(8)} and not skipped
    print(json.dumps(dict(kind=kind, budget_mib=budget, cancel=cancel, files=len(results),
                          skipped=len(skipped), seconds=round(elapsed, 4),
                          sampled_peak_rss_mib=round(max(samples) / MIB, 1))), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", nargs=4, metavar=("ROOT", "KIND", "BUDGET", "CANCEL"))
    args = parser.parse_args()
    if args.child:
        root, kind, budget, cancel = args.child
        child(Path(root), kind, int(budget), cancel == "1")
        return
    if psutil.virtual_memory().available < 4 * 1024 * MIB:
        raise RuntimeError("At least 4 GiB available memory required for this bounded QA run")
    with tempfile.TemporaryDirectory(prefix="sf-resource-qa-", dir=ROOT) as directory:
        root = Path(directory)
        print("Generating 8 fixtures per format...", flush=True)
        make_data(root)
        print(json.dumps({k: (root / k / f"0.{k}").stat().st_size for k in ("json", "xml", "xlsx")}), flush=True)
        for kind in ("json", "xml", "xlsx"):
            for budget, cancel in ((1024, False), (160, False), (160, True), (1, False)):
                command = [sys.executable, str(Path(__file__).resolve()), "--child", str(root), kind,
                           str(budget), str(int(cancel))]
                # The watchdog only terminates the child created for this case.
                with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      text=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"}) as process:
                    started = time.monotonic()
                    while process.poll() is None:
                        try:
                            rss = psutil.Process(process.pid).memory_info().rss
                        except psutil.NoSuchProcess:
                            break
                        if (rss > 2 * 1024 * MIB or psutil.virtual_memory().available < 2 * 1024 * MIB
                                or time.monotonic() - started > 90):
                            process.kill()
                            process.communicate()
                            raise RuntimeError(f"QA watchdog stopped {kind}/{budget}/{cancel}")
                        time.sleep(0.02)
                    stdout, stderr = process.communicate()
                    print(stdout, end="", flush=True)
                    if process.returncode:
                        raise RuntimeError(stderr)


if __name__ == "__main__":
    main()
