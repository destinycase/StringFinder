"""StringFinder 엔진 벤치마크: 표본 만들기, 측정, 기준선 비교를 한 도구로 합니다.

7.1.1부터 비교 기준은 옛 엔진이 아니라 저장해 둔 기준선(``docs/benchmark_baseline.json``)입니다.

사용법(저장소 루트에서):
    python tools/benchmark.py prepare                  # 표본 생성(data/benchmark/v7, 약 2.5GB)
    python tools/benchmark.py run                      # 측정 후 기준선과 비교
    python tools/benchmark.py run --rounds 16 --only K-LF-h,N-h
    python tools/benchmark.py run --write-baseline     # 측정값을 새 기준선으로 저장

측정 방법
- 엔진(``sf_engine.search_dir``)의 폴더 검색만 잽니다. 앱의 결과 정리·화면·저장은 포함하지 않습니다.
- 한 회차에 모든 표본을 한 번씩 돌고, 이를 ``--rounds``번 반복합니다. 첫 회차는 데우기로 버립니다.
  표본을 번갈아 돌리므로 디스크 캐시·CPU 클럭 변화가 한 표본에 몰리지 않습니다.
- 표본마다 중앙값과 최소·최대, 찾은 일치 수, 결과 지문(줄 번호·내용·건너뜀 목록의 해시)을 남깁니다.
- 기준선과 비교할 때 지문이 다르면 "결과 다름", 중앙값이 기준선의 ``--threshold``배(기본 1.15)를
  넘으면 "느려짐"으로 표시하고 종료 코드 1을 돌려줍니다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import random
import statistics
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "benchmark" / "v7"
BASELINE = ROOT / "docs" / "benchmark_baseline.json"
DEFAULT_OUT = ROOT / "scratch" / "benchmark"

M_JSON, M_XML, M_ARCHIVE, M_EXACT, M_EXCEL, M_EXCLUDE_BINARY = 1, 2, 4, 8, 16, 32
UNLIMITED = 1_000_000_000


@dataclass(frozen=True)
class Sample:
    sid: str
    what: str
    paths: tuple[str, ...]
    query: str
    mode: int = 0
    max_per_file: int = UNLIMITED


SAMPLES: tuple[Sample, ...] = (
    Sample("A", "작은 텍스트 파일 1만 개(파일 순회 비용)", ("set_a",), "target_keyword_a"),
    Sample("B", "50MB 2개 + 1MB 8개 텍스트", ("set_b",), "target_keyword_b"),
    Sample("C", "바이너리·텍스트 반반 500개", ("set_c",), "target_keyword_c"),
    Sample("D", "500MB, 첫 줄에 일치(전체 읽기)", ("set_d",), "target_keyword_d"),
    Sample("E", "100MB ASCII, 끝에 일치", ("set_e",), "target_keyword_e"),
    Sample("F", "30MB JSON 일반 검색", ("set_f",), "target_keyword_f"),
    Sample("G", "깊이 1000 XML + 50MB 한 줄", ("set_g",), "target_keyword_g"),
    Sample("H", "96MB 대부분 0바이트, 끝에 일치", ("set_h",), "target_keyword_h"),
    Sample("I", "50MB 줄바꿈 없음", ("set_i",), "target_keyword_i"),
    Sample("J", "XLSX 두 시트 값 검색", ("set_j",), "target_keyword_j", M_EXCEL),
    Sample("K-LF-a", "한글 위주 238MB(LF), 영문 검색어", ("k_lf",), "target_keyword_k"),
    Sample("K-LF-h", "한글 위주 238MB(LF), 한글 검색어", ("k_lf",), "목표단어"),
    Sample("K-CRLF-h", "한글 위주 240MB(CRLF), 한글 검색어", ("k_crlf",), "목표단어"),
    Sample("N-a", "CP949 50MB, 영문 검색어", ("n_cp949",), "target_keyword_n"),
    Sample("N-h", "CP949 50MB, 한글 검색어", ("n_cp949",), "목표단어"),
    Sample("O-a", "UTF-16LE 33MB, 영문 검색어", ("o_utf16",), "target_keyword_o"),
    Sample("O-h", "UTF-16LE 33MB, 한글 검색어", ("o_utf16",), "목표단어"),
    Sample("P-json", "한글 값 JSON 52MB, 값만 검색", ("p_json",), "목표단어", M_JSON),
    Sample("P-json-x", "같은 JSON, 정확히 일치", ("p_json",), "목표단어", M_JSON | M_EXACT),
    Sample("P-xml", "한글 값 XML 45MB, 값만 검색", ("p_xml",), "목표단어", M_XML),
    Sample("P-xml-x", "같은 XML, 정확히 일치", ("p_xml",), "목표단어", M_XML | M_EXACT),
    Sample("P-xlsx", "한글 값 XLSX 6MB, 값만 검색", ("p_xlsx",), "목표단어", M_EXCEL),
    Sample("P-xlsx-x", "같은 XLSX, 정확히 일치", ("p_xlsx",), "목표단어", M_EXCEL | M_EXACT),
    Sample("R-a", "4KiB 긴 줄 246MB(CRLF), 영문 검색어", ("r_long",), "target_keyword_r"),
    Sample("R-h", "4KiB 긴 줄 246MB(CRLF), 한글 검색어", ("r_long",), "목표단어"),
    Sample("Q", "폴더 세 개(A·B·C) 동시 검색", ("set_a", "set_b", "set_c"), "target_keyword"),
    Sample("Q-h", "큰 파일 폴더 세 개(K·R·N) 동시 검색, 한글", ("k_lf", "r_long", "n_cp949"), "목표단어"),
    Sample("S-ascii", "50MB, 일치 약 70만 건(ASCII)", ("s_ascii",), "dolor"),
    Sample("S-ko", "50MB, 일치 수십만 건(한글)", ("s_ko",), "검토"),
    Sample("S-ascii-cap", "S-ascii, 파일당 1만 건 상한", ("s_ascii",), "dolor", 0, 10_000),
    Sample("S-ko-cap", "S-ko, 파일당 1만 건 상한", ("s_ko",), "검토", 0, 10_000),
    Sample("T-bin-h", "바이너리 300MB + 텍스트 소량, 바이너리 제외·한글", ("t_bin",), "목표단어",
           M_EXCLUDE_BINARY),
    Sample("U-archive", "UE 현지화 archive 40MB, 값만 검색", ("u_archive",), "목표단어", M_ARCHIVE),
)


# ---------------------------------------------------------------------------
# 표본 만들기
# ---------------------------------------------------------------------------

def _hangul_words(seed: int) -> tuple[random.Random, list[str]]:
    rng = random.Random(seed)
    syl = [chr(c) for c in range(0xAC00, 0xD7A4)]
    return rng, ["".join(rng.choice(syl) for _ in range(rng.randint(1, 4))) for _ in range(4000)]


def _repeat(path: Path, block: str | bytes, reps: int, tail: str | bytes, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = block.encode(encoding) if isinstance(block, str) else block
    end = tail.encode(encoding) if isinstance(tail, str) else tail
    with open(path, "wb") as handle:
        for _ in range(reps):
            handle.write(data)
        handle.write(end)


def _make_basic(d: Path) -> None:
    """A~J: 6.x부터 쓰던 기본 묶음과 같은 내용입니다."""
    a = d / "set_a"
    a.mkdir(parents=True)
    for i in range(10000):
        text = "This is a small test file. " * 50 + ("target_keyword_a " if i % 100 == 0 else "")
        (a / f"file_{i:05d}.txt").write_text(text, encoding="utf-8")
    b = d / "set_b"
    b.mkdir()
    line = b"data " * 200 + b"\n"
    for i in range(10):
        size = 50 * 1024 * 1024 if i < 2 else 1024 * 1024
        with open(b / f"large_{i}.txt", "wb") as handle:
            written = 0
            while written < size:
                handle.write(line)
                written += len(line)
            if i == 0:
                handle.seek(size // 2)
                handle.write(b"target_keyword_b\n")
    c = d / "set_c"
    c.mkdir()
    rng = random.Random(3)
    for i in range(500):
        binary = i % 2 == 0
        body = bytes(rng.getrandbits(8) for _ in range(1024)) if binary else b"Normal text content. " * 10
        if i % 10 == 0:
            body += b"target_keyword_c"
        (c / f"file_{i}.{'bin' if binary else 'txt'}").write_bytes(body)
    _repeat(d / "set_d" / "large_full_scan.txt", b"Regular noise line for padding.\n" * 1000, 16384, b"",)
    with open(d / "set_d" / "large_full_scan.txt", "r+b") as handle:
        handle.write(b"target_keyword_d\n")
    _repeat(d / "set_e" / "ascii_fast.txt", "Standard ASCII line for fast-path testing.\n" * 1000, 2440,
            "target_keyword_e\n")
    rows = [{"id": i, "val": "some random string content " * 10} for i in range(100000)]
    rows[50000]["keyword"] = "target_keyword_f"
    (d / "set_f").mkdir()
    (d / "set_f" / "monster.json").write_text(json.dumps({"metadata": "benchmark", "data": rows}), encoding="utf-8")
    (d / "set_g").mkdir()
    with open(d / "set_g" / "deep.xml", "w", encoding="utf-8") as handle:
        handle.write('<?xml version="1.0" encoding="utf-8"?>\n<root>\n')
        for i in range(1000):
            handle.write(f"<node_{i}>\n")
        handle.write("  <content>target_keyword_g</content>\n  <padding>" + "x" * 50 * 1024 * 1024 + "</padding>\n")
        for i in range(999, -1, -1):
            handle.write(f"</node_{i}>\n")
        handle.write("</root>\n")
    (d / "set_h").mkdir()
    keyword = b"target_keyword_h\n"
    with open(d / "set_h" / "sparse_stress.txt", "wb") as handle:
        handle.write(b"Start of Stress\n")
        handle.seek(96 * 1024 * 1024 - len(keyword))
        handle.write(keyword)
    _repeat(d / "set_i" / "no_newline.txt", b"x" * (1024 * 1024), 50, b"target_keyword_i")
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.title = "BenchMain"
    sheet.append(["id", "source", "translation"])
    for i in range(1, 5001):
        sheet.append([i, "prefix_target_keyword_j_suffix" if i == 2500 else f"source_{i}", f"translation_{i}"])
    aux = book.create_sheet("BenchAux")
    aux.append(["key", "value"])
    for i in range(1, 2001):
        aux.append([f"k{i}", "target_keyword_j_aux" if i == 1333 else f"aux_value_{i}"])
    (d / "set_j").mkdir()
    book.save(d / "set_j" / "excel_mix.xlsx")


def _make_korean(d: Path) -> None:
    """K·N·O·R·P: 한글·여러 인코딩·긴 줄·구조화 파일."""
    rng, words = _hangul_words(42)

    def kline() -> str:
        return " ".join(rng.choice(words) for _ in range(rng.randint(6, 14))) + " 2026."

    lines = [kline() for _ in range(20000)]
    for name, nl in (("k_lf", "\n"), ("k_crlf", "\r\n")):
        _repeat(d / name / f"{name}.txt", nl.join(lines) + nl, 130, "끝 부분 target_keyword_k 목표단어 확인" + nl)
    nblock = "\r\n".join(lines[:5000]) + "\r\n"
    _repeat(d / "n_cp949" / "n_cp949.txt", nblock, 150, "끝 target_keyword_n 목표단어\r\n", "cp949")
    _repeat(d / "o_utf16" / "o_utf16.txt", "﻿" + nblock, 1, "", "utf-16-le")
    with open(d / "o_utf16" / "o_utf16.txt", "ab") as handle:
        for _ in range(79):
            handle.write(nblock.encode("utf-16-le"))
        handle.write("끝 target_keyword_o 목표단어\r\n".encode("utf-16-le"))

    def rline() -> str:
        parts: list[str] = []
        size = 0
        while size < 4096:
            word = rng.choice(words) if rng.random() < 0.5 else f"word{rng.randint(0, 9999)}"
            parts.append(word)
            size += len(word.encode()) + 1
        return " ".join(parts)

    _repeat(d / "r_long" / "r_long.txt", "\r\n".join(rline() for _ in range(2000)) + "\r\n", 30,
            "끝 target_keyword_r 목표단어\r\n")
    data: list[dict] = [{"id": i, "name": rng.choice(words), "desc": kline(), "tag": rng.choice(words)} for i in range(300000)]
    data[290000].update(name="목표단어", desc="앞 목표단어 뒤")
    (d / "p_json").mkdir()
    with open(d / "p_json" / "p.json", "w", encoding="utf-8") as text_out:
        json.dump(data, text_out, ensure_ascii=False, indent=1)
    (d / "p_xml").mkdir()
    with open(d / "p_xml" / "p.xml", "w", encoding="utf-8") as xml_out:
        xml_out.write("<root>\n")
        for row in data:
            xml_out.write(f"  <item id=\"{row['id']}\"><name>{row['name']}</name><desc>{row['desc']}</desc></item>\n")
        xml_out.write("</root>\n")
    from openpyxl import Workbook

    book = Workbook(write_only=True)
    sheet = book.create_sheet("s")
    for row in data[:60000]:
        sheet.append([row["id"], row["name"], row["desc"], row["tag"]])
    sheet.append([0, "목표단어", "앞 목표단어 뒤", "x"])
    (d / "p_xlsx").mkdir()
    book.save(d / "p_xlsx" / "p.xlsx")
    # U: UE 현지화 archive(구조화 JSON). 원문은 영문, 번역문은 한글입니다.
    children: list[dict] = [{"Key": f"KEY_{i:06d}", "Source": {"Text": f"Source text number {i} for localization"},
                 "Translation": {"Text": kline()}} for i in range(200000)]
    children[150000]["Translation"]["Text"] = "앞 목표단어 뒤"
    archive = {"FormatVersion": 2, "Namespace": "",
               "Subnamespaces": [{"Namespace": f"NS_{n}", "Children": children[n * 20000:(n + 1) * 20000]}
                                 for n in range(10)]}
    (d / "u_archive").mkdir()
    (d / "u_archive" / "Game.archive").write_text(json.dumps(archive, ensure_ascii=False, indent=4), encoding="utf-8")


def _make_dense_and_binary(d: Path) -> None:
    """S: 일치가 아주 많은 파일, T: 바이너리 위주 폴더."""
    rng = random.Random(11)
    latin = ("lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor incididunt ut labore "
             "et dolore magna aliqua").split()
    ascii_block = "\n".join(" ".join(rng.choice(latin) for _ in range(12)) for _ in range(20000)) + "\n"
    _repeat(d / "s_ascii" / "s_hits_ascii.txt", ascii_block, max(1, 50 * 1024 * 1024 // len(ascii_block)), "")
    _, words = _hangul_words(5)
    ko_pool = words[:200] + ["검토", "version", "확인", "결과"]
    ko_block = "\n".join(" ".join(rng.choice(ko_pool) for _ in range(10)) for _ in range(20000)) + "\n"
    _repeat(d / "s_ko" / "s_hits_ko.txt", ko_block, max(1, 50 * 1024 * 1024 // len(ko_block.encode())), "")
    t = d / "t_bin"
    t.mkdir()
    chunk = bytes(rng.getrandbits(8) for _ in range(1024 * 1024))
    for i in range(30):
        _repeat(t / f"blob_{i:02d}.bin", chunk, 10, b"")
    for i in range(20):
        (t / f"note_{i:02d}.txt").write_text("일반 텍스트 문서\n" * 100 + ("목표단어 포함\n" if i % 5 == 0 else ""),
                                            encoding="utf-8")


def prepare(force: bool) -> None:
    if DATA.exists():
        if not force:
            print(f"이미 있습니다: {DATA} (다시 만들려면 --force)")
            return
        import shutil

        shutil.rmtree(DATA)
    DATA.mkdir(parents=True)
    for step in (_make_basic, _make_korean, _make_dense_and_binary):
        started = time.perf_counter()
        step(DATA)
        print(f"{step.__name__}: {time.perf_counter() - started:.1f}s", flush=True)
    total = sum(p.stat().st_size for p in DATA.rglob("*") if p.is_file())
    print(f"완료: {DATA} ({total / 1024**3:.2f} GiB)")


# ---------------------------------------------------------------------------
# 측정
# ---------------------------------------------------------------------------

def _engine():
    sys.path.insert(0, str(ROOT / "src" / "rust_engine"))
    from rust_engine import sf_engine  # type: ignore[attr-defined]  # noqa: PLC0415

    return sf_engine


def measure_once(engine, sample: Sample, data_dir: Path = DATA) -> tuple[float, int, str]:
    """표본 한 번 검색: (밀리초, 일치 수, 결과 지문)."""
    roots = [str(data_dir / p) for p in sample.paths]
    started = time.perf_counter()
    results, skipped = engine.search_dir(root_paths=roots, pattern=sample.query, mode_bits=sample.mode,
                                         exclude_hidden=True, max_per_file=sample.max_per_file)
    elapsed = (time.perf_counter() - started) * 1000
    base = str(data_dir)
    lines = sorted((os.path.relpath(p, base), [(m.line, unicodedata.normalize("NFC", m.content)) for m in ms])
                   for p, ms in results)
    skips = sorted(os.path.relpath(str(s[0] if isinstance(s, tuple) else s), base) for s in skipped)
    digest = hashlib.sha256(repr((lines, skips)).encode("utf-8")).hexdigest()[:16]
    return elapsed, sum(len(ms) for _p, ms in lines), digest


def run(rounds: int, only: list[str] | None, data_dir: Path = DATA, engine=None) -> dict:
    engine = engine or _engine()
    samples = [s for s in SAMPLES if not only or s.sid in only]
    missing = [s.sid for s in samples if not all((data_dir / p).exists() for p in s.paths)]
    if missing:
        raise SystemExit(f"표본이 없습니다: {', '.join(missing)} (먼저 prepare)")
    times: dict[str, list[float]] = {s.sid: [] for s in samples}
    meta: dict[str, tuple[int, str]] = {}
    for rnd in range(rounds):
        for sample in samples:
            ms, hits, digest = measure_once(engine, sample, data_dir)
            if rnd > 0 or rounds == 1:
                times[sample.sid].append(ms)
            previous = meta.setdefault(sample.sid, (hits, digest))
            if previous != (hits, digest):
                raise SystemExit(f"{sample.sid}: 회차마다 결과가 다릅니다 {previous} != {(hits, digest)}")
        print(f"회차 {rnd + 1}/{rounds} 완료", flush=True)
    kst = timezone(timedelta(hours=9))
    return {
        "measured_at": datetime.now(kst).strftime("%Y-%m-%d %H:%M:%S %z"),
        "engine_version": getattr(engine, "ENGINE_VERSION", "unknown"),
        "rounds": rounds,
        "machine": {"platform": platform.platform(), "processor": platform.processor(),
                    "cpu_count": os.cpu_count()},
        "samples": {
            s.sid: {"what": s.what, "median_ms": round(statistics.median(times[s.sid]), 2),
                    "min_ms": round(min(times[s.sid]), 2), "max_ms": round(max(times[s.sid]), 2),
                    "hits": meta[s.sid][0], "digest": meta[s.sid][1]}
            for s in samples
        },
    }


def compare(result: dict, baseline: dict, threshold: float) -> list[str]:
    """기준선과 비교한 표 줄을 돌려주고, 문제가 있으면 줄 끝에 표시합니다."""
    out = []
    for sid, row in result["samples"].items():
        base = baseline.get("samples", {}).get(sid)
        if base is None:
            out.append(f"{sid:12s} {row['median_ms']:10.2f}ms  (기준선 없음)")
            continue
        ratio = row["median_ms"] / base["median_ms"] if base["median_ms"] else float("inf")
        flags = []
        if row["digest"] != base["digest"] or row["hits"] != base["hits"]:
            flags.append("결과 다름")
        if ratio > threshold:
            flags.append("느려짐")
        out.append(f"{sid:12s} {row['median_ms']:10.2f}ms  기준 {base['median_ms']:10.2f}ms  "
                   f"비율 {ratio:5.2f}  일치 {row['hits']}  {' '.join(flags)}".rstrip())
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p_prepare = sub.add_parser("prepare", help="표본 생성")
    p_prepare.add_argument("--force", action="store_true")
    p_run = sub.add_parser("run", help="측정")
    p_run.add_argument("--rounds", type=int, default=16)
    p_run.add_argument("--only", help="쉼표로 구분한 표본 ID")
    p_run.add_argument("--threshold", type=float, default=1.15)
    p_run.add_argument("--out", type=Path, default=None, help="결과 JSON 경로(기본 scratch/benchmark/)")
    p_run.add_argument("--baseline", type=Path, default=BASELINE)
    p_run.add_argument("--write-baseline", action="store_true", help="결과를 기준선 파일로 저장")
    args = parser.parse_args(argv)
    if args.command == "prepare":
        prepare(args.force)
        return 0
    result = run(args.rounds, args.only.split(",") if args.only else None)
    out = args.out or DEFAULT_OUT / f"bench-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"결과: {out}")
    if args.write_baseline:
        args.baseline.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"기준선 저장: {args.baseline}")
        return 0
    if not args.baseline.exists():
        for sid, row in result["samples"].items():
            print(f"{sid:12s} {row['median_ms']:10.2f}ms  ({row['min_ms']:.2f}~{row['max_ms']:.2f})  일치 {row['hits']}")
        return 0
    lines = compare(result, json.loads(args.baseline.read_text(encoding="utf-8")), args.threshold)
    print("\n".join(lines))
    return 1 if any(line.endswith(("결과 다름", "느려짐")) for line in lines) else 0


if __name__ == "__main__":
    raise SystemExit(main())
