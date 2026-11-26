"""엔진 소스 지문(SOURCE_HASH)을 계산합니다(7.7.6).

`src/rust_engine/build.rs`와 같은 규칙으로 계산합니다. 엔진에 들어간 지문과 이 값이 같으면, 그 엔진은
지금의 Rust 소스로 빌드된 것입니다. 엔진 버전은 앱 버전과 따로 매기므로 버전 숫자 대신 이 지문으로 확인합니다.

대상: `src/`·`data/` 아래 모든 파일, `Cargo.toml`(엔진 자체 버전 줄 제외), `Cargo.lock`(sf_engine 버전 줄 제외).
줄바꿈은 LF로 맞춥니다. 해시는 FNV-1a 64비트(16자리 16진수)입니다.
"""
from __future__ import annotations

import sys
from pathlib import Path

FNV_OFFSET = 0xCBF29CE484222325
FNV_PRIME = 0x00000100000001B3
MASK = 0xFFFFFFFFFFFFFFFF
DEFAULT_ENGINE_DIR = Path(__file__).resolve().parents[1] / "src" / "rust_engine"


def _fnv(hash_value: int, data: bytes) -> int:
    for byte in data:
        hash_value ^= byte
        hash_value = (hash_value * FNV_PRIME) & MASK
    return hash_value


def _normalized(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def _without_own_version(text: str, lock: bool) -> str:
    out = []
    skip_next_version = False
    in_package = False
    removed = False
    for line in text.split("\n"):
        trimmed = line.strip()
        if lock:
            if trimmed == 'name = "sf_engine"':
                skip_next_version = True
            elif skip_next_version and trimmed.startswith("version = "):
                skip_next_version = False
                continue
        else:
            if trimmed.startswith("["):
                in_package = trimmed == "[package]"
            elif in_package and not removed and trimmed.startswith("version = "):
                removed = True
                continue
        out.append(line)
    # Rust의 str::lines()는 끝의 빈 줄 하나를 만들지 않으므로 맞춥니다.
    if out and out[-1] == "" and text.endswith("\n"):
        out.pop()
    return "\n".join(out)


def engine_source_hash(engine_dir: Path = DEFAULT_ENGINE_DIR) -> str:
    engine_dir = Path(engine_dir)
    files = []
    for sub in ("src", "data"):
        base = engine_dir / sub
        if base.is_dir():
            files.extend(p for p in base.rglob("*") if p.is_file())
    entries = sorted((p.relative_to(engine_dir).as_posix(), p) for p in files)
    value = FNV_OFFSET
    for rel, path in entries:
        value = _fnv(value, b"file:" + rel.encode("utf-8") + b"\n")
        value = _fnv(value, _normalized(path.read_bytes()))
        value = _fnv(value, b"\n")
    for name, lock in (("Cargo.toml", False), ("Cargo.lock", True)):
        text = _normalized((engine_dir / name).read_bytes()).decode("utf-8", errors="replace")
        value = _fnv(value, b"file:" + name.encode("utf-8") + b"\n")
        value = _fnv(value, _without_own_version(text, lock).encode("utf-8"))
        value = _fnv(value, b"\n")
    return f"{value:016x}"


if __name__ == "__main__":
    print(engine_source_hash(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_ENGINE_DIR))
