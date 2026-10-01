"""Compare JSON duplicate-key policies and native binaries on fixed samples.

The default isolates Rust; --backend normal includes the Python wrapper and
no-match fallback. --backend precise measures the Python structured path.
Use --compare-engine to interleave a saved binary with the current one.
Output includes individual samples and content/location fingerprints as JSON.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
import logging
from pathlib import Path
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from sf_utils.constants import Constants  # noqa: E402


def create_cases(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    small = root / "small_objects.json"
    escaped = root / "escaped_keys.json"
    wide = root / "wide_object.json"
    if not small.exists():
        small.write_text(json.dumps({"items": [
            {"id": i, "value": "payload", "needle": "present"} for i in range(120_000)
        ]}), encoding="utf-8")
    if not escaped.exists():
        escaped.write_text('[' + ','.join(
            '{"id":' + str(i) + ',"val\\u0075e":"present"}' for i in range(20_000)
        ) + ']', encoding="utf-8")
    if not wide.exists():
        wide.write_text(json.dumps({f"key{i}": "needle" if i == 19_999 else "payload"
                                   for i in range(20_000)}), encoding="utf-8")
    return [
        ("small-repeated", small, "present", False),
        ("small-sparse", small, "119999", False),
        ("small-existence", small, "present", True),
        ("small-no-match", small, "__absent_keyword__", False),
        ("escaped-keys", escaped, "present", False),
        ("wide-object", wide, "needle", False),
    ]


def fingerprint(matches, backend="native"):
    values = [(m.line, m.content, m.offset, m.length) for m in matches] if backend == "native" else matches
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode("utf-8")).hexdigest()


def run_search(engine, case, allow, backend):
    _, path, query, existence = case
    if backend != "native":
        from core import search_engine
        search_engine.sf_engine = engine

        with search_engine.use_search_settings_snapshot({Constants.CONFIG_KEY_ALLOW_DUPLICATE_JSON_KEYS: allow}):
            return search_engine.search_in_json_special(str(path), query,
                existence_only=existence, use_complex_search=backend == "precise")
    bits = Constants.RUST_MODE_JSON
    if existence:
        bits |= Constants.RUST_MODE_EXISTENCE_ONLY
    if allow:
        bits |= Constants.RUST_MODE_ALLOW_DUPLICATE_JSON_KEYS
    return engine.search_file(str(path), query, bits, max_per_file=10_000,
                              max_json_depth=20_000, max_json_size=1024 * 1024 * 1024)


def validate_warm(name, warm, backend):
    count = len(warm) if backend == "native" else (warm[1] if warm else 0)
    if name == "small-no-match":
        assert not warm, (name, "unexpected hit")
    elif name in ("small-sparse", "small-existence", "wide-object"):
        assert count == 1, (name, "missing or extra result")
    else:
        assert count >= 10_000, (name, "missing results")


def measure(engine, case, repeats, backend="native"):
    name, path, _, _ = case
    samples = {False: [], True: []}
    fingerprints = {}

    for allow in samples:
        warm = run_search(engine, case, allow, backend)
        validate_warm(name, warm, backend)
        fingerprints[allow] = fingerprint(warm, backend)
    assert fingerprints[False] == fingerprints[True], (name, "policy changed unique-key results")
    for repeat in range(repeats):
        for allow in ((False, True) if repeat % 2 == 0 else (True, False)):
            gc.collect()
            started = time.perf_counter()
            result = run_search(engine, case, allow, backend)
            samples[allow].append(time.perf_counter() - started)
            assert fingerprint(result, backend) == fingerprints[allow], (name, "unstable results")
    return {"case": name, "backend": backend, "size_bytes": path.stat().st_size, "repeats": repeats,
            "fingerprint": fingerprints[False],
            "deny_median_s": statistics.median(samples[False]),
            "allow_median_s": statistics.median(samples[True]),
            "deny_samples_s": samples[False], "allow_samples_s": samples[True]}


def compare(engines, case, repeats, backend):
    name, path, _, _ = case
    order = [(label, allow) for label in engines for allow in (False, True)]
    samples = {key: [] for key in order}
    signatures = set()
    for label, allow in order:
        warm = run_search(engines[label], case, allow, backend)
        validate_warm(name, warm, backend)
        signatures.add(fingerprint(warm, backend))
    assert len(signatures) == 1, (name, "engine/policy changed results")
    signature = signatures.pop()
    for repeat in range(repeats):
        for label, allow in (order if repeat % 2 == 0 else order[::-1]):
            gc.collect()
            started = time.perf_counter()
            result = run_search(engines[label], case, allow, backend)
            samples[label, allow].append(time.perf_counter() - started)
            assert fingerprint(result, backend) == signature, (name, "unstable results")
    rows = []
    for label in engines:
        rows.append({"engine": label, "deny_median_s": statistics.median(samples[label, False]),
                     "allow_median_s": statistics.median(samples[label, True]),
                     "deny_samples_s": samples[label, False], "allow_samples_s": samples[label, True]})
    return {"case": name, "backend": backend, "size_bytes": path.stat().st_size,
            "repeats": repeats, "fingerprint": signature, "engines": rows}


def load_engine(path, namespace):
    spec = importlib.util.spec_from_file_location(f"{namespace}.sf_engine", path.resolve())
    if spec is None or spec.loader is None:
        raise ValueError("cannot load the native engine")
    engine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine)
    return engine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-path", type=Path, default=ROOT / "src/rust_engine/sf_engine.pyd")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--compare-engine", type=Path, help="interleave this saved baseline with --engine-path")
    parser.add_argument("--repeats", type=int, default=18)
    parser.add_argument("--backend", choices=("native", "normal", "precise"), default="native")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    if args.backend != "native":
        logging.disable(logging.CRITICAL)
    engine = load_engine(args.engine_path, "current")
    with tempfile.TemporaryDirectory(prefix="sf-json-policy-") as temporary:
        cases = create_cases(args.data_dir or Path(temporary))
        report = {"engine_sha256": hashlib.sha256(args.engine_path.read_bytes()).hexdigest()}
        if args.compare_engine:
            engines = {"baseline": load_engine(args.compare_engine, "baseline"), "current": engine}
            report["baseline_sha256"] = hashlib.sha256(args.compare_engine.read_bytes()).hexdigest()
            report["measurements"] = [compare(engines, case, args.repeats, args.backend) for case in cases]
        else:
            report["measurements"] = [measure(engine, case, args.repeats, args.backend) for case in cases]
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
