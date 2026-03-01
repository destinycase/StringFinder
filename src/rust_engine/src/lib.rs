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
use crossbeam_channel::Sender;

use crate::archive_search::{check_archive_file, search_archive_file};
use crate::excel_search::{check_excel_file, search_excel_file};
use crate::json_search::{check_json_file, search_json_file};
use crate::types::SearchMatch;
use crate::utils::{
    build_glob_set, decode_bytes, detect_encoding, generate_search_patterns, is_binary,
    match_filename_glob, parse_search_mode,
};
use crate::xml_search::{check_xml_file, search_xml_file};

const MAX_FILE_SIZE: u64 = 1024 * 1024 * 1024; // 1GB 제한
const MAX_DECODE_BUFFER_SIZE: usize = 200 * 1024 * 1024; // 200MB 버퍼 제한

// [상] 품질 보강: 필수 내부 타입 에일리어스 및 에러 마커 (Clippy 경고 대상 아님)
type FileMatches = Vec<(String, Vec<SearchMatch>)>;
type SkippedEntries = Vec<(String, String)>;
type KeywordFileHits = Vec<(String, u64)>;

const REASON_ERR_MMAP: &str = "ERR_MAP";
const REASON_ERR_OPEN: &str = "ERR_OPEN";
const REASON_ERR_METADATA: &str = "ERR_METADATA";
const REASON_ERR_TOO_LARGE: &str = "ERR_TOO_LARGE";
const REASON_ERR_MEMORY_GUARD: &str = "ERR_MEMORY_GUARD";

const MAX_JSON_SIZE: u64 = 100 * 1024 * 1024;
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
    py: Python,
    path: String,
    pattern: String,
    mode_bits: Option<u32>,
    stop_event: Option<pyo3::PyObject>,
) -> PyResult<Vec<SearchMatch>> {
    let norm_pattern = crate::utils::normalize_unicode(&pattern);
    let (is_json, is_xml, is_archive, is_exact, is_excel, exclude_binary, existence_only) = parse_search_mode(mode_bits);
    let patterns = generate_search_patterns(&norm_pattern, is_xml, is_json, is_archive);
    let ac = AhoCorasickBuilder::new()
        .ascii_case_insensitive(true)
        .match_kind(MatchKind::LeftmostFirst)
        .build(&patterns)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("{}", e)))?;

    let stop_flag = Arc::new(AtomicBool::new(false));
    let done_flag = Arc::new(AtomicBool::new(false));
    if let Some(evt) = stop_event {
        let flag_clone = stop_flag.clone();
        let done_clone = done_flag.clone();
        std::thread::spawn(move || {
            loop {
                if done_clone.load(Ordering::Relaxed) || flag_clone.load(Ordering::Relaxed) {
                    break;
                }
                let is_stopped = Python::with_gil(|py| {
                    if done_clone.load(Ordering::Relaxed) {
                        return false;
                    }
                    evt.call_method0(py, "is_set")
                        .and_then(|r| r.is_truthy(py))
                        .unwrap_or(false)
                });
                if is_stopped {
                    flag_clone.store(true, Ordering::SeqCst);
                    break;
                }
                if done_clone.load(Ordering::Relaxed) { break; }
                std::thread::sleep(std::time::Duration::from_millis(MONITOR_INTERVAL_MS));
            }
        });
    }

    let pat_lower = norm_pattern.to_lowercase();
    let pat_bytes = pat_lower.as_bytes().to_vec();

    let res = py.allow_threads(|| {
        search_file_internal(InternalSearchParams {
            path: Path::new(&path),
            pattern: &pattern,
            pat_lower: &pat_lower,
            pat_bytes: &pat_bytes,
            ac: &ac,
            is_exact,
            is_json,
            is_xml,
            is_archive,
            is_excel,
            exclude_hidden: false,
            exclude_binary,
            is_explicit_extension: true,
            existence_only,
            stop_flag,
        })
    });

    done_flag.store(true, Ordering::SeqCst);

    match res {
        Some(Ok(m)) => Ok(m),
        Some(Err(e)) => Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e)),
        None => Ok(Vec::new()),
    }
}

fn do_search_with_mmap(
    mmap: &[u8],
    pat_lower: &str,
    pat_bytes: &[u8],
    ac: &aho_corasick::AhoCorasick,
    is_exact: bool,
    existence_only: bool,
    stop_flag: &Arc<AtomicBool>,
) -> Vec<SearchMatch> {
    // [Optimization] 인코딩 탐지 및 라인 분석 전 Aho-Corasick으로 매치 여부 우선 확인
    if ac.find(mmap).is_none() {
        return Vec::new();
    }

    let mut results = Vec::new();
    let encoding = detect_encoding(mmap);

    if encoding == UTF_8 {
        if is_exact {
            let mut line_number = 1usize;
            let mut last_start = 0usize;
            
            for nl_pos in memchr::memchr_iter(b'\n', mmap) {
                if line_number % 1000 == 0 && stop_flag.load(Ordering::Relaxed) {
                    return results;
                }
                let mut line_bytes = &mmap[last_start..nl_pos];
                if !line_bytes.is_empty() && line_bytes[line_bytes.len() - 1] == b'\r' {
                    line_bytes = &line_bytes[..line_bytes.len() - 1];
                }
                
                let is_match = if line_bytes.is_ascii() && pat_bytes.is_ascii() {
                    line_bytes.eq_ignore_ascii_case(pat_bytes)
                } else {
                    let s = match simdutf8::basic::from_utf8(line_bytes) {
                        Ok(s) => s,
                        Err(_) => "INVALID_UTF8"
                    };
                    s.trim().to_lowercase() == pat_lower
                };

                if is_match {
                    let content = if existence_only {
                        "MATCH".to_string()
                    } else {
                        match simdutf8::basic::from_utf8(line_bytes) {
                            Ok(s) => s.trim().to_string(),
                            Err(_) => String::from_utf8_lossy(line_bytes).trim().to_string(),
                        }
                    };
                    results.push(SearchMatch::new(
                        line_number,
                        content,
                        Some(last_start),
                        Some(nl_pos - last_start),
                    ));
                    if existence_only {
                        return results;
                    }
                }
                last_start = nl_pos + 1;
                line_number += 1;
            }
            
            if last_start < mmap.len() {
                let mut line_bytes = &mmap[last_start..];
                if !line_bytes.is_empty() && line_bytes[line_bytes.len() - 1] == b'\r' {
                    line_bytes = &line_bytes[..line_bytes.len() - 1];
                }
                let is_match = if line_bytes.is_ascii() && pat_bytes.is_ascii() {
                    line_bytes.eq_ignore_ascii_case(pat_bytes)
                } else {
                    let s = match simdutf8::basic::from_utf8(line_bytes) {
                        Ok(s) => s,
                        Err(_) => "INVALID_UTF8"
                    };
                    s.trim().to_lowercase() == pat_lower
                };
                if is_match {
                    let content = if existence_only {
                        "MATCH".to_string()
                    } else {
                        match simdutf8::basic::from_utf8(line_bytes) {
                            Ok(s) => s.trim().to_string(),
                            Err(_) => String::from_utf8_lossy(line_bytes).trim().to_string(),
                        }
                    };
                    results.push(SearchMatch::new(
                        line_number,
                        content,
                        Some(last_start),
                        Some(mmap.len() - last_start),
                    ));
                }
            }
        } else {
            if existence_only {
                if let Some(mat) = ac.find(mmap) {
                    results.push(SearchMatch::new(1, "MATCH".to_string(), Some(mat.start()), Some(mat.len())));
                    return results;
                }
                return results;
            }

            let mut current_line = 1usize;
            let mut last_count_pos = 0usize;
            
            let mut iter = ac.find_iter(mmap);
            while let Some(mat) = iter.next() {
                if results.len() % 100 == 0 && stop_flag.load(Ordering::Relaxed) {
                    return results;
                }
                let m_start = mat.start();
                
                if m_start > last_count_pos {
                    current_line += memchr::memchr_iter(b'\n', &mmap[last_count_pos..m_start]).count();
                }
                last_count_pos = m_start;

                if results.last().is_none() || results.last().unwrap().line != current_line {
                    let line_start = mmap[..m_start].iter().rposition(|&b| b == b'\n').map(|p| p + 1).unwrap_or(0);
                    let line_end = mmap[m_start..].iter().position(|&b| b == b'\n').map(|p| m_start + p).unwrap_or(mmap.len());
                    
                    let line_bytes = &mmap[line_start..line_end];
                    let content = match simdutf8::basic::from_utf8(line_bytes) {
                        Ok(s) => s.trim().to_string(),
                        Err(_) => String::from_utf8_lossy(line_bytes).trim().to_string(),
                    };
                    
                    results.push(SearchMatch::new(current_line, content, Some(m_start), Some(mat.len())));
                }
            }
        }
        return results;
    }

    if existence_only {
        if let Some(mat) = ac.find(mmap) {
            results.push(SearchMatch::new(1, "MATCH".to_string(), Some(mat.start()), Some(mat.len())));
            return results;
        }
        return results;
    }

    let content = decode_bytes(mmap, encoding);
    if is_exact {
        for (i, line) in content.lines().enumerate() {
            if line.trim().eq_ignore_ascii_case(pat_lower) {
                results.push(SearchMatch::new(i + 1, line.to_string(), None, None));
                if existence_only { return results; }
            }
        }
    } else {
        let mut line_number = 1usize;
        let mut last_line_idx = 0usize;
        for mat in ac.find_iter(content.as_str()) {
            let match_start = mat.start();
            if match_start > last_line_idx {
                line_number += content.as_bytes()[last_line_idx..match_start].iter().filter(|&&b| b == b'\n').count();
            }
            if results.last().is_none() || results.last().unwrap().line != line_number {
                let ls = content[..match_start].rfind('\n').map(|i| i + 1).unwrap_or(0);
                let le = content[match_start..].find('\n').map(|i| match_start + i).unwrap_or(content.len());
                results.push(SearchMatch::new(line_number, content[ls..le].trim().to_string(), Some(match_start), Some(mat.len())));
                if existence_only { return results; }
            }
            last_line_idx = match_start;
        }
    }
    results
}

struct InternalSearchParams<'a> {
    path: &'a Path,
    pattern: &'a str,
    pat_lower: &'a str,
    pat_bytes: &'a [u8],
    ac: &'a aho_corasick::AhoCorasick,
    is_exact: bool,
    is_json: bool,
    is_xml: bool,
    is_archive: bool,
    is_excel: bool,
    exclude_hidden: bool,
    exclude_binary: bool,
    is_explicit_extension: bool,
    existence_only: bool,
    stop_flag: Arc<AtomicBool>,
}

fn search_file_internal(params: InternalSearchParams) -> Option<Result<Vec<SearchMatch>, String>> {
    let InternalSearchParams {
        path,
        pattern,
        pat_lower,
        pat_bytes,
        ac,
        is_exact,
        is_json,
        is_xml,
        is_archive,
        is_excel,
        exclude_hidden,
        exclude_binary,
        is_explicit_extension,
        existence_only,
        stop_flag,
    } = params;
    let ext = path
        .extension()
        .and_then(|s| s.to_str())
        .unwrap_or("")
        .to_lowercase();

    if is_excel || ["xlsx", "xlsb", "xls", "xlsm"].contains(&ext.as_str()) {
        if existence_only {
            if check_excel_file(path, pattern, ac, is_exact, stop_flag) {
                return Some(Ok(vec![SearchMatch::new(1, "MATCH".to_string(), None, None)]));
            }
            return None;
        }
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
    let (mmap_content, _is_fallback) = if f_len < 16 * 1024 {
        let mut f = file;
        if let Err(e) = f.read_to_end(&mut fallback_buf) {
            return Some(Err(encode_skip_reason(REASON_ERR_OPEN, e)));
        }
        (fallback_buf.as_slice(), true)
    } else {
        match unsafe { Mmap::map(&file) } {
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
                (fallback_buf.as_slice(), true)
            }
        }
    };

    let ext_lower = format!(".{}", ext);
    let encoding = detect_encoding(mmap_content);

    // [v4.62.0] 특수 모드(JSON, XML, Archive) 인코딩 호환성 강화
    // Rust의 serde_json이나 quick-xml은 UTF-8을 전제하므로, 비-UTF8(UTF-16 등)인 경우 수동 디코딩 수행
    let mut decoded_holder = Vec::new();
    let final_mmap = if (is_json || is_xml || is_archive) && encoding != UTF_8 {
        let decoded = decode_bytes(mmap_content, encoding);
        decoded_holder = decoded.into_bytes();
        decoded_holder.as_slice()
    } else {
        mmap_content
    };

    let res = if is_archive && (ext_lower == ".archive" || ext_lower == ".sf_archive") {
        if existence_only {
            if check_archive_file(final_mmap, pattern, ac, is_exact, stop_flag.clone()) {
                vec![SearchMatch::new(1, "MATCH".to_string(), None, None)]
            } else {
                Vec::new()
            }
        } else {
            search_archive_file(final_mmap, pattern, ac, is_exact, stop_flag.clone())
        }
    } else if is_json && ext_lower == ".json" {
        if final_mmap.len() as u64 > MAX_JSON_SIZE {
            vec![SearchMatch::new(
                1,
                encode_skip_reason(REASON_ERR_MEMORY_GUARD, format!("Size {} bytes exceeds limit", final_mmap.len())),
                None, None,
            )]
        } else if existence_only {
            if check_json_file(final_mmap, pattern, ac, is_exact, stop_flag.clone()) {
                vec![SearchMatch::new(1, "MATCH".to_string(), None, None)]
            } else {
                Vec::new()
            }
        } else {
            search_json_file(final_mmap, pattern, ac, is_exact, stop_flag.clone())
        }
    } else if is_xml && (ext_lower == ".xml" || ext_lower == ".sf_xml") {
        if existence_only {
            if check_xml_file(final_mmap, pattern, ac, is_exact, stop_flag.clone()) {
                vec![SearchMatch::new(1, "MATCH".to_string(), None, None)]
            } else {
                Vec::new()
            }
        } else {
            search_xml_file(final_mmap, pattern, ac, is_exact, stop_flag.clone())
        }
    } else {
        let is_bin = is_binary(mmap_content);
        if exclude_binary && is_bin {
            return None;
        }
        if is_bin {
            if existence_only {
                if ac.find(mmap_content).is_some() {
                    vec![SearchMatch::new(1, format!("{}{}", MATCH_META_BINARY_PREFIX, 1), None, Some(1))]
                } else {
                    Vec::new()
                }
            } else {
                let count = ac.find_iter(mmap_content).count();
                if count > 0 {
                    vec![SearchMatch::new(1, format!("{}{}", MATCH_META_BINARY_PREFIX, count), None, Some(count))]
                } else {
                    Vec::new()
                }
            }
        } else {
            do_search_with_mmap(mmap_content, pat_lower, pat_bytes, ac, is_exact, existence_only, &stop_flag)
        }
    };

    if res.is_empty() {
        None
    } else {
        Some(Ok(res))
    }
}

#[pyfunction]
#[pyo3(signature = (paths, pattern, extensions=None, mode_bits=None, filename_filter=None, exclude_hidden=false, stop_event=None, progress_callback=None, results_callback=None, batch_size=None, flush_ms=None))]
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
    results_callback: Option<PyObject>,
    batch_size: Option<usize>,
    flush_ms: Option<u64>,
) -> PyResult<(FileMatches, SkippedEntries)> {
    let stop_flag = Arc::new(AtomicBool::new(false));
    let done_flag = Arc::new(AtomicBool::new(false));
    let progress_counter = Arc::new(AtomicU64::new(0));

    let stop_flag_mon = stop_flag.clone();
    let done_mon = done_flag.clone();
    let stop_event_mon = stop_event.as_ref().map(|obj| obj.clone_ref(py));
    let progress_cb_mon = progress_callback.as_ref().map(|obj| obj.clone_ref(py));
    let progress_cnt_mon = progress_counter.clone();

    if stop_event.is_some() || progress_callback.is_some() {
        std::thread::spawn(move || {
            while !done_mon.load(Ordering::Relaxed) {
                let is_stopped = Python::with_gil(|py| {
                    if let Some(obj) = &stop_event_mon {
                        if let Ok(res) = obj.bind(py).call_method0("is_set") {
                            if let Ok(true) = res.extract::<bool>() { return true; }
                        }
                    }
                    if let Some(cb) = &progress_cb_mon {
                        let _ = cb.bind(py).call1((progress_cnt_mon.load(Ordering::Relaxed),));
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

    let results_dispatcher = results_callback.as_ref().map(|cb| {
        let (tx, rx) = crossbeam_channel::unbounded::<(String, Vec<SearchMatch>)>();
        let cb_clone = cb.clone_ref(py);
        let stop_flag_dispatcher = stop_flag.clone();
        let done_dispatcher = done_flag.clone();
        let batch_size_limit = batch_size.unwrap_or(100);
        let flush_time_ms = flush_ms.unwrap_or(100) as u128;
        
        let handle = std::thread::spawn(move || {
            let mut batch = Vec::new();
            let mut last_emit = std::time::Instant::now();
            loop {
                let mut received = false;
                while let Ok(res) = rx.try_recv() {
                    batch.push(res);
                    received = true;
                    if batch.len() >= batch_size_limit { break; }
                }
                if !batch.is_empty() && (batch.len() >= batch_size_limit || last_emit.elapsed().as_millis() >= flush_time_ms) {
                    let _ = Python::with_gil(|py| {
                        let _ = cb_clone.bind(py).call1((std::mem::take(&mut batch),));
                    });
                    last_emit = std::time::Instant::now();
                }
                if stop_flag_dispatcher.load(Ordering::Relaxed) { break; }
                if !received {
                    if done_dispatcher.load(Ordering::Relaxed) && rx.is_empty() { break; }
                    std::thread::sleep(std::time::Duration::from_millis(20));
                }
            }
            if !batch.is_empty() {
                let _ = Python::with_gil(|py| {
                    let _ = cb_clone.bind(py).call1((batch,));
                });
            }
        });
        (tx, handle)
    });

    py.allow_threads(|| {
        let (is_json, is_xml, is_archive, is_exact, is_excel, exclude_binary, existence_only) = parse_search_mode(mode_bits);
        let pat_lower = pattern.to_lowercase();
        let pat_bytes = pat_lower.as_bytes().to_vec();
        let ac = Arc::new(AhoCorasickBuilder::new()
            .ascii_case_insensitive(true)
            .match_kind(MatchKind::LeftmostFirst)
            .build([pattern.as_str()])
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("{}", e)))?);

        let exts = extensions.map(|v| v.iter().map(|s| s.trim_start_matches('.').to_lowercase()).collect::<HashSet<_>>());
        let glob_set = build_glob_set(&filename_filter.unwrap_or_default());
        let results = Arc::new(Mutex::new(Vec::new()));
        let skipped = Arc::new(Mutex::new(Vec::new()));

        paths.into_par_iter().for_each(|root| {
            let mut builder = WalkBuilder::new(root);
            builder.hidden(exclude_hidden).ignore(false).git_ignore(false);
            let walker = builder.build_parallel();
            
            let res_ref = results.clone();
            let skip_ref = skipped.clone();
            let ac_ref = ac.clone();
            let stop_ref = stop_flag.clone();
            let pat_ref = pattern.clone();
            let pat_l_ref = pat_lower.clone();
            let pat_b_ref = pat_bytes.clone();
            let exts_ref = exts.clone();
            let glob_ref = glob_set.clone();
            let tx_ref = results_dispatcher.as_ref().map(|(tx, _)| tx.clone());
            let progress_cnt_ref = progress_counter.clone();

            walker.run(|| {
                let ac_inner = ac_ref.clone();
                let stop_inner = stop_ref.clone();
                let pat_inner = pat_ref.clone();
                let pat_l_inner = pat_l_ref.clone();
                let pat_b_inner = pat_b_ref.clone();
                let exts_inner = exts_ref.clone();
                let glob_inner = glob_ref.clone();
                let res_inner = res_ref.clone();
                let skip_inner = skip_ref.clone();
                let tx_inner = tx_ref.clone();
                let progress_cnt_inner = progress_cnt_ref.clone();

                Box::new(move |entry| {
                    if stop_inner.load(Ordering::Relaxed) { return ignore::WalkState::Quit; }
                    let entry = match entry { Ok(e) => e, Err(_) => return ignore::WalkState::Continue };
                    if !entry.file_type().is_some_and(|ft| ft.is_file()) { return ignore::WalkState::Continue; }
                    
                    let path = entry.path();
                    if let Some(name) = path.file_name().and_then(|s| s.to_str()) {
                        if !match_filename_glob(name, &glob_inner) { return ignore::WalkState::Continue; }
                    }
                    if let Some(ref valid) = exts_inner {
                        if let Some(ext) = path.extension().and_then(|s| s.to_str()) {
                            if !valid.contains(&ext.to_lowercase()) { return ignore::WalkState::Continue; }
                        } else { return ignore::WalkState::Continue; }
                    }

                    let res = search_file_internal(InternalSearchParams {
                        path, pattern: &pat_inner, pat_lower: &pat_l_inner, pat_bytes: &pat_b_inner,
                        ac: &ac_inner, is_exact, is_json, is_xml, is_archive, is_excel,
                        exclude_hidden, exclude_binary, is_explicit_extension: false, existence_only,
                        stop_flag: stop_inner.clone(),
                    });

                    if let Some(r) = res {
                        let p_str = path.to_string_lossy().to_string();
                        match r {
                            Ok(m) => {
                                if let Some(tx) = &tx_inner { let _ = tx.send((p_str, m)); }
                                else if let Ok(mut g) = res_inner.lock() { g.push((p_str, m)); }
                            }
                            Err(e) => if let Ok(mut g) = skip_inner.lock() { g.push((p_str, e)); }
                        }
                    }
                    progress_cnt_inner.fetch_add(1, Ordering::Relaxed);
                    ignore::WalkState::Continue
                })
            });
        });

        done_flag.store(true, Ordering::SeqCst);
        if let Some((_, handle)) = results_dispatcher { let _ = handle.join(); }
        
        let final_res = results.lock().map(|g| g.clone()).unwrap_or_default();
        let final_skip = skipped.lock().map(|g| g.clone()).unwrap_or_default();
        Ok((final_res, final_skip))
    })
}

#[pyfunction]
#[pyo3(signature = (file_list, search_string, mode_bits=None, exclude_hidden=false, stop_event=None, progress_callback=None, results_callback=None, batch_size=None, _flush_ms=None, **_kwargs))]
#[allow(clippy::too_many_arguments)]
fn search_files_list(
    py: Python<'_>,
    file_list: Vec<String>,
    search_string: String,
    mode_bits: Option<u32>,
    exclude_hidden: bool,
    stop_event: Option<PyObject>,
    progress_callback: Option<PyObject>,
    results_callback: Option<PyObject>,
    batch_size: Option<usize>,
    _flush_ms: Option<u64>,
    _kwargs: Option<PyObject>,
) -> PyResult<(FileMatches, SkippedEntries)> {
    let stop_flag = Arc::new(AtomicBool::new(false));
    let done_flag = Arc::new(AtomicBool::new(false));
    let progress_counter = Arc::new(AtomicU64::new(0));

    let stop_flag_mon = stop_flag.clone();
    let done_mon = done_flag.clone();
    let stop_event_mon = stop_event.as_ref().map(|obj| obj.clone_ref(py));
    let progress_cb_mon = progress_callback.as_ref().map(|obj| obj.clone_ref(py));
    let progress_cnt_mon = progress_counter.clone();

    if stop_event.is_some() || progress_callback.is_some() {
        std::thread::spawn(move || {
            while !done_mon.load(Ordering::Relaxed) {
                let is_stopped = Python::with_gil(|py| {
                    if let Some(obj) = &stop_event_mon {
                        if let Ok(res) = obj.bind(py).call_method0("is_set") {
                            if let Ok(true) = res.extract::<bool>() { return true; }
                        }
                    }
                    if let Some(cb) = &progress_cb_mon {
                        let _ = cb.bind(py).call1((progress_cnt_mon.load(Ordering::Relaxed),));
                    }
                    false
                });
                if is_stopped { stop_flag_mon.store(true, Ordering::SeqCst); break; }
                std::thread::sleep(std::time::Duration::from_millis(MONITOR_INTERVAL_MS));
            }
        });
    }

    let results_dispatcher = results_callback.as_ref().map(|cb| {
        let (tx, rx) = crossbeam_channel::unbounded::<(String, Vec<SearchMatch>)>();
        let cb_clone = cb.clone_ref(py);
        let done_dispatcher = done_flag.clone();
        let stop_flag_dispatcher = stop_flag.clone();
        let batch_size_limit = batch_size.unwrap_or(100);
        
        let handle = std::thread::spawn(move || {
            let mut batch = Vec::new();
            loop {
                while let Ok(res) = rx.try_recv() {
                    batch.push(res);
                    if batch.len() >= batch_size_limit { break; }
                }
                if !batch.is_empty() {
                    let _ = Python::with_gil(|py| {
                        let _ = cb_clone.bind(py).call1((std::mem::take(&mut batch),));
                    });
                }
                if stop_flag_dispatcher.load(Ordering::Relaxed) { break; }
                if done_dispatcher.load(Ordering::Relaxed) && rx.is_empty() { break; }
                std::thread::sleep(std::time::Duration::from_millis(20));
            }
        });
        (tx, handle)
    });

    py.allow_threads(|| {
        let (is_json, is_xml, is_archive, is_exact, is_excel, exclude_binary, existence_only) = parse_search_mode(mode_bits);
        let pat_lower = search_string.to_lowercase();
        let pat_bytes = pat_lower.as_bytes().to_vec();
        let ac = Arc::new(AhoCorasickBuilder::new()
            .ascii_case_insensitive(true)
            .match_kind(MatchKind::LeftmostFirst)
            .build([search_string.as_str()])
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("{}", e)))?);

        let results = Arc::new(Mutex::new(Vec::new()));
        let skipped = Arc::new(Mutex::new(Vec::new()));
        let tx_main = results_dispatcher.as_ref().map(|(tx, _)| tx.clone());

        file_list.into_par_iter().for_each(|f_path| {
            if stop_flag.load(Ordering::Relaxed) { return; }
            let path = Path::new(&f_path);
            let res = search_file_internal(InternalSearchParams {
                path, pattern: &search_string, pat_lower: &pat_lower, pat_bytes: &pat_bytes,
                ac: &ac, is_exact, is_json, is_xml, is_archive, is_excel,
                exclude_hidden, exclude_binary, is_explicit_extension: true, existence_only,
                stop_flag: stop_flag.clone(),
            });

            if let Some(r) = res {
                match r {
                    Ok(m) => {
                        if let Some(tx) = &tx_main { let _ = tx.send((f_path, m)); }
                        else if let Ok(mut g) = results.lock() { g.push((f_path, m)); }
                    }
                    Err(e) => if let Ok(mut g) = skipped.lock() { g.push((f_path, e)); }
                }
            }
            progress_counter.fetch_add(1, Ordering::Relaxed);
        });

        done_flag.store(true, Ordering::SeqCst);
        if let Some((_, handle)) = results_dispatcher { let _ = handle.join(); }
        
        let final_res = results.lock().map(|g| g.clone()).unwrap_or_default();
        let final_skip = skipped.lock().map(|g| g.clone()).unwrap_or_default();
        Ok((final_res, final_skip))
    })
}

#[pyfunction]
#[pyo3(signature = (paths, keyword, extensions=None, mode_bits=None, filename_filter=None, exclude_hidden=false, stop_event=None, results_callback=None))]
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
    results_callback: Option<PyObject>,
) -> PyResult<(KeywordFileHits, SkippedEntries)> {
    let stop_flag = Arc::new(AtomicBool::new(false));
    let is_done = Arc::new(AtomicBool::new(false));
    let stop_flag_mon = stop_flag.clone();
    let is_done_mon = is_done.clone();
    let stop_evt_mon = stop_event.as_ref().map(|obj| obj.clone_ref(py));

    if stop_event.is_some() {
        std::thread::spawn(move || {
            while !is_done_mon.load(Ordering::Relaxed) && !stop_flag_mon.load(Ordering::Relaxed) {
                let is_stopped = Python::with_gil(|py| {
                    if let Some(obj) = &stop_evt_mon {
                        if let Ok(res) = obj.bind(py).call_method0("is_set") {
                            if let Ok(true) = res.extract::<bool>() { return true; }
                        }
                    }
                    false
                });
                if is_stopped { stop_flag_mon.store(true, Ordering::SeqCst); break; }
                std::thread::sleep(std::time::Duration::from_millis(MONITOR_INTERVAL_MS));
            }
        });
    }

    let results_dispatcher = results_callback.as_ref().map(|cb| {
        let (tx, rx) = crossbeam_channel::unbounded::<(String, u64)>();
        let cb_clone = cb.clone_ref(py);
        let done_dispatcher = is_done.clone();
        
        let handle = std::thread::spawn(move || {
            let mut batch = Vec::new();
            loop {
                while let Ok(res) = rx.try_recv() {
                    batch.push(res);
                    if batch.len() >= 100 { break; }
                }
                if !batch.is_empty() {
                    let _ = Python::with_gil(|py| {
                        let _ = cb_clone.bind(py).call1((std::mem::take(&mut batch),));
                    });
                }
                if done_dispatcher.load(Ordering::Relaxed) && rx.is_empty() { break; }
                std::thread::sleep(std::time::Duration::from_millis(20));
            }
        });
        (tx, handle)
    });

    py.allow_threads(|| {
        let (is_json, is_xml, is_archive, is_exact, _is_excel, exclude_binary, _existence_only) = parse_search_mode(mode_bits);
        let ac = Arc::new(AhoCorasickBuilder::new()
            .ascii_case_insensitive(true)
            .match_kind(MatchKind::LeftmostFirst)
            .build([keyword.as_str()])
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("{}", e)))?);

        let results = Arc::new(Mutex::new(Vec::new()));
        let skipped = Arc::new(Mutex::new(Vec::<(String, String)>::new()));
        let exts = extensions.map(|v| v.iter().map(|s| s.trim_start_matches('.').to_lowercase()).collect::<HashSet<_>>());
        let glob_set = build_glob_set(&filename_filter.unwrap_or_default());

        paths.into_par_iter().for_each(|root| {
            let mut builder = WalkBuilder::new(root);
            builder.hidden(exclude_hidden).ignore(false).git_ignore(false);
            let walker = builder.build_parallel();
            let res_ref = results.clone();
            let _skip_ref = skipped.clone();
            let tx_main = results_dispatcher.as_ref().map(|(tx, _)| tx.clone());

            walker.run(|| {
                let res_inner = res_ref.clone();
                let tx_inner = tx_main.clone();
                let ac_inner = ac.clone();
                let stop_inner = stop_flag.clone();
                let kw_inner = keyword.clone();
                let exts_inner = exts.clone();
                let glob_inner = glob_set.clone();

                struct ThreadContext {
                    local_results: Vec<(String, u64)>,
                    global_results: Arc<Mutex<Vec<(String, u64)>>>,
                    stream_tx: Option<Sender<(String, u64)>>,
                }

                impl Drop for ThreadContext {
                    fn drop(&mut self) {
                        if !self.local_results.is_empty() {
                            if let Some(tx) = &self.stream_tx {
                                for r in self.local_results.drain(..) {
                                    let _ = tx.send(r);
                                }
                            } else if let Ok(mut g) = self.global_results.lock() {
                                g.extend(self.local_results.drain(..));
                            }
                        }
                    }
                }

                let mut ctx = ThreadContext {
                    local_results: Vec::new(),
                    global_results: res_inner,
                    stream_tx: tx_inner,
                };

                Box::new(move |entry| {
                    if stop_inner.load(Ordering::Relaxed) { return ignore::WalkState::Quit; }
                    let entry = match entry { Ok(e) => e, Err(_) => return ignore::WalkState::Continue };
                    if !entry.file_type().is_some_and(|ft| ft.is_file()) { return ignore::WalkState::Continue; }
                    let path = entry.path();
                    if let Some(name) = path.file_name().and_then(|s| s.to_str()) {
                        if !match_filename_glob(name, &glob_inner) { return ignore::WalkState::Continue; }
                    }
                    if let Some(ref valid) = exts_inner {
                        if let Some(ext) = path.extension().and_then(|s| s.to_str()) {
                            if !valid.contains(&ext.to_lowercase()) { return ignore::WalkState::Continue; }
                        } else { return ignore::WalkState::Continue; }
                    }

                    match File::open(path) {
                        Ok(file) => {
                            let meta = match file.metadata() { Ok(m) => m, Err(_) => return ignore::WalkState::Continue };
                            let f_size = meta.len();
                            if f_size == 0 || f_size > MAX_FILE_SIZE { return ignore::WalkState::Continue; }
                            
                            if let Ok(mmap) = unsafe { Mmap::map(&file) } {
                                 let is_match = if is_json && path.extension().is_some_and(|e| e.eq_ignore_ascii_case("json")) {
                                     if f_size > MAX_JSON_SIZE { ac_inner.find(&mmap).is_some() && check_json_file(&mmap, &kw_inner, &ac_inner, is_exact, stop_inner.clone()) }
                                     else { check_json_file(&mmap, &kw_inner, &ac_inner, is_exact, stop_inner.clone()) }
                                 } else if is_xml && path.extension().is_some_and(|e| e.eq_ignore_ascii_case("xml")) {
                                     check_xml_file(&mmap, &kw_inner, &ac_inner, is_exact, stop_inner.clone())
                                 } else if is_archive && path.extension().is_some_and(|e| e.eq_ignore_ascii_case("archive")) {
                                     check_archive_file(&mmap, &kw_inner, &ac_inner, is_exact, stop_inner.clone())
                                 } else if exclude_binary && is_binary(&mmap) {
                                     false
                                 } else {
                                     ac_inner.find(&mmap).is_some()
                                 };

                                if is_match {
                                    let p_str = path.to_string_lossy().to_string();
                                    ctx.local_results.push((p_str, f_size));
                                }
                            }
                        }
                        Err(_) => {}
                    }
                    ignore::WalkState::Continue
                })
            });
        });

        is_done.store(true, Ordering::SeqCst);
        if let Some((_, handle)) = results_dispatcher { let _ = handle.join(); }
        let final_res = results.lock().map(|g| g.to_vec()).unwrap_or_default();
        let _final_skip = skipped.lock().map(|g| g.to_vec()).unwrap_or_default();
        Ok((final_res, Vec::new())) // find_files_with_keyword는 현재 skipped를 반환하지 않음 (이전 로직 준수)
    })
}

#[pymodule]
fn sf_engine(m: &Bound<'_, PyModule>) -> PyResult<()> {
    std::panic::set_hook(Box::new(|info| {
        let payload = info.payload();
        let msg = if let Some(s) = payload.downcast_ref::<&str>() { *s } 
                  else if let Some(s) = payload.downcast_ref::<String>() { s.as_str() } 
                  else { "unknown panic" };
        eprintln!("Rust panic: {}", msg);
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

    fn build_test_ac(pattern: &str) -> aho_corasick::AhoCorasick {
        AhoCorasickBuilder::new().ascii_case_insensitive(true).build([pattern]).unwrap()
    }

    #[test]
    fn do_search_with_mmap_stops_immediately_when_existence_only_is_true() {
        let ac = build_test_ac("hello");
        let stop_flag = Arc::new(AtomicBool::new(false));
        let data = b"hello\nhello\nhello\n";
        let matches = do_search_with_mmap(data, "hello", b"hello", &ac, false, true, &stop_flag);
        assert_eq!(matches.len(), 1);
    }
}
