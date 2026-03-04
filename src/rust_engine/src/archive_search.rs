#![allow(dead_code)]
use crate::types::RawMatch;
use serde_json::Value as JsonValue;

pub fn check_archive_file(
    mmap: &[u8],
    pattern: &str,
    ac: &aho_corasick::AhoCorasick,
    is_exact: bool,
    stop_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
    _max_per_file: usize,
) -> bool {
    // 선행 필터링 (가장 빠른 바이트 레벨 체크)
    if !is_exact && ac.find(mmap).is_none() {
        return false;
    }

    // UTF-8 BOM 스킵 (BOM이 있는 경우 3바이트를 건너뜁니다)
    let parse_mmap = if mmap.starts_with(b"\xef\xbb\xbf") {
        &mmap[3..]
    } else {
        mmap
    };

    let pat_upper = pattern.to_lowercase().to_uppercase();
    let deserializer = serde_json::Deserializer::from_slice(parse_mmap);
    let iter = deserializer.into_iter::<JsonValue>();

    for v in iter.flatten() {
        if stop_flag.load(std::sync::atomic::Ordering::Relaxed) {
            break;
        }
        if let Some(namespaces) = v.get("Subnamespaces").and_then(|v| v.as_array()) {
            for ns in namespaces {
                
                if let Some(children) = ns.get("Children").and_then(|v| v.as_array()) {
                    for child in children {
                        let s_text = child.get("Source").and_then(|v| v.get("Text")).and_then(|v| v.as_str()).unwrap_or("");
                        let t_text = child.get("Translation").and_then(|v| v.get("Text")).and_then(|v| v.as_str()).unwrap_or("");
                        
                        let is_match = if is_exact {
                            s_text.trim().to_lowercase().to_uppercase() == pat_upper || t_text.trim().to_lowercase().to_uppercase() == pat_upper
                        } else {
                            // 소문자 변환 없이 Aho-Corasick 검색을 직접 수행합니다.
                            ac.find(s_text).is_some() || ac.find(t_text).is_some()
                        };

                        if is_match {
                            return true;
                        }
                    }
                }
            }
        }
    }
    false
}

pub fn search_archive_file(
    mmap: &[u8],
    pattern: &str,
    ac: &aho_corasick::AhoCorasick,
    is_exact: bool,
    stop_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
    max_per_file: usize,
) -> Vec<RawMatch> {
    let mut results = Vec::new();
    let pat_upper = pattern.to_lowercase().to_uppercase();
    
    // UTF-8 BOM 스킵
    let mut offset_bonus = 0;
    let parse_mmap = if mmap.starts_with(b"\xef\xbb\xbf") {
        offset_bonus = 3;
        &mmap[3..]
    } else {
        mmap
    };

    // 성능 최적화: line counting을 위한 상태 유지
    let mut last_counted_pos = 0usize;
    let mut current_line = 1usize;
    let mut search_from = offset_bonus;

    let deserializer = serde_json::Deserializer::from_slice(parse_mmap);
    let iter = deserializer.into_iter::<JsonValue>();
    for v in iter.flatten() {
        if stop_flag.load(std::sync::atomic::Ordering::Relaxed) {
            break;
        }
        if let Some(namespaces) = v.get("Subnamespaces").and_then(|v| v.as_array()) {
            for ns in namespaces {
                
                let ns_name = ns.get("Namespace").and_then(|v| v.as_str()).unwrap_or("unknown");
                if let Some(children) = ns.get("Children").and_then(|v| v.as_array()) {
                    for child in children {
                        let raw_s = child.get("Source").and_then(|v| v.get("Text")).and_then(|v| v.as_str()).unwrap_or("");
                        let raw_t = child.get("Translation").and_then(|v| v.get("Text")).and_then(|v| v.as_str()).unwrap_or("");

                        let is_match = if is_exact {
                            raw_s.trim().to_lowercase().to_uppercase() == pat_upper || raw_t.trim().to_lowercase().to_uppercase() == pat_upper
                        } else {
                            ac.find(raw_s).is_some() || ac.find(raw_t).is_some()
                        };

                        if is_match {
                            if results.len() >= max_per_file + 1 {
                                return results;
                            }
                            let key = child.get("Key").and_then(|v| v.as_str()).unwrap_or("");
                            
                            // Key Block을 찾지 않고, 현재 search_from 오프셋에서 시작하여 실제 패턴 매치 위치를 직접 탐색합니다.
                            // 매 텀마다 Key 포매팅(allocation) 및 이중 스캔(find_bytes)이 발생하던 O(N^2)성 오버헤드를 제거합니다.
                            if let Some(mat) = ac.find(&mmap[search_from..]) {
                                let pos = search_from + mat.start();
                                
                                // 누적 라인 카운팅을 통해 매번 처음부터 세지 않도록 최적화합니다.
                                if pos > last_counted_pos {
                                    current_line += memchr::memchr_iter(b'\n', &mmap[last_counted_pos..pos]).count();
                                    last_counted_pos = pos;
                                }
                                
                                // N4: 실제 패턴 매치 끝 이후로 전진해 동일 위치 반복 매치를 방지합니다.
                                search_from = pos + mat.len().max(1);
                                if search_from > mmap.len() { search_from = mmap.len(); }
                                
                                let content = format!(
                                    "{}\t{}\t{}\t{}",
                                    ns_name, 
                                    key,
                                    raw_s,
                                    raw_t
                                );
                                results.push((current_line, content, Some(pos), None));
                            }
                        }
                    }
                }
            }
        }
    }
    results
}
