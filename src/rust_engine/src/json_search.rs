#![allow(dead_code)]
use crate::types::RawMatch;
use serde_json::Value as JsonValue;

struct JsonSearchContext<'a> {
    pattern: &'a str,
    pattern_upper: String,
    ac: &'a aho_corasick::AhoCorasick,
    is_exact: bool,
    mmap: &'a [u8],
    last_offset: usize,
    last_line: usize,
    results: Vec<RawMatch>,
    stop_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
}

pub fn search_json_file(
    mmap: &[u8],
    pattern: &str,
    ac: &aho_corasick::AhoCorasick,
    is_exact: bool,
    stop_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
) -> Vec<RawMatch> {
    let mut ctx = JsonSearchContext {
        pattern,
        pattern_upper: pattern.to_lowercase().to_uppercase(),
        ac,
        is_exact,
        mmap,
        last_offset: 0,
        last_line: 1,
        results: Vec::new(),
        stop_flag,
    };

    let ac_precheck = ctx.ac.find(mmap);
    if !ctx.is_exact && ac_precheck.is_none() {
        return Vec::new();
    }

    // UTF-8 BOM 스킵 (serde_json은 BOM을 지원하지 않아 파싱 에러 발생)
    let parse_mmap = if mmap.starts_with(b"\xef\xbb\xbf") {
        &mmap[3..]
    } else {
        mmap
    };

    // 스트리밍 데시리얼라이저를 사용하여 바이트 오프셋 기반으로 순차 접근
    let deserializer = serde_json::Deserializer::from_slice(parse_mmap);
    let mut stream = deserializer.into_iter::<JsonValue>();

    while let Some(value_res) = stream.next() {
        if ctx.stop_flag.load(std::sync::atomic::Ordering::Relaxed) {
            break;
        }
        
        let byte_offset = stream.byte_offset(); // 현재까지 파싱된 바이트 오프셋 확보

        match value_res {
            Ok(v) => {
                let mut stack = vec![(&v, String::new())];
                while let Some((val, path)) = stack.pop() {
                    if ctx.stop_flag.load(std::sync::atomic::Ordering::Relaxed) {
                        break;
                    }
                    match val {
                        JsonValue::Object(map) => {
                            for (k, v) in map.iter().rev() {
                                let new_path = if path.is_empty() { format!("/{}", k) } else { format!("{}/{}", path, k) };
                                stack.push((v, new_path));
                            }
                        }
                        JsonValue::Array(arr) => {
                            for (i, v) in arr.iter().enumerate().rev() {
                                let new_path = if path.is_empty() { format!("/{}", i) } else { format!("{}/{}", path, i) };
                                stack.push((v, new_path));
                            }
                        }
                        JsonValue::String(s) => {
                            let is_match = if ctx.is_exact {
                                s.trim().to_lowercase().to_uppercase() == ctx.pattern_upper
                            } else {
                                ctx.ac.find(s).is_some()
                            };

                            if is_match {
                                // 하이브리드 스트리밍: 현재 값의 대략적인 위치 주변에서 정밀 위치를 탐색합니다.
                                let mut found_pos = None;
                                let mut found_len = 0;

                                let search_area_start = ctx.last_offset;
                                let search_area_end = std::cmp::min(byte_offset + 1024, ctx.mmap.len());
                                
                                if search_area_start < search_area_end {
                                    if let Some(m) = ctx.ac.find(&ctx.mmap[search_area_start..search_area_end]) {
                                        found_pos = Some(search_area_start + m.start());
                                        found_len = m.len();
                                    }
                                }
                                
                                if let Some(actual_pos) = found_pos {
                                    ctx.last_line += memchr::memchr_iter(b'\n', &ctx.mmap[ctx.last_offset..actual_pos]).count();
                                    ctx.results.push((ctx.last_line, format!("{}\t{}", path, s), Some(actual_pos), Some(found_len)));
                                    ctx.last_offset = actual_pos + found_len;
                                } else {
                                    ctx.results.push((ctx.last_line, format!("{}\t{}", path, s), None, None));
                                }
                            }
                        }
                        JsonValue::Number(n) => {
                            let s = n.to_string();
                            let is_match = if ctx.is_exact { s == *ctx.pattern } else { ctx.ac.find(&s).is_some() };
                            if is_match {
                                let mut found_pos = None;
                                let search_area_end = std::cmp::min(byte_offset + 512, ctx.mmap.len());
                                if ctx.last_offset < search_area_end {
                                    if let Some(m) = ctx.ac.find(&ctx.mmap[ctx.last_offset..search_area_end]) {
                                        found_pos = Some(ctx.last_offset + m.start());
                                    }
                                }
                                if let Some(actual_pos) = found_pos {
                                    ctx.last_line += memchr::memchr_iter(b'\n', &ctx.mmap[ctx.last_offset..actual_pos]).count();
                                    ctx.results.push((ctx.last_line, format!("{}\t{}", path, s), Some(actual_pos), Some(s.len())));
                                    ctx.last_offset = actual_pos + s.len();
                                } else {
                                    ctx.results.push((ctx.last_line, format!("{}\t{}", path, s), None, None));
                                }
                            }
                        }
                        _ => {}
                    }
                }
            }
            Err(_) => break, // 파싱 오류 시 중단
        }
    }

    ctx.results
}

pub fn check_json_file(
    mmap: &[u8],
    pattern: &str,
    ac: &aho_corasick::AhoCorasick,
    is_exact: bool,
    stop_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
) -> bool {
    let pattern_upper = pattern.to_lowercase().to_uppercase();
    
    // 전체 바이트 스캔을 통한 선행 필터링 (파싱 오버헤드 방지)
    if !is_exact && ac.find(mmap).is_none() {
        return false;
    }

    // UTF-8 BOM 스킵
    let parse_mmap = if mmap.starts_with(b"\xef\xbb\xbf") {
        &mmap[3..]
    } else {
        mmap
    };

    // 스트리밍 방식으로 구조 탐색하여 메모리 스파이크 억제
    let deserializer = serde_json::Deserializer::from_slice(parse_mmap);
    let iter = deserializer.into_iter::<JsonValue>();

    for value_res in iter {
        if stop_flag.load(std::sync::atomic::Ordering::Relaxed) {
            break;
        }
        if let Ok(v) = value_res {
            let mut stack = vec![&v];
            while let Some(val) = stack.pop() {
                match val {
                    JsonValue::Object(map) => {
                        for v in map.values().rev() {
                            stack.push(v);
                        }
                    }
                    JsonValue::Array(arr) => {
                        for v in arr.iter().rev() {
                            stack.push(v);
                        }
                    }
                    JsonValue::String(s) => {
                        let is_match = if is_exact {
                            s.to_lowercase().to_uppercase() == pattern_upper
                        } else {
                            ac.find(s).is_some()
                        };
                        if is_match {
                            return true;
                        }
                    }
                    JsonValue::Number(n) => {
                        let s = n.to_string();
                        let is_match = if is_exact { s == *pattern } else { ac.find(&s).is_some() };
                        if is_match {
                            return true;
                        }
                    }
                    _ => {}
                }
            }
        } else {
            break;
        }
    }
    false
}
