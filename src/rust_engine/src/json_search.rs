#![allow(dead_code)]
use crate::types::SearchMatch;
use serde_json::Value as JsonValue;

struct JsonSearchContext<'a> {
    pattern: &'a str,
    pattern_lower: String,
    ac: &'a aho_corasick::AhoCorasick,
    is_exact: bool,
    mmap: &'a [u8],
    last_offset: usize,
    last_line: usize,
    results: Vec<SearchMatch>,
    stop_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
}

pub fn search_json_file(
    mmap: &[u8],
    pattern: &str,
    ac: &aho_corasick::AhoCorasick,
    is_exact: bool,
    stop_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
) -> Vec<SearchMatch> {
    let mut ctx = JsonSearchContext {
        pattern,
        pattern_lower: pattern.to_lowercase(),
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

    // 스트리밍 데시리얼라이저를 사용하여 이터레이터 방식으로 접근 시도
    let deserializer = serde_json::Deserializer::from_slice(mmap);
    let iter = deserializer.into_iter::<JsonValue>();

    for value_res in iter {
        if ctx.stop_flag.load(std::sync::atomic::Ordering::Relaxed) {
            break;
        }
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
                                s.to_lowercase() == ctx.pattern_lower
                            } else {
                                ctx.ac.find(s).is_some()
                            };

                            if is_match {
                                // windows().position() 방식의 O(N*M) 검색은 대용량 파일에서 성능 트랩이 됨
                                // mmap 상에서 직접적인 바이트 매칭이나 Aho-Corasick 재활용을 통해 정확한 오프셋 산출
                                let mut found_pos = None;
                                let mut found_len = 0;

                                if ctx.last_offset < ctx.mmap.len() {
                                    // Aho-Corasick를 사용하여 mmap 내에서 해당 문자열의 물리적 위치를 고속 검색
                                    if let Some(m) = ctx.ac.find(&ctx.mmap[ctx.last_offset..]) {
                                        found_pos = Some(ctx.last_offset + m.start());
                                        found_len = m.len();
                                    }
                                }
                                
                                if let Some(actual_pos) = found_pos {
                                    let count = ctx.mmap[ctx.last_offset..actual_pos].iter().filter(|&&b| b == b'\n').count();
                                    ctx.last_line += count;
                                    ctx.results.push(SearchMatch::new(ctx.last_line, format!("{}\t{}", path, s), Some(actual_pos), Some(found_len)));
                                    ctx.last_offset = actual_pos + found_len;
                                } else {
                                    ctx.results.push(SearchMatch::new(ctx.last_line, format!("{}\t{}", path, s), None, None));
                                }
                            }
                        }
                        JsonValue::Number(n) => {
                            let s = n.to_string();
                            let is_match = if ctx.is_exact { s == *ctx.pattern } else { ctx.ac.find(&s).is_some() };
                            if is_match {
                                let mut found_pos = None;
                                if ctx.last_offset < ctx.mmap.len() {
                                    if let Some(m) = ctx.ac.find(&ctx.mmap[ctx.last_offset..]) {
                                        found_pos = Some(ctx.last_offset + m.start());
                                    }
                                }
                                if let Some(actual_pos) = found_pos {
                                    let count = ctx.mmap[ctx.last_offset..actual_pos].iter().filter(|&&b| b == b'\n').count();
                                    ctx.last_line += count;
                                    ctx.results.push(SearchMatch::new(ctx.last_line, format!("{}\t{}", path, s), Some(actual_pos), Some(s.len())));
                                    ctx.last_offset = actual_pos + s.len();
                                } else {
                                    ctx.results.push(SearchMatch::new(ctx.last_line, format!("{}\t{}", path, s), None, None));
                                }
                            }
                        }
                        _ => {}
                    }
                }
            }
            Err(_) => break, // 파싱 오류 시 중단 (또는 무시)
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
    let pattern_lower = pattern.to_lowercase();
    
    // 전체 바이트 스캔을 통한 선행 필터링 (파싱 오버헤드 방지)
    if !is_exact && ac.find(mmap).is_none() {
        return false;
    }

    // 스트리밍 방식으로 구조 탐색하여 메모리 스파이크 억제
    let deserializer = serde_json::Deserializer::from_slice(mmap);
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
                            s.to_lowercase() == pattern_lower
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
