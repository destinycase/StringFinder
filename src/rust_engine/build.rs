//! 엔진 소스 지문(SOURCE_HASH)을 만들어 엔진에 넣습니다(7.7.6).
//!
//! 엔진 버전은 앱 버전과 따로 매기므로, "이 엔진이 지금 소스로 빌드됐는가"는 버전 숫자가 아니라 이 지문으로
//! 확인합니다. 같은 계산을 `tools/engine_source_hash.py`가 하며, 둘이 어긋나면 개발 환경·CI 확인이 실패합니다.
//!
//! 대상: `src/`·`data/` 아래 모든 파일, `Cargo.toml`(엔진 자체 버전 줄 제외), `Cargo.lock`(sf_engine 버전 줄 제외).
//! 줄바꿈은 LF로 맞춰(Windows 체크아웃의 CRLF와 CI의 LF가 같게) 계산합니다. 해시는 FNV-1a 64비트입니다.

use std::fs;
use std::path::{Path, PathBuf};

const FNV_OFFSET: u64 = 0xcbf2_9ce4_8422_2325;
const FNV_PRIME: u64 = 0x0000_0100_0000_01b3;

fn fnv(hash: &mut u64, bytes: &[u8]) {
    for byte in bytes {
        *hash ^= u64::from(*byte);
        *hash = hash.wrapping_mul(FNV_PRIME);
    }
}

fn normalized(bytes: Vec<u8>) -> Vec<u8> {
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'\r' && i + 1 < bytes.len() && bytes[i + 1] == b'\n' {
            i += 1;
            continue;
        }
        out.push(bytes[i]);
        i += 1;
    }
    out
}

fn collect(dir: &Path, root: &Path, out: &mut Vec<(String, PathBuf)>) {
    let Ok(entries) = fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            collect(&path, root, out);
        } else if path.is_file() {
            let rel = path
                .strip_prefix(root)
                .unwrap_or(&path)
                .to_string_lossy()
                .replace('\\', "/");
            out.push((rel, path));
        }
    }
}

/// `Cargo.toml`에서 [package]의 version 줄을, `Cargo.lock`에서 sf_engine 바로 아래 version 줄을 뺍니다.
fn without_own_version(text: &str, lock: bool) -> String {
    let mut out = Vec::new();
    let mut skip_next_version = false;
    let mut in_package = false;
    let mut removed = false;
    for line in text.lines() {
        let trimmed = line.trim();
        if lock {
            if trimmed == "name = \"sf_engine\"" {
                skip_next_version = true;
            } else if skip_next_version && trimmed.starts_with("version = ") {
                skip_next_version = false;
                continue;
            }
        } else if trimmed.starts_with('[') {
            in_package = trimmed == "[package]";
        } else if in_package && !removed && trimmed.starts_with("version = ") {
            removed = true;
            continue;
        }
        out.push(line);
    }
    out.join("\n")
}

fn main() {
    let root = PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR"));
    let mut files = Vec::new();
    for sub in ["src", "data"] {
        collect(&root.join(sub), &root, &mut files);
        println!("cargo:rerun-if-changed={sub}");
    }
    files.sort_by(|a, b| a.0.cmp(&b.0));

    let mut hash = FNV_OFFSET;
    for (rel, path) in &files {
        fnv(&mut hash, b"file:");
        fnv(&mut hash, rel.as_bytes());
        fnv(&mut hash, b"\n");
        fnv(&mut hash, &normalized(fs::read(path).unwrap_or_default()));
        fnv(&mut hash, b"\n");
    }
    for (name, lock) in [("Cargo.toml", false), ("Cargo.lock", true)] {
        println!("cargo:rerun-if-changed={name}");
        let text =
            String::from_utf8_lossy(&normalized(fs::read(root.join(name)).unwrap_or_default()))
                .into_owned();
        fnv(&mut hash, b"file:");
        fnv(&mut hash, name.as_bytes());
        fnv(&mut hash, b"\n");
        fnv(&mut hash, without_own_version(&text, lock).as_bytes());
        fnv(&mut hash, b"\n");
    }
    println!("cargo:rustc-env=SF_SOURCE_HASH={hash:016x}");
}
