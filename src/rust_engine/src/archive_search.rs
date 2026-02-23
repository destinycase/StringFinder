#![allow(dead_code)]
use crate::types::SearchMatch;
use serde_json::Value as JsonValue;

pub fn check_archive_file(
    mmap: &[u8],
    pattern: &str,
    ac: &aho_corasick::AhoCorasick,
    is_exact: bool,
    stop_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
) -> bool {
    // 선행 필터링
    if !is_exact && ac.find(mmap).is_none() {
        return false;
    }

    let pat_lower = pattern.to_lowercase();
    let deserializer = serde_json::Deserializer::from_slice(mmap);
    let iter = deserializer.into_iter::<JsonValue>();

    for v in iter.flatten() {
        if stop_flag.load(std::sync::atomic::Ordering::Relaxed) {
            break;
        }
        if let Some(namespaces) = v.get("Subnamespaces").and_then(|v| v.as_array()) {
            for ns in namespaces {
                if let Some(children) = ns.get("Children").and_then(|v| v.as_array()) {
                    for child in children {
                        let s_text = child.get("Source").and_then(|v| v.get("Text")).and_then(|v| v.as_str()).unwrap_or("").to_lowercase();
                        let t_text = child.get("Translation").and_then(|v| v.get("Text")).and_then(|v| v.as_str()).unwrap_or("").to_lowercase();
                        if is_exact {
                            if s_text == pat_lower || t_text == pat_lower {
                                return true;
                            }
                        } else if ac.find(&s_text).is_some() || ac.find(&t_text).is_some() {
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
) -> Vec<SearchMatch> {
    let mut results = Vec::new();
    let pat_lower = pattern.to_lowercase();
    
    let mut search_from = 0;
    
    let deserializer = serde_json::Deserializer::from_slice(mmap);
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
                        let s_text = raw_s.to_lowercase();
                        let t_text = raw_t.to_lowercase();

                        let is_match = if is_exact {
                            s_text == pat_lower || t_text == pat_lower
                        } else {
                            ac.find(&s_text).is_some() || ac.find(&t_text).is_some()
                        };

                        if is_match {
                            let key = child.get("Key").and_then(|v| v.as_str()).unwrap_or("");
                            // 성능 최적화: windows().position() 대신 memchr를 활용한 고속 바이트 탐색
                            let key_pattern = format!("\"Key\": \"{}\"", key);
                            let key_bytes = key_pattern.as_bytes();
                            if let Some(key_pos_rel) = crate::utils::find_bytes(&mmap[search_from..], key_bytes) {
                                let key_pos = search_from + key_pos_rel;
                                
                                // Key 위치 이후에서 실제 패턴 탐색
                                if let Some(mat) = ac.find(&mmap[key_pos..]) {
                                    let pos = key_pos + mat.start();
                                    let line = mmap[..pos].iter().filter(|&&b| b == b'\n').count() + 1;
                                    
                                    // 다음 검색을 위해 search_from 업데이트 (Key 이후로 이동)
                                    search_from = key_pos + key_pattern.len();
                                    
                                    let content = format!(
                                        "{}\t{}\t{}\t{}",
                                        ns_name, 
                                        key,
                                        raw_s,
                                        raw_t
                                    );
                                    results.push(SearchMatch::new(line, content, Some(pos), None));
                                    continue;
                                }
                            }

                            // Key 앵커 탐색 실패 시 펄백: 이전 방식(전역 검색) 유지
                            if let Some(mat) = ac.find(&mmap[search_from..]) {
                                let pos = search_from + mat.start();
                                let line = mmap[..pos].iter().filter(|&&b| b == b'\n').count() + 1;
                                // [상] 패닉 방지: mat.end()가 파일 경계를 넘지 않도록 보장하며, 
                                // 다음 검색 위치를 현재 매치 시작점 바로 다음으로 설정하여 중복 누락 방지
                                search_from = pos + 1;
                                if search_from > mmap.len() {
                                    search_from = mmap.len();
                                }
                                
                                let content = format!(
                                    "{}\t{}\t{}\t{}",
                                    ns_name, 
                                    child.get("Key").and_then(|v| v.as_str()).unwrap_or("none"),
                                    raw_s,
                                    raw_t
                                );
                                results.push(SearchMatch::new(line, content, Some(pos), None));
                            }
                        }
                    }
                }
            }
        }
    }
    results
}
