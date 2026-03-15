// 전역 allow 설정: 유지보수 시 미사용 코드가 CI를 통과하는 것을 방지합니다.
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
use pyo3::wrap_pyfunction;
use rayon::prelude::*;
use std::collections::HashSet;
use std::fs::File;
use std::io::Read;
use std::path::Path;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use crate::archive_search::{check_archive_file, search_archive_file};
use crate::excel_search::{check_excel_file, search_excel_file};
use crate::json_search::{check_json_file, search_json_file};
use crate::utils::{
    build_glob_set, decode_bytes, detect_encoding, generate_search_patterns, is_binary,
    match_filename_glob, parse_search_mode,
};
use crate::xml_search::{check_xml_file, search_xml_file};

const MAX_FILE_SIZE: u64 = 1024 * 1024 * 1024; // 1GB 제한

// 내부 타입 에일리어스 및 에러 마커 정의
type RawMatch = (usize, String, Option<usize>, Option<usize>);
type FileMatches = Vec<(String, Vec<RawMatch>)>;
type SkippedEntries = Vec<(String, String)>;
type KeywordFileHits = Vec<(String, u64)>;

const REASON_ERR_MMAP: &str = "ERR_MAP";
const REASON_ERR_OPEN: &str = "ERR_OPEN";
const REASON_ERR_METADATA: &str = "ERR_METADATA";
const REASON_ERR_TOO_LARGE: &str = "ERR_TOO_LARGE";
const REASON_ERR_MEMORY_GUARD: &str = "ERR_MEMORY_GUARD";

const MAX_JSON_SIZE: u64 = 80 * 1024 * 1024;
const MATCH_META_BINARY_PREFIX: &str = "__SF_BINARY_MATCH__|";
// Python 측에서 문자열 비교에 사용되므로 유지합니다.
#[allow(dead_code)]
const MATCH_META_LONG_LINE_PREFIX: &str = "__SF_LONG_LINE__|";

const MONITOR_INTERVAL_MS: u64 = 100;

fn encode_skip_reason<T: std::fmt::Display>(code: &str, detail: T) -> String {
    format!("{}|{}", code, detail)
}

#[pyfunction]
#[pyo3(signature = (path, pattern, mode_bits=None, stop_event=None, max_per_file=5000, max_check_cells=500000, max_json_depth=20000))]
fn search_file(
    py: Python,
    path: String,
    pattern: String,
    mode_bits: Option<u32>,
    stop_event: Option<pyo3::PyObject>,
    max_per_file: usize,
    max_check_cells: u64,
    max_json_depth: usize,
) -> Result<Vec<RawMatch>, PyErr> {
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
    // C2: JoinHandle을 보관하여 done_flag 설정 후 스레드가 완전히 종료됨을 보장합니다.
    let monitor_handle = if let Some(evt) = stop_event {
        let flag_clone = stop_flag.clone();
        let done_clone = done_flag.clone();
        Some(std::thread::spawn(move || {
            loop {
                if done_clone.load(Ordering::Relaxed) || flag_clone.load(Ordering::Relaxed) {
                    break;
                }
                let is_stopped = Python::with_gil(|py| {
                    if done_clone.load(Ordering::Relaxed) {
                        return false;
                    }
                    if let Ok(res) = evt.bind(py).call_method0("is_set") {
                        if let Ok(true) = res.extract::<bool>() {
                            return true;
                        }
                    }
                    false
                });
                if is_stopped {
                    flag_clone.store(true, Ordering::SeqCst);
                    break;
                }
                std::thread::sleep(std::time::Duration::from_millis(MONITOR_INTERVAL_MS));
            }
        }))
    } else {
        None
    };

    let pat_upper = norm_pattern.to_lowercase().to_uppercase();
    let pat_bytes = norm_pattern.to_lowercase().as_bytes().to_vec();

    let res = py.allow_threads(|| {
        search_file_internal(InternalSearchParams {
            path: Path::new(&path),
            pattern: &pattern,
            pat_upper: &pat_upper,
            pat_bytes: &pat_bytes,
            ac: &ac,
            is_exact,
            is_json,
            is_xml,
            is_archive,
            is_excel,
            exclude_hidden: false,
            exclude_binary,
            existence_only,
            stop_flag,
            max_per_file,
            max_check_cells,
            max_json_depth,
        })
    });

    done_flag.store(true, Ordering::SeqCst);
    if let Some(h) = monitor_handle { let _ = h.join(); }

    match res {
        Some(Ok(mut m)) => {
            if m.len() > max_per_file + 1 { m.truncate(max_per_file + 1); }
            Ok(m)
        },
        Some(Err(e)) => Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e)),
        None => Ok(Vec::new()),
    }
}

fn do_search_with_mmap(
    mmap: &[u8],
    pat_upper: &str,
    pat_bytes: &[u8],
    ac: &aho_corasick::AhoCorasick,
    is_exact: bool,
    existence_only: bool,
    stop_flag: &Arc<AtomicBool>,
    max_per_file: usize,
) -> Vec<RawMatch> {
    let mut results = Vec::new();
    let encoding = detect_encoding(mmap);

    if encoding == UTF_8 {
        if ac.find(mmap).is_none() { return results; }

        if is_exact {
            let mut line_number = 1usize;
            let mut last_start = 0usize;
            for nl_pos in memchr::memchr_iter(b'\n', mmap) {
            if results.len() >= max_per_file + 1 { break; }
                if line_number.is_multiple_of(1000) && stop_flag.load(Ordering::Relaxed) { return results; }
                let mut line_bytes = &mmap[last_start..nl_pos];
                if !line_bytes.is_empty() && line_bytes[line_bytes.len() - 1] == b'\r' {
                    line_bytes = &line_bytes[..line_bytes.len() - 1];
                }
                
                let is_match = if line_bytes.is_ascii() && pat_bytes.is_ascii() {
                    line_bytes.eq_ignore_ascii_case(pat_bytes)
                } else {
                    let s = simdutf8::basic::from_utf8(line_bytes).unwrap_or("INVALID_UTF8");
                    let s_norm = crate::utils::normalize_unicode(s);
                    s_norm.trim().to_lowercase().to_uppercase() == pat_upper
                };

                if is_match {
                    let content = extract_line_content_bytes(mmap, last_start, nl_pos);
                    results.push((line_number, content, None, None));
                    if existence_only { return results; }
                }
                last_start = nl_pos + 1;
                line_number += 1;
            }
            if last_start < mmap.len() && results.len() < max_per_file + 1 {
                let line_bytes = &mmap[last_start..];
                let is_match = if line_bytes.is_ascii() && pat_bytes.is_ascii() { line_bytes.eq_ignore_ascii_case(pat_bytes) }
                else {
                    let s = simdutf8::basic::from_utf8(line_bytes).unwrap_or("INVALID_UTF8");
                    let s_norm = crate::utils::normalize_unicode(s);
                    s_norm.trim().to_lowercase().to_uppercase() == pat_upper
                };
                if is_match {
                    let content = extract_line_content_bytes(mmap, last_start, mmap.len());
                    results.push((line_number, content, None, None));
                }
            }
            return results;
        }

        let mut current_line = 1usize;
        let mut last_nl_pos = 0usize;
        let mut next_nl_pos = memchr::memchr(b'\n', mmap).unwrap_or(mmap.len());
        let mut cached_line: Option<(usize, String)> = None;
        let mut last_returned_line = 0usize;

        for mat in ac.find_iter(mmap) {
            if results.len() >= max_per_file + 1 { break; }
            if results.len() % 1000 == 0 && stop_flag.load(Ordering::Relaxed) { return results; }
            let m_start = mat.start();
            while m_start > next_nl_pos {
                current_line += 1;
                last_nl_pos = next_nl_pos + 1;
                if last_nl_pos >= mmap.len() { next_nl_pos = mmap.len(); break; }
                next_nl_pos = memchr::memchr(b'\n', &mmap[last_nl_pos..]).map(|p| last_nl_pos + p).unwrap_or(mmap.len());
            }

            // 라인당 한 번만 FFI 호출을 수행하도록 최적화합니다.
            let content = if current_line == last_returned_line {
                "__SF_SAME_LINE__".to_string()
            } else {
                last_returned_line = current_line;
                if let Some((l, ref s)) = cached_line {
                    if l == current_line { s.clone() } else {
                        let ns = extract_line_content_bytes(mmap, last_nl_pos, next_nl_pos);
                        cached_line = Some((current_line, ns.clone()));
                        ns
                    }
                } else {
                    let ns = extract_line_content_bytes(mmap, last_nl_pos, next_nl_pos);
                    cached_line = Some((current_line, ns.clone()));
                    ns
                }
            };
            results.push((current_line, content, Some(m_start), Some(mat.len())));
            if existence_only { return results; }
        }
    } else {
        // Non-UTF-8 branch
        let content_str = decode_bytes(mmap, encoding);
        if existence_only {
            if let Some(mat) = ac.find(&content_str) {
                results.push((1, "MATCH".to_string(), Some(mat.start()), Some(mat.len())));
                return results;
            }
            return results;
        }

        for (i, line) in content_str.lines().enumerate() {
            if results.len() >= max_per_file + 1 { break; }
            if i % 1000 == 0 && stop_flag.load(Ordering::Relaxed) { break; }
            let is_match = if is_exact { line.trim().to_lowercase().to_uppercase() == pat_upper }
                           else { ac.find(line).is_some() };
            if is_match {
                results.push((i + 1, line.to_string(), None, None));
            }
        }
    }
    results
}

struct InternalSearchParams<'a> {
    path: &'a Path, pattern: &'a str, pat_upper: &'a str, pat_bytes: &'a [u8], ac: &'a aho_corasick::AhoCorasick,
    is_exact: bool, is_json: bool, is_xml: bool, is_archive: bool, is_excel: bool,
    exclude_hidden: bool, exclude_binary: bool, existence_only: bool, stop_flag: Arc<AtomicBool>,
    max_per_file: usize, max_check_cells: u64, max_json_depth: usize,
}

fn search_file_internal(params: InternalSearchParams) -> Option<Result<Vec<RawMatch>, String>> {
    let InternalSearchParams {
        path, pattern, pat_upper, pat_bytes, ac, is_exact, is_json, is_xml, is_archive, is_excel,
        exclude_hidden, exclude_binary, existence_only, stop_flag,
        max_per_file, max_check_cells, max_json_depth,
    } = params;
    
    let ext = path.extension().and_then(|s| s.to_str()).unwrap_or("").to_lowercase();
    let ext_l = format!(".{}", ext);

    if is_excel || ["xlsx", "xlsb", "xls", "xlsm"].contains(&ext.as_str()) {
        if existence_only {
            if check_excel_file(path, pattern, ac, is_exact, stop_flag, max_check_cells) {
                return Some(Ok(vec![(1, "MATCH".to_string(), None, None)]));
            }
            return None;
        }
        let r_raw = search_excel_file(path, pattern, ac, is_exact, stop_flag, max_per_file, max_check_cells);
        let r = r_raw;
        return if r.is_empty() { None } else { Some(Ok(r)) };
    }

    let file = match File::open(path) { Ok(f) => f, Err(e) => return Some(Err(encode_skip_reason(REASON_ERR_OPEN, e))) };
    let meta = match file.metadata() { Ok(m) => m, Err(e) => return Some(Err(encode_skip_reason(REASON_ERR_METADATA, e))) };

    if exclude_hidden {
        #[cfg(windows)] {
            use std::os::windows::fs::MetadataExt;
            if (meta.file_attributes() & 0x02) != 0 { return None; }
        }
    }

    let f_len = meta.len();
    if f_len == 0 { return None; }
    if f_len > MAX_FILE_SIZE { return Some(Err(encode_skip_reason(REASON_ERR_TOO_LARGE, format!("{} bytes", f_len)))); }

    let mut buf = Vec::new();
    let mmap_h;
    let (mmap_c, _) = if f_len < 16 * 1024 {
        let mut f = file;
        if let Err(e) = f.read_to_end(&mut buf) { return Some(Err(encode_skip_reason(REASON_ERR_OPEN, e))); }
        (buf.as_slice(), true)
    } else {
        match unsafe { Mmap::map(&file) } {
            Ok(m) => { mmap_h = Some(m); (mmap_h.as_ref().unwrap().as_ref(), false) }
            Err(e) => {
                let mut f = file;
                if let Err(re) = f.read_to_end(&mut buf) { return Some(Err(encode_skip_reason(REASON_ERR_MMAP, format!("{}/{}", e, re)))); }
                (buf.as_slice(), true)
            }
        }
    };

    let enc = detect_encoding(mmap_c);
    let mut _dec_h;
    let final_mmap = if is_json || is_xml || is_archive || enc != UTF_8 {
        let d = decode_bytes(mmap_c, enc);
        _dec_h = d.into_bytes();
        _dec_h.as_slice()
    } else { mmap_c };

    let res = if is_archive && (ext_l == ".archive" || ext_l == ".sf_archive") {
        if existence_only {
            if check_archive_file(final_mmap, pattern, ac, is_exact, stop_flag.clone(), max_per_file) { vec![(1, "MATCH".to_string(), None, None)] }
            else { Vec::new() }
        } else { search_archive_file(final_mmap, pattern, ac, is_exact, stop_flag.clone(), max_per_file) }
    } else if is_json && ext_l == ".json" {
        if final_mmap.len() as u64 > MAX_JSON_SIZE {
            vec![(1, encode_skip_reason(REASON_ERR_MEMORY_GUARD, "Large JSON"), None, None)]
        } else if existence_only {
            if check_json_file(final_mmap, pattern, ac, is_exact, stop_flag.clone(), max_json_depth) { vec![(1, "MATCH".to_string(), None, None)] }
            else { Vec::new() }
        } else { search_json_file(final_mmap, pattern, ac, is_exact, stop_flag.clone(), max_per_file, max_json_depth) }
    } else if is_xml && (ext_l == ".xml" || ext_l == ".sf_xml") {
        if existence_only {
            if check_xml_file(final_mmap, pattern, ac, is_exact, stop_flag.clone()) { vec![(1, "MATCH".to_string(), None, None)] }
            else { Vec::new() }
        } else { search_xml_file(final_mmap, pattern, ac, is_exact, stop_flag.clone(), max_per_file) }
    } else {
        let bin = is_binary(mmap_c);
        if exclude_binary && bin { return None; }
        if bin {
            if existence_only {
                if ac.find(mmap_c).is_some() { vec![(1, format!("{}{}", MATCH_META_BINARY_PREFIX, 1), None, Some(1))] }
                else { Vec::new() }
            } else {
                let c = ac.find_iter(mmap_c).count();
                if c > 0 { vec![(1, format!("{}{}", MATCH_META_BINARY_PREFIX, c), None, Some(c))] }
                else { Vec::new() }
            }
        } else {
            do_search_with_mmap(final_mmap, pat_upper, pat_bytes, ac, is_exact, existence_only, &stop_flag, max_per_file)
        }
    };

    if res.is_empty() { None } else { Some(Ok(res)) }
}



#[pyfunction]
#[pyo3(signature = (root_paths, pattern, extensions=None, mode_bits=None, filename_filter=None, exclude_hidden=false, stop_event=None, progress_callback=None, results_callback=None, batch_size=None, _flush_ms=None, max_per_file=None, max_check_cells=None, max_json_depth=None, **_kwargs))]
#[allow(clippy::too_many_arguments)]
pub fn search_dir(
    py: Python,
    root_paths: Vec<String>,
    pattern: String,
    extensions: Option<Vec<String>>,
    mode_bits: Option<u32>,
    filename_filter: Option<Vec<String>>,
    exclude_hidden: bool,
    stop_event: Option<pyo3::PyObject>,
    progress_callback: Option<pyo3::PyObject>,
    results_callback: Option<pyo3::PyObject>,
    batch_size: Option<usize>,
    _flush_ms: Option<u64>,
    max_per_file: Option<usize>,
    max_check_cells: Option<u64>,
    max_json_depth: Option<usize>,
    _kwargs: Option<pyo3::PyObject>,
) -> Result<(FileMatches, SkippedEntries), PyErr> {
    let norm_pattern = crate::utils::normalize_unicode(&pattern);
    let (is_json, is_xml, is_archive, is_exact, is_excel, exclude_binary, existence_only) = parse_search_mode(mode_bits);
    let patterns = generate_search_patterns(&norm_pattern, is_xml, is_json, is_archive);
    let ac = Arc::new(AhoCorasickBuilder::new()
        .ascii_case_insensitive(true)
        .match_kind(MatchKind::LeftmostFirst)
        .build(&patterns)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("{}", e)))?);

    let pat_upper = norm_pattern.to_lowercase().to_uppercase();
    let pat_bytes = norm_pattern.to_lowercase().as_bytes().to_vec();

    let extensions_set = extensions.map(|exts| {
        exts.into_iter().map(|s| s.to_lowercase().trim_start_matches('.').to_string()).collect::<HashSet<_>>()
    });
    let filename_glob_set = filename_filter.as_ref().and_then(|f| build_glob_set(f));

    let stop_flag = Arc::new(AtomicBool::new(false));
    let done_flag = Arc::new(AtomicBool::new(false));
    let processed_files = Arc::new(AtomicU64::new(0)); // M4: 실제 처리 파일 수 카운터

    // N2: JoinHandle을 보관하여 done_flag 설정 후 모니터 스레드가 완전히 종료됨을 보장합니다.
    let monitor_handle = if stop_event.is_some() || progress_callback.is_some() {
        let flag_clone = stop_flag.clone();
        let done_clone = done_flag.clone();
        let stop_evt_mon = stop_event.as_ref().map(|obj| obj.clone_ref(py));
        let progress_cb_mon = progress_callback.as_ref().map(|obj| obj.clone_ref(py));
        let processed_files_mon = processed_files.clone();
        
        Some(std::thread::spawn(move || {
            while !done_clone.load(Ordering::Relaxed) && !flag_clone.load(Ordering::Relaxed) {
                let is_stopped = Python::with_gil(|py| {
                    if done_clone.load(Ordering::Relaxed) { return false; }
                    if let Some(obj) = &stop_evt_mon {
                        if let Ok(res) = obj.bind(py).call_method0("is_set") {
                            if let Ok(true) = res.extract::<bool>() { return true; }
                        }
                    }
                    if let Some(cb) = &progress_cb_mon {
                        // M4: 하드코딩 대신 실제 처리 파일 수 전달
                        let cnt = processed_files_mon.load(Ordering::Relaxed);
                        let _ = cb.bind(py).call1((cnt,));
                    }
                    false
                });
                if is_stopped { flag_clone.store(true, Ordering::SeqCst); break; }
                std::thread::sleep(std::time::Duration::from_millis(MONITOR_INTERVAL_MS));
            }
        }))
    } else {
        None
    };

    let results_dispatcher = results_callback.as_ref().map(|cb| {
        let (tx, rx) = crossbeam_channel::unbounded::<(String, Vec<RawMatch>)>();
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
                    Python::with_gil(|py| {
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

    let results = Arc::new(Mutex::new(Vec::new()));
    let skipped = Arc::new(Mutex::new(Vec::new()));
    let tx_main = results_dispatcher.as_ref().map(|(tx, _)| tx.clone());

    py.allow_threads(|| {
        for root_path in root_paths {
            if stop_flag.load(Ordering::Relaxed) { break; }
            WalkBuilder::new(&root_path)
                .hidden(exclude_hidden)
                .build_parallel()
                .run(|| {
                    let res_ref = results.clone();
                    let skip_ref = skipped.clone();
                    let ac_ref = ac.clone();
                    let stop_ref = stop_flag.clone();
                    let p_upper = pat_upper.clone();
                    let p_bytes = pat_bytes.clone();
                    let pat_orig = pattern.clone();
                    let tx_worker = tx_main.clone();
                    let ext_s = extensions_set.as_ref();
                    let glob_s = filename_glob_set.clone();
                    let file_counter = processed_files.clone(); // M4: 카운터 공유

                    Box::new(move |entry| {
                        if stop_ref.load(Ordering::Relaxed) { return ignore::WalkState::Quit; }
                        let entry = match entry { 
                            Ok(e) => e, 
                            Err(e) => {
                                if let Ok(mut s) = skip_ref.lock() {
                                    s.push(("walker error".to_string(), e.to_string()));
                                }
                                return ignore::WalkState::Continue; 
                            }
                        };
                        if !entry.file_type().is_some_and(|ft| ft.is_file()) { return ignore::WalkState::Continue; }
                        
                        let path = entry.path();
                        if let Some(s) = ext_s {
                            let ext_str = path.extension().and_then(|s| s.to_str()).unwrap_or("").to_lowercase();
                            if !s.contains(&ext_str) { return ignore::WalkState::Continue; }
                        }
                        if let Some(ref set) = glob_s {
                            let filename = path.file_name().and_then(|s| s.to_str()).unwrap_or("");
                            if !set.is_match(filename) { return ignore::WalkState::Continue; }
                        }

                        let res = search_file_internal(InternalSearchParams {
                            path,
                            pattern: &pat_orig,
                            pat_upper: &p_upper,
                            pat_bytes: &p_bytes,
                            ac: &ac_ref,
                            is_exact, is_json, is_xml, is_archive, is_excel,
                            exclude_hidden, exclude_binary, existence_only,
                            stop_flag: stop_ref.clone(),
                            max_per_file: max_per_file.unwrap_or(5000),
                            max_check_cells: max_check_cells.unwrap_or(500_000),
                            max_json_depth: max_json_depth.unwrap_or(20_000),
                        });

                        if let Some(r) = res {
                            let f_path = path.to_string_lossy().to_string();
                            match r {
                                Ok(matches) => {
                                    if !matches.is_empty() {
                                        if let Some(tx) = &tx_worker {
                                            let _ = tx.send((f_path, matches));
                                        } else if let Ok(mut g) = res_ref.lock() {
                                            g.push((f_path, matches));
                                        }
                                    }
                                }
                                Err(e) => {
                                    if let Ok(mut s) = skip_ref.lock() {
                                        s.push((f_path, e));
                                    }
                                }
                            }
                        }
                        file_counter.fetch_add(1, Ordering::Relaxed); // M4: 파일 처리 완료 카운트
                        ignore::WalkState::Continue
                    })
                });
        }
        // A3: done_flag를 먼저 설정하여 dispatcher와 monitor 스레드가 종료 루프에 진입할 수 있도록 합니다.
        done_flag.store(true, Ordering::SeqCst);
        if let Some((_, handle)) = results_dispatcher { let _ = handle.join(); }
        if let Some(h) = monitor_handle { let _ = h.join(); }
    });

    let final_res = results.lock().map(|g| g.clone()).unwrap_or_default();
    let final_skip = skipped.lock().map(|g| g.clone()).unwrap_or_default();
    Ok((final_res, final_skip))
}

#[pyfunction]
#[pyo3(signature = (file_list, search_string, mode_bits=None, exclude_hidden=false, stop_event=None, progress_callback=None, results_callback=None, batch_size=None, _flush_ms=None, max_per_file=None, max_check_cells=None, max_json_depth=None, **_kwargs))]
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
    max_per_file: Option<usize>,
    max_check_cells: Option<u64>,
    max_json_depth: Option<usize>,
    _kwargs: Option<PyObject>,
) -> Result<(FileMatches, SkippedEntries), PyErr> {
    let stop_flag = Arc::new(AtomicBool::new(false));
    let done_flag = Arc::new(AtomicBool::new(false));
    let progress_counter = Arc::new(AtomicU64::new(0));

    let stop_flag_mon = stop_flag.clone();
    let done_mon = done_flag.clone();
    let stop_event_mon = stop_event.as_ref().map(|obj| obj.clone_ref(py));
    let progress_cb_mon = progress_callback.as_ref().map(|obj| obj.clone_ref(py));
    let progress_cnt_mon = progress_counter.clone();

    // X1: search_dir(N2)와 동일하게 JoinHandle을 보관하여 done_flag 설정 후 join합니다.
    let monitor_handle = if stop_event.is_some() || progress_callback.is_some() {
        Some(std::thread::spawn(move || {
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
        }))
    } else {
        None
    };

    let results_dispatcher = results_callback.as_ref().map(|cb| {
        let (tx, rx) = crossbeam_channel::unbounded::<(String, Vec<RawMatch>)>();
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
                    Python::with_gil(|py| {
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
        let norm_pattern = crate::utils::normalize_unicode(&search_string);
        let pat_upper = norm_pattern.to_lowercase().to_uppercase();
        let pat_bytes_v = norm_pattern.to_lowercase().as_bytes().to_vec();
        let (is_json, is_xml, is_archive, is_exact, is_excel, exclude_binary, existence_only) = parse_search_mode(mode_bits);
        let patterns = generate_search_patterns(&norm_pattern, is_xml, is_json, is_archive);

        let ac = Arc::new(AhoCorasickBuilder::new()
            .ascii_case_insensitive(true)
            .match_kind(MatchKind::LeftmostFirst)
            .build(&patterns)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("{}", e)))?);

        let results = Arc::new(Mutex::new(Vec::new()));
        let skipped = Arc::new(Mutex::new(Vec::new()));
        let tx_main = results_dispatcher.as_ref().map(|(tx, _)| tx.clone());

        file_list.into_par_iter().for_each(|f_path| {
            if stop_flag.load(Ordering::Relaxed) { return; }
            let path = Path::new(&f_path);
            let res = search_file_internal(InternalSearchParams {
                path, pattern: &search_string, pat_upper: &pat_upper, pat_bytes: &pat_bytes_v,
                ac: &ac, is_exact, is_json, is_xml, is_archive, is_excel,
                exclude_hidden, exclude_binary, existence_only,
                stop_flag: stop_flag.clone(),
                max_per_file: max_per_file.unwrap_or(5000),
                max_check_cells: max_check_cells.unwrap_or(500_000),
                max_json_depth: max_json_depth.unwrap_or(20_000),
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

        // B1: done_flag를 먼저 설정하여 dispatcher와 monitor 스레드가 종료 루프에 진입할 수 있도록 합니다.
        done_flag.store(true, Ordering::SeqCst);
        if let Some((_, handle)) = results_dispatcher { let _ = handle.join(); }
        if let Some(h) = monitor_handle { let _ = h.join(); }
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
) -> Result<(KeywordFileHits, SkippedEntries), PyErr> {
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

    // M1: `_results_dispatcher` → `results_dispatcher` (언더스코어 제거)
    // `_` prefix 변수는 Rust 컴파일러에 의해 즉시 drop되므로 tx도 함께 소멸되어 콜백 스레드가 동작하지 않았음.
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
                    Python::with_gil(|py| {
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
        let ac_shared = Arc::new(AhoCorasickBuilder::new()
            .ascii_case_insensitive(true)
            .match_kind(MatchKind::LeftmostFirst)
            .build([keyword.as_str()])
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("{}", e)))?);

        let results = Arc::new(Mutex::new(Vec::new()));
        let skipped = Arc::new(Mutex::new(Vec::<(String, String)>::new()));
        let exts = extensions.map(|v| v.iter().map(|s| s.trim_start_matches('.').to_lowercase()).collect::<HashSet<String>>());
        let glob_set = build_glob_set(&filename_filter.unwrap_or_default());

        paths.into_par_iter().for_each(|root| {
            let mut builder = WalkBuilder::new(&root);
            builder.hidden(exclude_hidden).ignore(false).git_ignore(false);
            let walker = builder.build_parallel();
            let res_ref = Arc::clone(&results);
            let ac_ref = Arc::clone(&ac_shared);
            let stop_ref = Arc::clone(&stop_flag);
            let kw_orig = keyword.clone();
            let ext_s = exts.clone();
            let glob_s = glob_set.clone();
            // M1: results_dispatcher에서 tx 채널 추출
            let tx_kw = results_dispatcher.as_ref().map(|(tx, _)| tx.clone());

            walker.run(move || {
                let res_inner = Arc::clone(&res_ref);
                let ac_inner = Arc::clone(&ac_ref);
                let stop_inner = Arc::clone(&stop_ref);
                let kw_inner = kw_orig.clone();
                let exts_inner = ext_s.clone();
                let glob_inner = glob_s.clone();
                let tx_inner = tx_kw.clone(); // M1: tx 채널 공유

                Box::new(move |entry| {
                    if stop_inner.load(Ordering::Relaxed) { return ignore::WalkState::Quit; }
                    let entry = match entry { Ok(e) => e, Err(_) => return ignore::WalkState::Continue };
                    if !entry.file_type().is_some_and(|ft| ft.is_file()) { return ignore::WalkState::Continue; }
                    
                    let path = entry.path();
                    if let Some(ref valid) = exts_inner {
                        let ext_str = path.extension().and_then(|s| s.to_str()).unwrap_or("").to_lowercase();
                        if !valid.contains(&ext_str) { return ignore::WalkState::Continue; }
                    }
                    if !match_filename_glob(path.file_name().and_then(|s| s.to_str()).unwrap_or(""), &glob_inner) {
                        return ignore::WalkState::Continue;
                    }

                    if let Ok(file) = File::open(path) {
                        let meta = match file.metadata() { Ok(m) => m, Err(_) => return ignore::WalkState::Continue };
                        let f_size = meta.len();
                        if f_size == 0 || f_size > MAX_FILE_SIZE { return ignore::WalkState::Continue; }
                        
                        if let Ok(mmap) = unsafe { Mmap::map(&file) } {
                             let is_match = if is_json && path.extension().is_some_and(|e| e.eq_ignore_ascii_case("json")) {
                                 check_json_file(&mmap, &kw_inner, &ac_inner, is_exact, stop_inner.clone(), 20_000)
                             } else if is_xml && path.extension().is_some_and(|e| e.eq_ignore_ascii_case("xml")) {
                                 check_xml_file(&mmap, &kw_inner, &ac_inner, is_exact, stop_inner.clone())
                             } else if is_archive && path.extension().is_some_and(|e| e.eq_ignore_ascii_case("archive")) {
                                 check_archive_file(&mmap, &kw_inner, &ac_inner, is_exact, stop_inner.clone(), 5000)
                             } else if exclude_binary && is_binary(&mmap) {
                                 false
                             } else {
                                 ac_inner.find(&mmap).is_some()
                             };

                            if is_match {
                                let f_path = path.to_string_lossy().to_string();
                                // M1: tx 채널이 있으면 스트리밍, 없으면 공유 벡터에 직접 저장
                                if let Some(tx) = &tx_inner {
                                    let _ = tx.send((f_path, f_size));
                                } else if let Ok(mut g) = res_inner.lock() {
                                    g.push((f_path, f_size));
                                }
                            }
                        }
                    }
                    ignore::WalkState::Continue
                })
            });
        });

        // N1: is_done을 먼저 설정하여 dispatcher가 종료 루프에 진입할 수 있도록 합니다.
        is_done.store(true, Ordering::SeqCst);
        if let Some((_, handle)) = results_dispatcher { let _ = handle.join(); }
        let final_res = results.lock().map(|g| g.clone()).unwrap_or_default();
        let final_skipped = skipped.lock().map(|g| g.clone()).unwrap_or_default();
        Ok((final_res, final_skipped))
    })
}

#[pymodule]
fn sf_engine(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // [Stability] 커스텀 패닉 훅 설치:
    // catch_unwind가 모든 패닉을 처리하므로, Rust 기본 훅의 stderr 출력은 불필요합니다.
    // 훅을 no-op으로 교체하여 로그 창에 원시 패닉 메시지가 출력되는 현상을 방지합니다.
    std::panic::set_hook(Box::new(|_info| {
        // 의도적으로 비워둠: catch_unwind에서 패닉을 처리하므로 별도 출력 불필요
    }));

    m.add_function(wrap_pyfunction!(search_file, m)?)?;
    m.add_function(wrap_pyfunction!(search_dir, m)?)?;
    m.add_function(wrap_pyfunction!(search_files_list, m)?)?;
    m.add_function(wrap_pyfunction!(find_files_with_keyword, m)?)?;
    m.add("API_VERSION", 5)?;
    // Cargo.toml version 필드를 빌드 시점에 자동으로 읽어 Python 측에 노출합니다.
    m.add("ENGINE_VERSION", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}


fn extract_line_content_bytes(mmap: &[u8], start: usize, end: usize) -> String {
    let mut line_end = end;
    if line_end > start && mmap[line_end - 1] == b'\r' {
        line_end -= 1;
    }
    let line_len = line_end - start;
    if line_len > 4096 {
        let preview_len = 1024.min(line_len);
        let preview_bytes = &mmap[start..start + preview_len];
        let preview_text = match simdutf8::basic::from_utf8(preview_bytes) {
            Ok(s) => s.trim().to_string(),
            Err(_) => String::from_utf8_lossy(preview_bytes).trim().to_string(),
        };
        return format!("__SF_LONG_LINE__|{}", preview_text);
    }
    let line_bytes = &mmap[start..line_end];
    match simdutf8::basic::from_utf8(line_bytes) {
        Ok(s) => s.trim().to_string(),
        Err(_) => String::from_utf8_lossy(line_bytes).trim().to_string(),
    }
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
        let matches = do_search_with_mmap(data, "hello", b"hello", &ac, false, true, &stop_flag, 5000);
        assert_eq!(matches.len(), 1);
    }
}