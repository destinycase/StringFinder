// 전역 allow 구제: 유지보수 실수로 새 미사용 코드가 CI를 통과하는 것을 방지
mod archive_search;
mod excel_search;
mod json_search;
mod types;
mod utils;
mod xml_search;

use aho_corasick::{AhoCorasickBuilder, MatchKind};
use encoding_rs::UTF_8;
use ignore::WalkBuilder;
use memmap2::Mmap;
use pyo3::prelude::*;
use rayon::prelude::*;
use std::collections::HashSet;
use std::fs::File;
use std::io::Read;
use std::path::Path;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use crate::archive_search::{check_archive_file, search_archive_file};
use crate::excel_search::search_excel_file;
use crate::json_search::{check_json_file, search_json_file};
use crate::types::SearchMatch;
use crate::utils::{
    build_glob_set, decode_bytes, detect_encoding, generate_search_patterns, is_binary,
    match_filename_glob, parse_search_mode,
};
use crate::xml_search::{check_xml_file, search_xml_file};

const MAX_FILE_SIZE: u64 = 1024 * 1024 * 1024; // 1GB 제한
const MAX_DECODE_BUFFER_SIZE: usize = 200 * 1024 * 1024; // 200MB 버퍼 제한

type FileMatches = Vec<(String, Vec<SearchMatch>)>;
type SkippedEntries = Vec<(String, String)>;
type KeywordFileHits = Vec<(String, u64)>;

const REASON_ERR_WALK: &str = "ERR_WALK";
const REASON_ERR_OPEN: &str = "ERR_OPEN";
const REASON_ERR_METADATA: &str = "ERR_METADATA";
const REASON_ERR_MMAP: &str = "ERR_MMAP";
const REASON_ERR_TOO_LARGE: &str = "ERR_TOO_LARGE";
const REASON_ERR_MEMORY_GUARD: &str = "ERR_MEMORY_GUARD";
const REASON_ERR_PANIC: &str = "ERR_PANIC";
const MAX_JSON_SIZE: u64 = 100 * 1024 * 1024; // 100MB JSON DOM 파싱 임계치 (Python과 동기화)
const MATCH_META_BINARY_PREFIX: &str = "__SF_BINARY_MATCH__|";
// Python 쪽에서 문자열 비교에 사용되므로 보관 (Rust 코드 내 직접 호출 없어 경고 억제)
#[allow(dead_code)]
const MATCH_META_LONG_LINE_PREFIX: &str = "__SF_LONG_LINE__|";

const MONITOR_INTERVAL_MS: u64 = 100;

fn encode_skip_reason<T: std::fmt::Display>(code: &str, detail: T) -> String {
    format!("{}|{}", code, detail)
}

#[pyfunction]
#[pyo3(signature = (path, pattern, mode_bits=None, stop_event=None))]
fn search_file(
    path: String,
    pattern: String,
    mode_bits: Option<u32>,
    stop_event: Option<pyo3::PyObject>,  // stop_event 매개변수 추가 (Python 측에서 중단 가능)
) -> PyResult<Vec<SearchMatch>> {
    let (is_json, is_xml, is_archive, is_exact, is_excel, exclude_binary) = parse_search_mode(mode_bits);
    let norm_pattern = crate::utils::normalize_unicode(&pattern);
    let patterns = generate_search_patterns(&norm_pattern, is_xml, is_json, is_archive);
    let ac = AhoCorasickBuilder::new()
        .ascii_case_insensitive(true)
        .match_kind(MatchKind::LeftmostFirst)
        .build(&patterns)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("{}", e)))?;

    // stop_event를 파이썬에서 수신하여 실제 AtomicBool 플래그로 변환
    // 이전: 더미 중단 플래그(항상 false)만 사용 → 단일 파일 검색은 중단 불가
    // 수정: Python의 threading.Event 구현체에서 is_set() 호출하는 모니터 스레드 방식 사용
    let stop_flag = Arc::new(AtomicBool::new(false));
    if let Some(evt) = stop_event {
        let flag_clone = stop_flag.clone();
        // 서브 스레드를 스폰하여 Python stop_event 모니턱링
        std::thread::spawn(move || {
            Python::with_gil(|py| {
                loop {
                    if flag_clone.load(Ordering::Relaxed) { break; }
                    let is_set = evt.call_method0(py, "is_set")
                        .and_then(|r| r.is_truthy(py))
                        .unwrap_or(false);
                    if is_set {
                        flag_clone.store(true, Ordering::Relaxed);
                        break;
                    }
                    std::thread::sleep(std::time::Duration::from_millis(MONITOR_INTERVAL_MS));
                }
            });
        });
    }

    let res = search_file_internal(InternalSearchParams {
        path: Path::new(&path),
        pattern: &pattern,
        ac: &ac,
        is_exact,
                            is_json,
                            is_xml,
                            is_archive,
                            is_excel,
                            exclude_hidden: false,
                            exclude_binary,
                            is_explicit_extension: true, // 단일 파일 검색은 명시적 의도로 간주
                            stop_flag,
                        });

    match res {
        Some(Ok(m)) => Ok(m),
        Some(Err(e)) => Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e)),
        None => Ok(Vec::new()),
    }
}

fn do_search_with_mmap(
    mmap: &[u8],
    pattern: &str,
    ac: &aho_corasick::AhoCorasick,
    is_exact: bool,
    stop_flag: &Arc<AtomicBool>,
) -> Vec<SearchMatch> {
    let mut results = Vec::new();
    let encoding = detect_encoding(mmap);
    let pat_lower = pattern.to_lowercase();

    if encoding == UTF_8 {
        // 거대 UTF-8 파일 지원: 100MB 초과 시 라인 정렬 청크로 처리하여 메모리 스파이크 방지
        if mmap.len() > 100 * 1024 * 1024 {
            let mut line_number = 1usize;
            let mut current_pos = 0usize;
            let target_chunk_size = 10 * 1024 * 1024usize;

            while current_pos < mmap.len() {
                if stop_flag.load(Ordering::Relaxed) { break; }

                // [무결성] 줄 경계에 맞춰 청크 자르기 — UTF-8 문자 절단 방지
                let mut end = (current_pos + target_chunk_size).min(mmap.len());
                if end < mmap.len() {
                    let search_limit = (end + 1024 * 1024).min(mmap.len());
                    if let Some(pos) = mmap[end..search_limit].iter().position(|&b| b == b'\n') {
                        end += pos + 1;
                    }
                }

                let chunk_bytes = &mmap[current_pos..end];
                let chunk_str = String::from_utf8_lossy(chunk_bytes);
                let chunk_data = chunk_str.as_bytes();
                let mut last_line_start = 0usize;

                if is_exact {
                    let mut line_start = 0usize;
                    for (i, &b) in chunk_data.iter().enumerate() {
                        if i % 5000 == 0 && stop_flag.load(Ordering::Relaxed) { return results; }
                        if b == b'\n' || i == chunk_data.len() - 1 {
                            let line_end = if b == b'\n' { i } else { i + 1 };
                            let line_trimmed = String::from_utf8_lossy(&chunk_data[line_start..line_end]);
                            let line_trimmed = line_trimmed.trim();
                            let is_match = if line_trimmed.is_ascii() && pat_lower.is_ascii() {
                                line_trimmed.eq_ignore_ascii_case(&pat_lower)
                            } else {
                                line_trimmed.to_lowercase() == pat_lower
                            };
                            if is_match {
                                results.push(SearchMatch::new(line_number, line_trimmed.to_string(), Some(current_pos + line_start), Some(line_end - line_start)));
                            }
                            line_start = i + 1;
                            line_number += 1;
                        }
                    }
                } else {
                    let mut mat_count = 0usize;
                    for mat in ac.find_iter(chunk_data) {
                        mat_count += 1;
                        if mat_count.is_multiple_of(100) && stop_flag.load(Ordering::Relaxed) { return results; }
                        let m_start = mat.start();
                        // last_line_start를 슬라이스 절대 offset으로 정확히 추적
                        // 이전 버그: last_line_start += i + 1 (i는 슬라이스 상대 인덱스 -> 누적 오차)
                        // 수정: 슬라이스 탐색 후 실제 절대 위치 = last_line_start + i + 1 로 갱신
                        // 수동 루프 대신 고성능 바이트 스캔 사용
                        if m_start > last_line_start {
                            line_number += chunk_data[last_line_start..m_start].iter().filter(|&&b| b == b'\n').count();
                            last_line_start = m_start.saturating_sub(
                                chunk_data[..m_start].iter().rposition(|&b| b == b'\n').map(|p| m_start - p - 1).unwrap_or(m_start)
                            );
                        }
                        // rposition/position 재스캔 제거:
                        // last_line_start가 이미 현재 줄 시작을 추적하므로 그대로 활용
                        let line_end = chunk_data[m_start..].iter().position(|&b| b == b'\n')
                            .map(|p| m_start + p)
                            .unwrap_or(chunk_data.len());
                        results.push(SearchMatch::new(
                            line_number,
                            String::from_utf8_lossy(&chunk_data[last_line_start..line_end]).to_string(),
                            Some(current_pos + m_start),
                            Some(mat.len()),
                        ));
                    }
                    // 청크 나머지 줄번호 계산 (다음 청크를 위한 line_number 동기화)
                    for &b in &chunk_data[last_line_start..] {
                        if b == b'\n' { line_number += 1; }
                    }
                }
                current_pos = end;
            }
            return results;
        }

        // 100MB 이하: 전체 로드 처리
        let raw = String::from_utf8_lossy(mmap);
        let data = raw.as_bytes();
        if is_exact {
            let mut line_number = 1usize;
            let mut last_start = 0usize;
            for (i, &b) in data.iter().enumerate() {
                if i % 10000 == 0 && stop_flag.load(Ordering::Relaxed) { return results; }
                if b == b'\n' || i == data.len() - 1 {
                    let end = if b == b'\n' { i } else { i + 1 };
                    let line = String::from_utf8_lossy(&data[last_start..end]);
                    let line_trimmed = line.trim();
                    let is_match = if line_trimmed.is_ascii() && pat_lower.is_ascii() {
                        line_trimmed.eq_ignore_ascii_case(&pat_lower)
                    } else {
                        line_trimmed.to_lowercase() == pat_lower
                    };
                    if is_match {
                        results.push(SearchMatch::new(line_number, line_trimmed.to_string(), Some(last_start), Some(end - last_start)));
                    }
                    last_start = i + 1;
                    line_number += 1;
                }
            }
        } else {
            let mut line_number = 1usize;
            let mut last_line_idx = 0usize;
            for (mat_count, mat) in ac.find_iter(data).enumerate() {
                if mat_count % 500 == 0 && stop_flag.load(Ordering::Relaxed) { return results; }
                let match_start = mat.start();
                
                // [안전성 패치] 위험한 수동 슬라이싱 및 증분 계산 대신, 
                // 이전 위치부터 현재 매칭까지의 뉴라인 개수만 정직하게 합산
                if match_start > last_line_idx {
                    line_number += data[last_line_idx..match_start].iter().filter(|&&b| b == b'\n').count();
                }

                if results.last().is_none_or(|m: &SearchMatch| m.line != line_number) {
                    // 라인 경계 계산(rposition/position)을 실제로 결과가 필요할 때만 수행
                    let line_start = data[..match_start].iter().rposition(|&b| b == b'\n').map(|p| p + 1).unwrap_or(0);
                    let line_end = data[match_start..].iter().position(|&b| b == b'\n').map(|p| match_start + p).unwrap_or(data.len());
                    
                    results.push(SearchMatch::new(
                        line_number, 
                        String::from_utf8_lossy(&data[line_start..line_end]).trim().to_string(), 
                        Some(match_start), 
                        Some(mat.len())
                    ));
                }
                last_line_idx = match_start;
            }
        }
    } else {
        // 비 UTF-8 인코딩 (EUC-KR 등)
        if mmap.len() > 50 * 1024 * 1024 {
            let mut decoder = encoding.new_decoder();
            let mut line_number = 1usize;
            let mut current_pos = 0usize;
            let chunk_size = 10 * 1024 * 1024usize;
            let mut decoded_buf = String::with_capacity(chunk_size * 2);
            let mut last_line_idx = 0usize;

            while current_pos < mmap.len() {
                if stop_flag.load(Ordering::Relaxed) { break; }
                let end = (current_pos + chunk_size).min(mmap.len());
                let is_last = end == mmap.len();
                let mut tmp = String::with_capacity((end - current_pos) * 2);
                let _ = decoder.decode_to_string(&mmap[current_pos..end], &mut tmp, is_last);
                if decoded_buf.len() + tmp.len() > MAX_DECODE_BUFFER_SIZE {
                    decoded_buf.clear(); last_line_idx = 0;
                }
                decoded_buf.push_str(&tmp);
                if let Some(last_nl) = decoded_buf.rfind('\n') {
                    let search_range = &decoded_buf[..last_nl];
                    if is_exact {
                        for line in search_range.lines() {
                            if line.eq_ignore_ascii_case(&pat_lower) {
                                results.push(SearchMatch::new(line_number, line.to_string(), None, None));
                            }
                            line_number += 1;
                        }
                    } else {
                        for mat in ac.find_iter(search_range) {
                            let m = mat.start();
                            // byte index out of bounds 방지 및 고성능 바이트 스캔 (as_bytes 사용)
                            if m > last_line_idx {
                                line_number += &search_range.as_bytes()[last_line_idx..m].iter().filter(|&&b| b == b'\n').count();
                            }
                            if results.last().is_none_or(|r: &SearchMatch| r.line != line_number) {
                                let ls = search_range[..m].rfind('\n').map(|i| i + 1).unwrap_or(0);
                                let le = search_range[m..].find('\n').map(|i| m + i).unwrap_or(search_range.len());
                                results.push(SearchMatch::new(line_number, search_range[ls..le].trim().to_string(), None, None));
                            }
                            last_line_idx = m;
                        }
                    }
                    decoded_buf.drain(..last_nl + 1); last_line_idx = 0;
                }
                current_pos = end;
            }
            return results;
        }

        let content = decode_bytes(mmap, encoding);
        let mut line_number = 1usize;
        let mut last_line_idx = 0usize;
        if is_exact {
            for (i, line) in content.lines().enumerate() {
                if line.eq_ignore_ascii_case(&pat_lower) {
                    results.push(SearchMatch::new(i + 1, line.to_string(), None, None));
                }
            }
        } else {
            for mat in ac.find_iter(content.as_str()) {
                let match_start = mat.start();
                // Panic out of bounds 방지 및 고성능 바이트 스캔
                if match_start > last_line_idx {
                    line_number += &content.as_bytes()[last_line_idx..match_start].iter().filter(|&&b| b == b'\n').count();
                }
                
                if results.last().is_none_or(|r: &SearchMatch| r.line != line_number) {
                    let le = content[match_start..].find('\n').map(|i| match_start + i).unwrap_or(content.len());
                    let ls = content[..match_start].rfind('\n').map(|i| i + 1).unwrap_or(0);
                    results.push(SearchMatch::new(line_number, content[ls..le].trim().to_string(), None, None));
                }
                last_line_idx = match_start;
            }
        }
    }
    results
}

struct InternalSearchParams<'a> {
    path: &'a Path,
    pattern: &'a str,
    ac: &'a aho_corasick::AhoCorasick,
    is_exact: bool,
    is_json: bool,
    is_xml: bool,
    is_archive: bool,
    is_excel: bool,
    exclude_hidden: bool,
    exclude_binary: bool,
    is_explicit_extension: bool,
    stop_flag: Arc<AtomicBool>,
}

fn search_file_internal(params: InternalSearchParams) -> Option<Result<Vec<SearchMatch>, String>> {
    let InternalSearchParams {
        path,
        pattern,
        ac,
        is_exact,
        is_json,
        is_xml,
        is_archive,
        is_excel,
        exclude_hidden,
        exclude_binary,
        is_explicit_extension,
        stop_flag,
    } = params;
    let ext = path
        .extension()
        .and_then(|s| s.to_str())
        .unwrap_or("")
        .to_lowercase();

    if is_excel || ["xlsx", "xlsb", "xls", "xlsm"].contains(&ext.as_str()) {
        let res = search_excel_file(path, pattern, ac, is_exact, stop_flag);
        return if res.is_empty() { None } else { Some(Ok(res)) };
    }

    let file = match File::open(path) {
        Ok(f) => f,
        Err(e) => return Some(Err(encode_skip_reason(REASON_ERR_OPEN, e))),
    };

    #[cfg(windows)]
    let _ = file.lock_shared();

    let metadata = match file.metadata() {
        Ok(m) => m,
        Err(e) => return Some(Err(encode_skip_reason(REASON_ERR_METADATA, e))),
    };

    if exclude_hidden {
        #[cfg(windows)]
        {
            use std::os::windows::fs::MetadataExt;
            if (metadata.file_attributes() & 0x02) != 0 {
                return None;
            }
        }
    }

    let f_len = metadata.len();
    if f_len == 0 {
        return None;
    }
    if f_len > MAX_FILE_SIZE {
        return Some(Err(encode_skip_reason(
            REASON_ERR_TOO_LARGE,
            format!("{} bytes", f_len),
        )));
    }

    let mut fallback_buf = Vec::new();
    let mmap_holder;
    let (mmap_content, is_fallback) = match unsafe { Mmap::map(&file) } {
        Ok(m) => {
            mmap_holder = Some(m);
            (mmap_holder.as_ref().unwrap().as_ref(), false)
        }
        Err(e) => {
            if f_len > MAX_DECODE_BUFFER_SIZE as u64 {
                return Some(Err(encode_skip_reason(REASON_ERR_MMAP, format!("Mmap failed: {}. File too large for Read fallback.", e))));
            }
            let mut f = file;
            if let Err(re) = f.read_to_end(&mut fallback_buf) {
                return Some(Err(encode_skip_reason(REASON_ERR_MMAP, format!("Mmap failed: {}. Read fallback failed: {}", e, re))));
            }
            eprintln!("[StringFinder] FALLBACK_TO_READ: {:?} (Reason: {})", path, e);
            (fallback_buf.as_slice(), true)
        }
    };

    let ext_lower = format!(".{}", ext);

    let res = if is_archive && (ext_lower == ".archive" || ext_lower == ".sf_archive") {
        search_archive_file(mmap_content, pattern, ac, is_exact, stop_flag.clone())
    } else if is_json && ext_lower == ".json" {
        if mmap_content.len() as u64 > MAX_JSON_SIZE {
            vec![SearchMatch::new(
                1,
                encode_skip_reason(REASON_ERR_MEMORY_GUARD, format!("Size {} bytes exceeds limit", mmap_content.len())),
                None, None,
            )]
        } else {
            search_json_file(mmap_content, pattern, ac, is_exact, stop_flag.clone())
        }
    } else if is_xml && ext_lower == ".xml" {
        search_xml_file(mmap_content, pattern, ac, is_exact, stop_flag.clone())
    } else {
        let is_bin = is_binary(mmap_content);
        if exclude_binary && !is_explicit_extension && is_bin {
            return None;
        }
        if is_bin {
            let count = ac.find_iter(mmap_content).count();
            if count > 0 {
                vec![SearchMatch::new(1, format!("{}{}", MATCH_META_BINARY_PREFIX, count), None, Some(count))]
            } else {
                Vec::new()
            }
        } else {
            do_search_with_mmap(mmap_content, pattern, ac, is_exact, &stop_flag)
        }
    };

    if is_fallback && !res.is_empty() {
        // 폴백 성공 로그는 이미 eprintln!으로 처리됨
    }

    if res.is_empty() {
        None
    } else {
        Some(Ok(res))
    }
}

#[pyfunction]
#[pyo3(signature = (paths, pattern, extensions=None, mode_bits=None, filename_filter=None, exclude_hidden=false, stop_event=None, progress_callback=None))]
#[allow(clippy::too_many_arguments)]
fn search_dir(
    py: Python<'_>,
    paths: Vec<String>,
    pattern: String,
    extensions: Option<Vec<String>>,
    mode_bits: Option<u32>,
    filename_filter: Option<Vec<String>>,
    exclude_hidden: bool,
    stop_event: Option<PyObject>,
    progress_callback: Option<PyObject>,
) -> PyResult<(FileMatches, SkippedEntries)> {
    let stop_flag = Arc::new(AtomicBool::new(false));
    let progress_counter = Arc::new(AtomicU64::new(0));

    let stop_flag_mon = stop_flag.clone();
    let progress_counter_mon = progress_counter.clone();
    let stop_event_mon = stop_event.as_ref().map(|obj| obj.clone_ref(py));
    let progress_callback_mon = progress_callback.as_ref().map(|obj| obj.clone_ref(py));

    if stop_event.is_some() || progress_callback.is_some() {
        std::thread::spawn(move || {
            while !stop_flag_mon.load(Ordering::Relaxed) {
                let is_stopped = Python::with_gil(|py| {
                    if let Some(obj) = &stop_event_mon {
                        if let Ok(res) = obj.bind(py).call_method0("is_set") {
                            if let Ok(true) = res.extract::<bool>() {
                                return true;
                            }
                        }
                    }
                    if let Some(cb) = &progress_callback_mon {
                        let count = progress_counter_mon.load(Ordering::Relaxed);
                        // 진행률 콜백 간격 상향: 100 -> 1000 (GIL 부하 감소)
                        if count.is_multiple_of(1000) || stop_flag_mon.load(Ordering::Relaxed) {
                            let _ = cb.bind(py).call1((count,));
                        }
                    }
                    false
                });
                if is_stopped {
                    stop_flag_mon.store(true, Ordering::SeqCst);
                    break;
                }
                std::thread::sleep(std::time::Duration::from_millis(MONITOR_INTERVAL_MS));
            }
        });
    }

    py.allow_threads(|| {
        let (is_json, is_xml, is_archive, is_exact, is_excel, exclude_binary) = parse_search_mode(mode_bits);
        let ac = AhoCorasickBuilder::new()
            .ascii_case_insensitive(true)
            .match_kind(MatchKind::LeftmostFirst)
            .build([pattern.as_str()])
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("{}", e)))?;

        let is_explicit_extension = extensions.is_some() || filename_filter.is_some();

        let exts = extensions.map(|v| {
            v.iter()
                .map(|s| s.trim_start_matches('.').to_lowercase())
                .collect::<HashSet<_>>()
        });
        let glob_set = build_glob_set(&filename_filter.unwrap_or_default());

        use std::sync::{Arc, Mutex};
        let matches = Arc::new(Mutex::new(Vec::new()));
        let skipped = Arc::new(Mutex::new(Vec::new()));

        if paths.is_empty() {
            stop_flag.store(true, Ordering::SeqCst);
            return Ok((Vec::new(), Vec::new()));
        }

        let mut builder = WalkBuilder::new(&paths[0]);
        builder
            .hidden(exclude_hidden)
            .ignore(false)
            .git_global(false)
            .git_ignore(false)
            .git_exclude(false);
        for path in paths.iter().skip(1) {
            builder.add(path);
        }

        let walker = builder
            .threads(rayon::current_num_threads())
            .build_parallel();

        walker.run(|| {
            let matches_ptr = matches.clone();
            let skipped_ptr = skipped.clone();
            let pattern_ptr = pattern.clone();
            let ac_ptr = ac.clone();
            let stop_flag_ptr = stop_flag.clone();
            let progress_counter_ptr = progress_counter.clone();
            let exts_ptr = exts.clone();
            let glob_set_ptr = glob_set.clone();

            #[allow(clippy::type_complexity)]
            struct ThreadCollector {
                local_matches: Vec<(String, Vec<SearchMatch>)>,
                local_skipped: Vec<(String, String)>,
                global_matches: crate::types::SearchResultCollector,
                global_skipped: crate::types::SkippedResultCollector,
            }

            impl Drop for ThreadCollector {
                fn drop(&mut self) {
                    if !self.local_matches.is_empty() {
                        if let Ok(mut g) = self.global_matches.lock() {
                            g.extend(self.local_matches.drain(..));
                        }
                    }
                    if !self.local_skipped.is_empty() {
                        if let Ok(mut g) = self.global_skipped.lock() {
                            g.extend(self.local_skipped.drain(..));
                        }
                    }
                }
            }

            let mut collector = ThreadCollector {
                local_matches: Vec::new(),
                local_skipped: Vec::new(),
                global_matches: matches_ptr,
                global_skipped: skipped_ptr,
            };

            let is_explicit_ptr = is_explicit_extension;

            Box::new(move |result| {
                if stop_flag_ptr.load(Ordering::Relaxed) {
                    return ignore::WalkState::Quit;
                }

                match result {
                    Ok(entry) => {
                        if entry.file_type().is_some_and(|ft| ft.is_file()) {
                            let path = entry.path();
                            let path_str = path.to_string_lossy().to_string();

                            if let Some(name) = path.file_name().and_then(|s| s.to_str()) {
                                if !match_filename_glob(name, &glob_set_ptr) {
                                    return ignore::WalkState::Continue;
                                }
                            }

                            if let Some(ref valid_exts) = exts_ptr {
                                if let Some(ext) = path.extension().and_then(|s| s.to_str()) {
                                    // 성능 최적화: valid_exts는 이미 set_dir 루프 외부에서 to_lowercase() 처리됨
                                    // 매칭 시에도 Case Insensitive 집합 조회이므로 별도 변환 최소화
                                    if !valid_exts.contains(ext) {
                                        let ext_lower = ext.to_lowercase();
                                        if !valid_exts.contains(&ext_lower) {
                                            return ignore::WalkState::Continue;
                                        }
                                    }
                                } else {
                                    return ignore::WalkState::Continue;
                                }
                            }

                            let sf_panic = stop_flag_ptr.clone();
                            let pt_panic = pattern_ptr.clone();
                            let ac_panic = ac_ptr.clone();

                            let results_panic = std::panic::catch_unwind(move || {
                                search_file_internal(InternalSearchParams {
                                    path,
                                    pattern: &pt_panic,
                                    ac: &ac_panic,
                                    is_exact,
                                    is_json,
                                    is_xml,
                                    is_archive,
                                    is_excel,
                                    exclude_hidden,
                                    exclude_binary,
                                    is_explicit_extension: is_explicit_ptr,
                                    stop_flag: sf_panic,
                                })
                            });

                            match results_panic {
                                Ok(res) => {
                                    if let Some(r) = res {
                                        match r {
                                            Ok(m) => collector.local_matches.push((path_str, m)),
                                            Err(e) => collector.local_skipped.push((path_str, e)),
                                        }
                                    }
                                }
                                Err(_) => {
                                    collector.local_skipped.push((
                                        path_str,
                                        encode_skip_reason(REASON_ERR_PANIC, "INTERNAL_PANIC"),
                                    ));
                                }
                            }
                            progress_counter_ptr.fetch_add(1, Ordering::Relaxed);
                        }
                    }
                    Err(e) => {
                        collector.local_skipped.push((
                            "Unknown".to_string(),
                            encode_skip_reason(REASON_ERR_WALK, e),
                        ));
                    }
                }
                ignore::WalkState::Continue
            })
        });

        stop_flag.store(true, Ordering::SeqCst);

        let final_results = matches.lock()
            .map(|g| g.clone())
            .unwrap_or_else(|_| Vec::new());
        let final_skipped = skipped.lock()
            .map(|g| g.clone())
            .unwrap_or_else(|_| Vec::new());
        Ok((final_results, final_skipped))
    })
}

#[pyfunction]
#[pyo3(signature = (file_list, search_string, mode_bits=None, exclude_hidden=false, stop_event=None, progress_callback=None, **_kwargs))]
#[allow(clippy::too_many_arguments)]
fn search_files_list(
    py: Python<'_>,
    file_list: Vec<String>,
    search_string: String,
    mode_bits: Option<u32>,
    exclude_hidden: bool,
    stop_event: Option<PyObject>,
    progress_callback: Option<PyObject>,
    _kwargs: Option<PyObject>,
) -> PyResult<(FileMatches, SkippedEntries)> {
    let stop_flag = Arc::new(AtomicBool::new(false));
    let progress_counter = Arc::new(AtomicU64::new(0));

    let stop_flag_mon = stop_flag.clone();
    let progress_counter_mon = progress_counter.clone();
    let stop_event_mon = stop_event.as_ref().map(|obj| obj.clone_ref(py));
    let progress_callback_mon = progress_callback.as_ref().map(|obj| obj.clone_ref(py));

    if stop_event.is_some() || progress_callback.is_some() {
        std::thread::spawn(move || {
            while !stop_flag_mon.load(Ordering::Relaxed) {
                let is_stopped = Python::with_gil(|py| {
                    if let Some(obj) = &stop_event_mon {
                        if let Ok(res) = obj.bind(py).call_method0("is_set") {
                            if let Ok(true) = res.extract::<bool>() {
                                return true;
                            }
                        }
                    }
                    if let Some(cb) = &progress_callback_mon {
                        // 진행률 콜백 간격 상향: 100 -> 1000
                        let count = progress_counter_mon.load(Ordering::Relaxed);
                        if count.is_multiple_of(1000) || stop_flag_mon.load(Ordering::Relaxed) {
                            let _ = cb.bind(py).call1((count,));
                        }
                    }
                    false
                });
                if is_stopped {
                    stop_flag_mon.store(true, Ordering::SeqCst);
                    break;
                }
                // 모니터링 주기 정책값(상수) 사용
                std::thread::sleep(std::time::Duration::from_millis(MONITOR_INTERVAL_MS));
            }
        });
    }

    py.allow_threads(|| {
        let (is_json, is_xml, is_archive, is_exact, is_excel, _exclude_binary) = parse_search_mode(mode_bits);
        let ac = AhoCorasickBuilder::new()
            .ascii_case_insensitive(true)
            .match_kind(MatchKind::LeftmostFirst)
            .build([search_string.as_str()])
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("{}", e)))?;

        let matches = Arc::new(Mutex::new(Vec::new()));
        let skipped = Arc::new(Mutex::new(Vec::new()));

        // 매 호출마다 새 rayon 풀 생성 대신 rayon 글로벌 기본 풀 직접 사용
        // 이전: ThreadPoolBuilder::new().build() -> 관리 스레드 + 실제 작업 스레드 이중 생성 오버헤드
        // 수정: rayon 글로벌 풀은 자동으로 thread::사용 가능한 병렬성()으로 관리됨
            file_list.into_par_iter().for_each(|path_str| {
                let sf_panic = stop_flag.clone();
                let ac_panic = ac.clone();
                let ss_panic = search_string.clone();
                let ps_panic = path_str.clone();
                let matches_clone = matches.clone();
                let skipped_clone = skipped.clone();

                let results_panic = std::panic::catch_unwind(move || {
                    if sf_panic.load(Ordering::Relaxed) {
                        return Ok::<Option<()>, String>(None);
                    }

                    #[allow(clippy::type_complexity)]
                    struct ThreadCollector {
                        local_matches: Vec<(String, Vec<SearchMatch>)>,
                        local_skipped: Vec<(String, String)>,
                        global_matches: Arc<Mutex<Vec<(String, Vec<SearchMatch>)>>>,
                        global_skipped: Arc<Mutex<Vec<(String, String)>>>,
                    }

                    impl Drop for ThreadCollector {
                        fn drop(&mut self) {
                            if !self.local_matches.is_empty() {
                                if let Ok(mut g) = self.global_matches.lock() {
                                    g.extend(self.local_matches.drain(..));
                                }
                            }
                            if !self.local_skipped.is_empty() {
                                if let Ok(mut g) = self.global_skipped.lock() {
                                    g.extend(self.local_skipped.drain(..));
                                }
                            }
                        }
                    }

                    let mut collector = ThreadCollector {
                        local_matches: Vec::new(),
                        local_skipped: Vec::new(),
                        global_matches: matches_clone,
                        global_skipped: skipped_clone,
                    };

                    let path = Path::new(&ps_panic);
                    match File::open(path) {
                        Ok(file) => {
                            let metadata = match file.metadata() {
                                Ok(m) => m,
                                Err(e) => {
                                    collector.local_skipped.push((
                                        ps_panic.clone(),
                                        encode_skip_reason(REASON_ERR_METADATA, e),
                                    ));
                                    return Ok::<Option<()>, String>(None);
                                }
                            };

                            if exclude_hidden {
                                #[cfg(windows)]
                                {
                                    use std::os::windows::fs::MetadataExt;
                                    if (metadata.file_attributes() & 0x02) != 0 {
                                        return Ok::<Option<()>, String>(None);
                                    }
                                }
                            }

                            if metadata.len() == 0 {
                                return Ok::<Option<()>, String>(None);
                            }

                            if metadata.len() > MAX_FILE_SIZE {
                                collector.local_skipped.push((
                                    ps_panic.clone(),
                                    encode_skip_reason(
                                        REASON_ERR_TOO_LARGE,
                                        format!("{} bytes", metadata.len()),
                                    ),
                                ));
                                return Ok::<Option<()>, String>(None);
                            }

                            let mut fallback_buf = Vec::new();
                            let mmap_holder;
                            let (mmap_content, _is_fallback) = match unsafe { Mmap::map(&file) } {
                                Ok(m) => {
                                    mmap_holder = Some(m);
                                    (mmap_holder.as_ref().unwrap().as_ref(), false)
                                }
                                Err(e) => {
                                    if metadata.len() > MAX_DECODE_BUFFER_SIZE as u64 {
                                        collector.local_skipped.push((ps_panic.clone(), encode_skip_reason(REASON_ERR_MMAP, format!("Mmap failed: {}. Tool large for Read fallback.", e))));
                                        return Ok::<Option<()>, String>(None);
                                    }
                                    let mut f = file;
                                    if let Err(re) = f.read_to_end(&mut fallback_buf) {
                                        collector.local_skipped.push((ps_panic.clone(), encode_skip_reason(REASON_ERR_MMAP, format!("Mmap failed: {}. Read fallback failed: {}", e, re))));
                                        return Ok::<Option<()>, String>(None);
                                    }
                                    eprintln!("[StringFinder] FALLBACK_TO_READ: {:?} (Reason: {})", ps_panic, e);
                                    (fallback_buf.as_slice(), true)
                                }
                            };

                            let ext = path.extension().and_then(|s| s.to_str()).unwrap_or("").to_lowercase();
                            let ext_lower = format!(".{}", ext);

                            // 바이너리 판별 결과 캐싱
                            let is_binary_file = is_binary(mmap_content);
                            let res = if is_excel || ["xlsx", "xlsb", "xls", "xlsm"].contains(&ext.as_str()) {
                                search_excel_file(path, &ss_panic, &ac_panic, is_exact, sf_panic.clone())
                            } else if is_archive && (ext_lower == ".archive" || ext_lower == ".sf_archive") {
                                search_archive_file(mmap_content, &ss_panic, &ac_panic, is_exact, sf_panic.clone())
                            } else if is_json && ext_lower == ".json" {
                                if mmap_content.len() as u64 > MAX_JSON_SIZE {
                                    vec![SearchMatch::new(1, encode_skip_reason(REASON_ERR_MEMORY_GUARD, format!("Size {} bytes exceeds limit", mmap_content.len())), None, None)]
                                } else {
                                    search_json_file(mmap_content, &ss_panic, &ac_panic, is_exact, sf_panic.clone())
                                }
                            } else if is_xml && ext_lower == ".xml" {
                                search_xml_file(mmap_content, &ss_panic, &ac_panic, is_exact, sf_panic.clone())
                            } else if is_binary_file {
                                let count = ac_panic.find_iter(mmap_content).count();
                                if count > 0 {
                                    vec![SearchMatch::new(1, format!("{}{}", MATCH_META_BINARY_PREFIX, count), None, Some(count))]
                                } else { Vec::new() }
                            } else {
                                do_search_with_mmap(mmap_content, &ss_panic, &ac_panic, is_exact, &sf_panic)
                            };

                            if !res.is_empty() {
                                collector.local_matches.push((ps_panic.clone(), res));
                            }
                            Ok::<Option<()>, String>(Some(()))
                        }
                        Err(e) => {
                            collector.local_skipped.push((ps_panic.clone(), encode_skip_reason(REASON_ERR_OPEN, e)));
                            Ok::<Option<()>, String>(None)
                        }
                    }
                });

                match results_panic {
                    Ok(Ok(_)) => {}
                    Ok(Err(_)) => {} // Already handled via collector or return
                    Err(_) => {
                        if let Ok(mut g) = skipped.lock() {
                            g.push((path_str, encode_skip_reason(REASON_ERR_PANIC, "INTERNAL_PANIC")));
                        }
                    }
                }
                progress_counter.fetch_add(1, Ordering::Relaxed);
            });

        stop_flag.store(true, Ordering::SeqCst);
        let final_results = matches.lock()
            .map(|g| g.clone())
            .unwrap_or_default();
        let final_skipped = skipped.lock()
            .map(|g| g.clone())
            .unwrap_or_default();
        Ok((final_results, final_skipped))
    })
}

// 미래 fast-path 검색 최적화를 위해 보관 중 (clippy 경고 개별 억제)
#[allow(dead_code)]
fn simple_check_text_mmap(
    mmap: &[u8],
    pattern: &str,
    ac: &aho_corasick::AhoCorasick,
    is_exact: bool,
    _ext: &str,
    stop_flag: Arc<AtomicBool>,
) -> bool {
    let encoding = detect_encoding(mmap);
    let pat_lower = pattern.to_lowercase();
    if encoding == UTF_8 {
        let mmap_stripped = mmap; // 주석 제거 기능 삭제

        if is_exact {
            let mut last_start = 0;
            for (i, &b) in mmap_stripped.iter().enumerate() {
                if i % 1000 == 0 && stop_flag.load(Ordering::Relaxed) {
                    return false;
                }
                if b == b'\n' || i == mmap_stripped.len() - 1 {
                    let end = if b == b'\n' { i } else { i + 1 };
                    let line_bytes = &mmap_stripped[last_start..end];
                    let line_str = String::from_utf8_lossy(line_bytes);
                    let line_trimmed = line_str.trim();
                    if line_trimmed.eq_ignore_ascii_case(&pat_lower) {
                        return true;
                    }
                    last_start = i + 1;
                }
            }
            false
        } else {
            ac.find(mmap_stripped).is_some()
        }
    } else {
        let content_raw = decode_bytes(mmap, encoding);
        let content = &content_raw;
        if is_exact {
            content
                .lines()
                .any(|l: &str| l.trim().eq_ignore_ascii_case(&pat_lower))
        } else {
            ac.find(content.as_str()).is_some()
        }
    }
}

#[pyfunction]
#[pyo3(signature = (paths, keyword, extensions=None, mode_bits=None, filename_filter=None, exclude_hidden=false, stop_event=None))]
#[allow(clippy::too_many_arguments)]
fn find_files_with_keyword(
    py: Python<'_>,
    paths: Vec<String>,
    keyword: String,
    extensions: Option<Vec<String>>,
    mode_bits: Option<u32>,
    filename_filter: Option<Vec<String>>,
    exclude_hidden: bool,
    stop_event: Option<PyObject>,
) -> PyResult<(KeywordFileHits, SkippedEntries)> {
    let stop_flag = Arc::new(AtomicBool::new(false));
    let is_done = Arc::new(AtomicBool::new(false));

    let stop_flag_mon = stop_flag.clone();
    let is_done_mon = is_done.clone();
    let stop_event_mon = stop_event.as_ref().map(|obj| obj.clone_ref(py));

    if stop_event.is_some() {
        std::thread::spawn(move || {
            while !is_done_mon.load(Ordering::Relaxed) && !stop_flag_mon.load(Ordering::Relaxed) {
                let is_stopped = Python::with_gil(|py| {
                    if let Some(obj) = &stop_event_mon {
                        if let Ok(res) = obj.bind(py).call_method0("is_set") {
                            if let Ok(true) = res.extract::<bool>() {
                                return true;
                            }
                        }
                    }
                    false
                });
                if is_stopped {
                    stop_flag_mon.store(true, Ordering::SeqCst);
                    break;
                }
                std::thread::sleep(std::time::Duration::from_millis(MONITOR_INTERVAL_MS));
            }
        });
    }

    py.allow_threads(|| {
        let (is_json, is_xml, is_archive, is_exact, _is_excel, exclude_binary) = parse_search_mode(mode_bits);
        let is_explicit_extension = extensions.is_some() || filename_filter.is_some();

        let norm_keyword = crate::utils::normalize_unicode(&keyword);
        let patterns = generate_search_patterns(&norm_keyword, is_xml, is_json, is_archive);
        let ac = AhoCorasickBuilder::new()
            .ascii_case_insensitive(true)
            .match_kind(MatchKind::Standard) // 빠른 불리언 매치 체크
            .build(&patterns)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("{}", e)))?;

        let exts = extensions.map(|v| {
            v.iter()
                .map(|s| s.trim_start_matches('.').to_lowercase())
                .collect::<HashSet<_>>()
        });
        let glob_set = build_glob_set(&filename_filter.unwrap_or_default());

        let results = Arc::new(Mutex::new(Vec::new()));
        let skipped = Arc::new(Mutex::new(Vec::new()));

        for root_path in &paths {
            if stop_flag.load(Ordering::Relaxed) {
                break;
            }
            let mut builder = WalkBuilder::new(root_path);
            builder
                .hidden(exclude_hidden)
                .ignore(false)
                .git_global(false)
                .git_ignore(false)
                .git_exclude(false);
            let walker = builder
                .threads(rayon::current_num_threads())
                .build_parallel();

            let results_ref = results.clone();
            let skipped_ref = skipped.clone();
            let ac = ac.clone();
            let exts = exts.clone();
            let glob_set = glob_set.clone();
            let keyword = keyword.clone();
            let root_path = root_path.clone();
            let stop_flag = stop_flag.clone();

            walker.run(|| {
                let r_path = root_path.clone();
                let kw = keyword.clone();
                let a_c = ac.clone();
                let s_f = stop_flag.clone();
                let ex = exts.clone();
                let gs = glob_set.clone();
                let g_results = results_ref.clone();
                let g_skipped = skipped_ref.clone();

                struct ThreadContext {
                    local_results: Vec<(String, u64)>,
                    local_skipped: Vec<(String, String)>,
                    global_results: Arc<Mutex<KeywordFileHits>>,
                    global_skipped: Arc<Mutex<SkippedEntries>>,
                }

                impl Drop for ThreadContext {
                    fn drop(&mut self) {
                        if !self.local_results.is_empty() {
                            if let Ok(mut g) = self.global_results.lock() {
                                g.extend(self.local_results.drain(..));
                            }
                        }
                        if !self.local_skipped.is_empty() {
                            if let Ok(mut g) = self.global_skipped.lock() {
                                g.extend(self.local_skipped.drain(..));
                            }
                        }
                    }
                }

                let mut ctx = ThreadContext {
                    local_results: Vec::new(),
                    local_skipped: Vec::new(),
                    global_results: g_results,
                    global_skipped: g_skipped,
                };

                Box::new(move |entry| {
                    if s_f.load(Ordering::Relaxed) {
                        return ignore::WalkState::Quit;
                    }

                    let e = match entry {
                        Ok(e) => e,
                        Err(err) => {
                            ctx.local_skipped.push((
                                r_path.clone(),
                                encode_skip_reason(REASON_ERR_WALK, err),
                            ));
                            return ignore::WalkState::Continue;
                        }
                    };

                    if !e.file_type().is_some_and(|ft| ft.is_file()) {
                        return ignore::WalkState::Continue;
                    }

                    let path = e.path();
                    let path_str = path.to_string_lossy().to_string();

                    if let Some(name) = path.file_name().and_then(|s| s.to_str()) {
                        if !match_filename_glob(name, &gs) {
                            return ignore::WalkState::Continue;
                        }
                    } else {
                        return ignore::WalkState::Continue;
                    }

                    if let Some(ref valid_exts) = ex {
                        if let Some(ext) = path.extension().and_then(|s| s.to_str()) {
                            if !valid_exts.contains(ext) {
                                let ext_lower = ext.to_lowercase();
                                if !valid_exts.contains(&ext_lower) {
                                    return ignore::WalkState::Continue;
                                }
                            }
                        } else {
                            return ignore::WalkState::Continue;
                        }
                    }

                    match File::open(path) {
                        Ok(file) => {
                            #[cfg(windows)]
                            let _ = file.lock_shared();

                            let metadata = match file.metadata() {
                                Ok(m) => m,
                                Err(err) => {
                                    ctx.local_skipped.push((
                                        path_str,
                                        encode_skip_reason(REASON_ERR_METADATA, err),
                                    ));
                                    return ignore::WalkState::Continue;
                                }
                            };
                            let f_size = metadata.len();
                            if f_size == 0 {
                                return ignore::WalkState::Continue;
                            }
                            if f_size > MAX_FILE_SIZE {
                                ctx.local_skipped.push((
                                    path_str,
                                    encode_skip_reason(
                                        REASON_ERR_TOO_LARGE,
                                        format!("{} bytes", f_size),
                                    ),
                                ));
                                return ignore::WalkState::Continue;
                            }


                            match unsafe { Mmap::map(&file) } {
                                Ok(mmap) => {
                                    let path_buf = path.to_path_buf();
                                    let kw_inner = kw.clone();
                                    let ac_inner = a_c.clone();
                                    let sf_inner = s_f.clone();

                                    let sf_panic = sf_inner.clone();
                                    let ac_panic = ac_inner.clone();
                                    let kw_panic = kw_inner.clone();

                                    let results_panic = std::panic::catch_unwind(move || {
                                        if let Some(ext) =
                                            path_buf.extension().and_then(|s| s.to_str())
                                        {
                                            let ext_lower = ext.to_lowercase();
                                            if ["xlsx", "xlsb", "xls", "xlsm"]
                                                .contains(&ext_lower.as_str())
                                            {
                                                !search_excel_file(
                                                    &path_buf,
                                                    &kw_panic,
                                                    &ac_panic,
                                                    is_exact,
                                                    sf_panic,
                                                )
                                                .is_empty()
                                            } else if is_json && ext_lower == "json" {
                                                if f_size > MAX_JSON_SIZE {
                                                    if ac_panic.find(&mmap).is_none() {
                                                        false
                                                    } else {
                                                        check_json_file(
                                                            &mmap,
                                                            &kw_panic,
                                                            &ac_panic,
                                                            is_exact,
                                                            sf_panic,
                                                        )
                                                    }
                                                } else {
                                                    check_json_file(
                                                        &mmap,
                                                        &kw_panic,
                                                        &ac_panic,
                                                        is_exact,
                                                        sf_panic,
                                                    )
                                                }
                                            } else if is_xml && ext_lower == "xml" {
                                                if ac_panic.find(&mmap).is_none() {
                                                    false
                                                } else {
                                                    check_xml_file(
                                                        &mmap,
                                                        &kw_panic,
                                                        &ac_panic,
                                                        is_exact,
                                                        sf_panic,
                                                    )
                                                }
                                            } else if is_archive && ext_lower == "archive" {
                                                if f_size > MAX_JSON_SIZE {
                                                    if ac_panic.find(&mmap).is_none() {
                                                        false
                                                    } else {
                                                        check_archive_file(
                                                            &mmap,
                                                            &kw_panic,
                                                            &ac_panic,
                                                            is_exact,
                                                            sf_panic,
                                                        )
                                                    }
                                                } else {
                                                    check_archive_file(
                                                        &mmap,
                                                        &kw_panic,
                                                        &ac_panic,
                                                        is_exact,
                                                        sf_panic,
                                                    )
                                                }
                                            } else {
                                                // 일반 텍스트 검색 path with binary filter
                                                if exclude_binary && !is_explicit_extension && is_binary(&mmap) {
                                                    return false;
                                                }
                                                // 바이트 레벨 조기 탈출: 매치 가능성이 없는 경우 디코딩 스킵
                                                if ac_panic.find(&mmap).is_none() {
                                                    return false;
                                                }
                                                simple_check_text_mmap(
                                                    &mmap,
                                                    &kw_panic,
                                                    &ac_panic,
                                                    is_exact,
                                                    &ext_lower,
                                                    sf_panic,
                                                )
                                            }
                                        } else {
                                            // 일반 텍스트 검색 for files without extension
                                            if exclude_binary && !is_explicit_extension && is_binary(&mmap) {
                                                return false;
                                            }
                                             // 바이트 레벨 조기 탈출: 매치 가능성이 없는 경우 디코딩 스킵
                                             if ac_panic.find(&mmap).is_none() {
                                                 return false;
                                             }
                                             simple_check_text_mmap(
                                                 &mmap,
                                                 &kw_panic,
                                                 &ac_panic,
                                                 is_exact,
                                                 "",
                                                 sf_panic,
                                             )
                                        }
                                    });

                                    let is_match = match results_panic {
                                        Ok(v) => v,
                                        Err(_) => {
                                            ctx.local_skipped.push((
                                                path_str.clone(),
                                                encode_skip_reason(
                                                    REASON_ERR_PANIC,
                                                    "SMART_SCAN_PANIC",
                                                ),
                                            ));
                                            return ignore::WalkState::Continue;
                                        }
                                    };


                                    if is_match {
                                        ctx.local_results.push((path_str, f_size));
                                    }
                                }
                                Err(err) => {
                                    ctx.local_skipped.push((path_str, encode_skip_reason(REASON_ERR_MMAP, err)));
                                }
                            }
                        }
                        Err(err) => {
                            ctx.local_skipped.push((path_str, encode_skip_reason(REASON_ERR_OPEN, err)));
                        }
                    }

                    ignore::WalkState::Continue
                })
            });
        }

        is_done.store(true, Ordering::SeqCst);
        let final_results = results.lock()
            .map(|g| g.clone())
            .unwrap_or_default();
        let final_skipped = skipped.lock()
            .map(|g| g.clone())
            .unwrap_or_default();
        Ok((final_results, final_skipped))
    })
}

#[pymodule]
fn sf_engine(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Rust 엔진 파닉 발생 시 Python 인터프리터 보호를 위한 커스텀 훅 등록
    std::panic::set_hook(Box::new(|info| {
        let payload = info.payload();
        let msg = if let Some(s) = payload.downcast_ref::<&str>() {
            *s
        } else if let Some(s) = payload.downcast_ref::<String>() {
            s.as_str()
        } else {
            "unknown panic"
        };
        let location = info.location().map(|l| format!("file '{}' at line {}", l.file(), l.line())).unwrap_or_else(|| "unknown location".to_string());
        eprintln!("Rust panic occurred: {} at {}", msg, location);
    }));

    m.add_function(wrap_pyfunction!(search_file, m)?)?;
    m.add_function(wrap_pyfunction!(search_dir, m)?)?;
    m.add_function(wrap_pyfunction!(search_files_list, m)?)?;
    m.add_function(wrap_pyfunction!(find_files_with_keyword, m)?)?;
    m.add("API_VERSION", 4)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use aho_corasick::{AhoCorasick, AhoCorasickBuilder, MatchKind};
    use std::fs;
    use std::path::PathBuf;
    use std::sync::Arc;
    use std::sync::atomic::AtomicBool;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn build_test_ac(pattern: &str) -> AhoCorasick {
        AhoCorasickBuilder::new()
            .ascii_case_insensitive(true)
            .match_kind(MatchKind::LeftmostFirst)
            .build([pattern])
            .expect("failed to build aho-corasick")
    }

    fn make_temp_file(prefix: &str, bytes: &[u8]) -> PathBuf {
        let mut path = std::env::temp_dir();
        let ts = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system time is before unix epoch")
            .as_nanos();
        path.push(format!("sf_engine_{}_{}_{}.bin", prefix, std::process::id(), ts));
        fs::write(&path, bytes).expect("임시 파일 쓰기 실패");
        path
    }

    fn extract_match_contract(matches: &[SearchMatch]) -> Vec<(usize, String, Option<usize>, Option<usize>)> {
        matches
            .iter()
            .map(|m| (m.line, m.content.clone(), m.offset, m.length))
            .collect()
    }

    #[test]
    fn encode_skip_reason_uses_pipe_contract() {
        let encoded = encode_skip_reason(REASON_ERR_OPEN, "Permission denied");
        assert_eq!(encoded, "ERR_OPEN|Permission denied");
    }

    #[test]
    fn binary_file_is_included_for_explicit_file_list_even_when_exclude_binary_is_true() {
        let path = make_temp_file("explicit", b"\x00hello\x00hello");
        let ac = build_test_ac("hello");

        let result = search_file_internal(InternalSearchParams {
            path: path.as_path(),
            pattern: "hello",
            ac: &ac,
            is_exact: false,
            is_json: false,
            is_xml: false,
            is_archive: false,
            is_excel: false,
            exclude_hidden: false,
            exclude_binary: true,
            is_explicit_extension: true,
            stop_flag: Arc::new(AtomicBool::new(false)),
        });

        let _ = fs::remove_file(&path);
        let matches = match result {
            Some(Ok(m)) => m,
            other => panic!("명시적 파일 목록 예상됨 binary match, got {other:?}"),
        };

        assert_eq!(matches.len(), 1);
        assert!(matches[0].content.starts_with(MATCH_META_BINARY_PREFIX));
        assert!(matches[0].content.ends_with("2"));
    }

    #[test]
    fn binary_file_is_excluded_for_directory_style_scan_when_exclude_binary_is_true() {
        let path = make_temp_file("directory", b"\x00hello\x00hello");
        let ac = build_test_ac("hello");

        let result = search_file_internal(InternalSearchParams {
            path: path.as_path(),
            pattern: "hello",
            ac: &ac,
            is_exact: false,
            is_json: false,
            is_xml: false,
            is_archive: false,
            is_excel: false,
            exclude_hidden: false,
            exclude_binary: true,
            is_explicit_extension: false,
            stop_flag: Arc::new(AtomicBool::new(false)),
        });

        let _ = fs::remove_file(&path);
        assert!(result.is_none());
    }

    #[test]
    fn do_search_with_mmap_stops_immediately_when_stop_flag_is_pre_set() {
        let ac = build_test_ac("hello");
        let stop_flag = Arc::new(AtomicBool::new(true));
        let data = b"hello\nhello\nhello\n";

        let matches = do_search_with_mmap(data, "hello", &ac, false, &stop_flag);
        assert!(matches.is_empty());
    }

    #[test]
    fn do_search_with_mmap_returns_deterministic_results_for_same_input() {
        let ac = build_test_ac("hello");
        let stop_flag = Arc::new(AtomicBool::new(false));
        let data = b"one hello\nTwo HELLO\nthree\nhello\n";

        let first = do_search_with_mmap(data, "hello", &ac, false, &stop_flag);
        let second = do_search_with_mmap(data, "hello", &ac, false, &stop_flag);

        assert_eq!(extract_match_contract(&first), extract_match_contract(&second));
        assert!(!first.is_empty());
    }
}