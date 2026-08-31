// 전역 allow 설정: 유지보수 시 미사용 코드가 CI를 통과하는 것을 방지합니다.
mod excel_search;
mod json_search;
mod types;
mod utils;
mod xml_search;

use aho_corasick::{AhoCorasickBuilder, MatchKind};
use encoding_rs::{Encoding, UTF_16BE, UTF_16LE, UTF_8};
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
use std::time::SystemTime;


use crate::excel_search::{check_excel_file, search_excel_file};
use crate::json_search::{check_json_file, search_json_file};
use crate::utils::{
    build_glob_set, decode_bytes, detect_encoding, generate_search_patterns, is_binary,
    match_filename_glob, parse_search_mode,
};
use crate::xml_search::{check_xml_file, search_xml_file};
use crate::types::{SearchMatch, SearchOptions};

const MAX_FILE_SIZE: u64 = 1024 * 1024 * 1024; // 1GB 제한

// 내부 타입 에일리어스 및 에러 마커 정의
type RawMatch = (usize, String, Option<usize>, Option<usize>);
type RawFileMatches = Vec<(String, Vec<RawMatch>)>;
type FileMatches = Vec<(String, Vec<SearchMatch>)>;
type SkippedEntries = Vec<(String, String)>;
type KeywordFileHits = Vec<(String, u64)>;

const REASON_ERR_MMAP: &str = "ERR_MAP";
const REASON_ERR_OPEN: &str = "ERR_OPEN";
const REASON_ERR_METADATA: &str = "ERR_METADATA";
const REASON_ERR_TOO_LARGE: &str = "ERR_TOO_LARGE";
const REASON_ERR_MEMORY_GUARD: &str = "ERR_MEMORY_GUARD";

const DEFAULT_MAX_JSON_SIZE: u64 = 500 * 1024 * 1024;
const MATCH_META_BINARY_PREFIX: &str = "__SF_BINARY_MATCH__|";
const MATCH_META_TRUNCATED: &str = "__SF_TRUNCATED__";
// Python 측에서 문자열 비교에 사용되므로 유지합니다.
#[allow(dead_code)]
const MATCH_META_LONG_LINE_PREFIX: &str = "__SF_LONG_LINE__|";

const MONITOR_INTERVAL_MS: u64 = 100;

struct CallbackState {
    failed: AtomicBool,
    message: Mutex<Option<String>>,
}

impl CallbackState {
    fn new() -> Self {
        Self { failed: AtomicBool::new(false), message: Mutex::new(None) }
    }

    fn record_error(&self, stop_flag: &Arc<AtomicBool>, name: &str, error: PyErr) {
        if !self.failed.swap(true, Ordering::SeqCst) {
            let mut message = self.message.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
            *message = Some(format!("{} callback failed: {}", name, error));
        }
        stop_flag.store(true, Ordering::SeqCst);
    }

    fn error_message(&self) -> Option<String> {
        self.message.lock().unwrap_or_else(|poisoned| poisoned.into_inner()).clone()
    }
}

fn to_python_matches(matches: Vec<RawMatch>) -> Vec<SearchMatch> {
    matches.into_iter().map(SearchMatch::from).collect()
}

fn to_python_file_matches(results: RawFileMatches) -> FileMatches {
    results
        .into_iter()
        .map(|(path, matches)| (path, to_python_matches(matches)))
        .collect()
}

fn encode_skip_reason<T: std::fmt::Display>(code: &str, detail: T) -> String {
    format!("{}|{}", code, detail)
}

enum FileSnapshot {
    Mapped { _lock_file: File, mmap: Mmap },
    Owned(Vec<u8>),
}

impl FileSnapshot {
    fn as_slice(&self) -> &[u8] {
        match self {
            Self::Mapped { mmap, .. } => mmap,
            Self::Owned(bytes) => bytes,
        }
    }
}

fn metadata_matches(file: &File, expected_len: u64, expected_modified: Option<SystemTime>) -> bool {
    let Ok(meta) = file.metadata() else { return false; };
    if meta.len() != expected_len { return false; }
    expected_modified.is_none_or(|expected| meta.modified().ok() == Some(expected))
}

fn load_file_snapshot(
    file: File,
    expected_len: u64,
    expected_modified: Option<SystemTime>,
) -> Result<FileSnapshot, String> {
    if expected_len < 16 * 1024 {
        let mut bytes = Vec::new();
        let mut reader = file;
        reader.read_to_end(&mut bytes).map_err(|e| e.to_string())?;
        return Ok(FileSnapshot::Owned(bytes));
    }

    // A shared lock protects the mmap path on platforms that enforce file
    // sharing. If it cannot be acquired, use a read-copy snapshot instead.
    if fs2::FileExt::try_lock_shared(&file).is_ok() {
        match unsafe { Mmap::map(&file) } {
            Ok(mmap) if metadata_matches(&file, expected_len, expected_modified) => {
                return Ok(FileSnapshot::Mapped { _lock_file: file, mmap });
            }
            Ok(_) | Err(_) => {}
        }
        let _ = fs2::FileExt::unlock(&file);
    }

    let mut bytes = Vec::new();
    let mut reader = file.try_clone().map_err(|e| e.to_string())?;
    reader.read_to_end(&mut bytes).map_err(|e| e.to_string())?;
    Ok(FileSnapshot::Owned(bytes))
}

#[pyfunction]
#[pyo3(signature = (path, pattern, mode_bits=None, stop_event=None, max_per_file=5000, max_check_cells=500000, max_json_depth=20000, max_json_size=524288000, options=None))]
#[allow(clippy::too_many_arguments)]
fn search_file(
    py: Python,
    path: String,
    pattern: String,
    mut mode_bits: Option<u32>,
    mut stop_event: Option<pyo3::PyObject>,
    mut max_per_file: usize,
    mut max_check_cells: u64,
    mut max_json_depth: usize,
    mut max_json_size: u64,
    options: Option<Py<SearchOptions>>,
) -> Result<Vec<SearchMatch>, PyErr> {
    if let Some(config) = options.as_ref() {
        let config = config.bind(py).borrow();
        if config.mode_bits.is_some() { mode_bits = config.mode_bits; }
        if config.stop_event.is_some() { stop_event = config.stop_event.as_ref().map(|event| event.clone_ref(py)); }
        if config.max_per_file.is_some() { max_per_file = config.max_per_file.unwrap_or(max_per_file); }
        if config.max_check_cells.is_some() { max_check_cells = config.max_check_cells.unwrap_or(max_check_cells); }
        if config.max_json_depth.is_some() { max_json_depth = config.max_json_depth.unwrap_or(max_json_depth); }
        if config.max_json_size.is_some() { max_json_size = config.max_json_size.unwrap_or(max_json_size); }
    }
    let norm_pattern = crate::utils::normalize_unicode(&pattern);
    let (is_json, is_xml, is_exact, is_excel, exclude_binary, existence_only) = parse_search_mode(mode_bits);
    let patterns = generate_search_patterns(&norm_pattern, is_xml, is_json);
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
            is_excel,
            exclude_hidden: false,
            exclude_binary,
            existence_only,
            stop_flag,
            max_per_file,
            max_check_cells,
            max_json_depth,
            max_json_size,
        })
    });

    done_flag.store(true, Ordering::SeqCst);
    if let Some(h) = monitor_handle { let _ = h.join(); }

    match res {
        Some(Ok(m)) => {
            Ok(to_python_matches(apply_match_limit(m, max_per_file)))
        },
        Some(Err(e)) => Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e)),
        None => Ok(Vec::new()),
    }
}

#[allow(clippy::too_many_arguments)]
fn do_search_with_mmap(
    mmap: &[u8],
    encoding: &'static Encoding,
    pat_upper: &str,
    ac: &aho_corasick::AhoCorasick,
    is_exact: bool,
    existence_only: bool,
    stop_flag: &Arc<AtomicBool>,
    max_per_file: usize,
) -> Vec<RawMatch> {
    let mut results = Vec::new();
    if encoding == UTF_8 {
        if ac.find(mmap).is_none() { return results; }

        if is_exact {
            let mut line_number = 1usize;
            let mut last_start = 0usize;
            for nl_pos in memchr::memchr_iter(b'\n', mmap) {
            if results.len() > max_per_file { break; }
                if line_number.is_multiple_of(1000) && stop_flag.load(Ordering::Relaxed) { return results; }
                let mut line_bytes = &mmap[last_start..nl_pos];
                if !line_bytes.is_empty() && line_bytes[line_bytes.len() - 1] == b'\r' {
                    line_bytes = &line_bytes[..line_bytes.len() - 1];
                }
                
                let is_match = exact_line_matches(line_bytes, pat_upper);

                if is_match {
                    let content = extract_line_content_bytes(mmap, last_start, nl_pos, None, None);
                    results.push((line_number, content, None, None));
                    if existence_only { return results; }
                }
                last_start = nl_pos + 1;
                line_number += 1;
            }
            if last_start < mmap.len() && results.len() < max_per_file + 1 {
                let mut line_bytes = &mmap[last_start..];
                if line_bytes.last() == Some(&b'\r') {
                    line_bytes = &line_bytes[..line_bytes.len() - 1];
                }
                let is_match = exact_line_matches(line_bytes, pat_upper);
                if is_match {
                    let content = extract_line_content_bytes(mmap, last_start, mmap.len(), None, None);
                    results.push((line_number, content, None, None));
                }
            }
            return results;
        }

        let mut current_line = 1usize;
        let mut last_nl_pos = 0usize;
        let mut next_nl_pos = memchr::memchr(b'\n', mmap).unwrap_or(mmap.len());

        for mat in ac.find_iter(mmap) {
            if results.len() > max_per_file { break; }
            if results.len() % 1000 == 0 && stop_flag.load(Ordering::Relaxed) { return results; }
            let m_start = mat.start();
            while m_start > next_nl_pos {
                current_line += 1;
                last_nl_pos = next_nl_pos + 1;
                if last_nl_pos >= mmap.len() { next_nl_pos = mmap.len(); break; }
                next_nl_pos = memchr::memchr(b'\n', &mmap[last_nl_pos..]).map(|p| last_nl_pos + p).unwrap_or(mmap.len());
            }

            // 라인당 한 번만 FFI 호출을 수행하도록 최적화합니다.
            let content = extract_line_content_bytes(mmap, last_nl_pos, next_nl_pos, Some(m_start), Some(mat.len()));
            let (offset, length) = if content.starts_with(MATCH_META_LONG_LINE_PREFIX) {
                (None, None)
            } else {
                (Some(m_start), Some(mat.len()))
            };
            results.push((current_line, content, offset, length));
            if existence_only { return results; }
        }
    } else {
        // Non-UTF8 일반 텍스트는 파일 전체를 String으로 복사하지 않고 청크 단위로 디코딩합니다.
        search_non_utf8_chunks(mmap, encoding, pat_upper, ac, is_exact, existence_only, stop_flag, max_per_file, &mut results);
    }
    results
}

fn apply_match_limit(mut matches: Vec<RawMatch>, max_per_file: usize) -> Vec<RawMatch> {
    let is_single_error_marker = matches.len() == 1 && matches[0].1.starts_with("ERR_");
    if matches.len() > max_per_file && !is_single_error_marker {
        matches.truncate(max_per_file);
        matches.push((0, MATCH_META_TRUNCATED.to_string(), None, None));
    }
    matches
}

fn exact_line_matches(line: &[u8], pat_upper: &str) -> bool {
    let s = simdutf8::basic::from_utf8(line).unwrap_or("INVALID_UTF8");
    let s_norm = crate::utils::normalize_unicode(s);
    s_norm.trim().to_lowercase().to_uppercase() == pat_upper
}

fn decoded_line_matches(line: &str, pat_upper: &str, ac: &aho_corasick::AhoCorasick, is_exact: bool) -> bool {
    if is_exact {
        let normalized = crate::utils::normalize_unicode(line);
        normalized.trim().to_lowercase().to_uppercase() == pat_upper
    } else {
        ac.find(line).is_some()
    }
}

#[allow(clippy::too_many_arguments)]
fn search_non_utf8_chunks(
    mmap: &[u8],
    encoding: &'static Encoding,
    pat_upper: &str,
    ac: &aho_corasick::AhoCorasick,
    is_exact: bool,
    existence_only: bool,
    stop_flag: &Arc<AtomicBool>,
    max_per_file: usize,
    results: &mut Vec<RawMatch>,
) {
    const DECODE_CHUNK_SIZE: usize = 64 * 1024;
    let mut decoder = encoding.new_decoder_without_bom_handling();
    let mut pending = String::new();
    let mut input_offset = 0usize;
    let mut line_number = 1usize;

    while input_offset < mmap.len() {
        let end = (input_offset + DECODE_CHUNK_SIZE).min(mmap.len());
        let last = end == mmap.len();
        // encoding_rs는 출력 버퍼가 가득 차면 입력을 소비하지 않으므로 충분한
        // 청크 출력 공간을 미리 확보합니다. 필요 시 내부적으로 더 작은 결과를 생성합니다.
        let mut decoded = String::with_capacity((end - input_offset).saturating_mul(3));
        let (_status, consumed, _had_errors) = decoder.decode_to_string(&mmap[input_offset..end], &mut decoded, last);
        if consumed == 0 && end > input_offset {
            // Decoder가 입력을 소비하지 못하는 경우 무한 루프를 방지합니다.
            break;
        }
        input_offset += consumed;
        pending.push_str(&decoded);

        while let Some(newline) = pending.find('\n') {
            if results.len() > max_per_file { return; }
            if line_number.is_multiple_of(1000) && stop_flag.load(Ordering::Relaxed) { return; }
            let line = pending[..newline].strip_suffix('\r').unwrap_or(&pending[..newline]);
            if decoded_line_matches(line, pat_upper, ac, is_exact) {
                if existence_only {
                    results.push((line_number, "MATCH".to_string(), None, None));
                    return;
                }
                results.push((line_number, line.to_string(), None, None));
            }
            pending.drain(..newline + 1);
            line_number += 1;
        }

        if consumed == 0 { break; }
    }

    if !pending.is_empty() && results.len() <= max_per_file && !stop_flag.load(Ordering::Relaxed) {
        let line = pending.strip_suffix('\r').unwrap_or(&pending);
        if decoded_line_matches(line, pat_upper, ac, is_exact) {
            if existence_only {
                results.push((line_number, "MATCH".to_string(), None, None));
            } else {
                results.push((line_number, line.to_string(), None, None));
            }
        }
    }
}

struct InternalSearchParams<'a> {
    path: &'a Path, pattern: &'a str, pat_upper: &'a str, pat_bytes: &'a [u8], ac: &'a aho_corasick::AhoCorasick,
    is_exact: bool, is_json: bool, is_xml: bool, is_excel: bool,
    exclude_hidden: bool, exclude_binary: bool, existence_only: bool, stop_flag: Arc<AtomicBool>,
    max_per_file: usize, max_check_cells: u64, max_json_depth: usize, max_json_size: u64,
}

fn search_file_internal(params: InternalSearchParams) -> Option<Result<Vec<RawMatch>, String>> {
    let InternalSearchParams {
        path, pattern, pat_upper, pat_bytes: _pat_bytes, ac, is_exact, is_json, is_xml, is_excel,
        exclude_hidden, exclude_binary, existence_only, stop_flag,
        max_per_file, max_check_cells, max_json_depth, max_json_size,
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
    let f_modified = meta.modified().ok();
    if f_len == 0 { return None; }
    if f_len > MAX_FILE_SIZE { return Some(Err(encode_skip_reason(REASON_ERR_TOO_LARGE, format!("{} bytes", f_len)))); }

    let file_snapshot = match load_file_snapshot(file, f_len, f_modified) {
        Ok(snapshot) => snapshot,
        Err(e) => return Some(Err(encode_skip_reason(REASON_ERR_MMAP, e))),
    };
    let mmap_c = file_snapshot.as_slice();

    let enc = detect_encoding(mmap_c);
    let mut _dec_h;
    // 구조화된 문서는 파서가 전체 버퍼를 요구하므로 기존 디코딩 경로를 유지합니다.
    // 일반 Non-UTF8 텍스트는 아래의 청크 디코딩 경로에서 처리하여 파일 전체 String 복사를 피합니다.
    let final_mmap = if is_json || is_xml {
        let d = decode_bytes(mmap_c, enc);
        _dec_h = d.into_bytes();
        _dec_h.as_slice()
    } else { mmap_c };

    let res = if is_json && ext_l == ".json" {
        if final_mmap.len() as u64 > max_json_size {
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
        // 인코딩 감지에 성공한 텍스트(UTF-16/EUC-KR 등)는 NUL 바이트가 포함될 수
        // 있으므로 원본 바이트만 보고 바이너리로 판정하지 않습니다.
        let bin = enc == UTF_8 && is_binary(mmap_c);
        if exclude_binary && bin { return None; }
        if bin {
            if existence_only {
                if ac.find(mmap_c).is_some() { vec![(0, format!("{}{}", MATCH_META_BINARY_PREFIX, 1), None, Some(1))] }
                else { Vec::new() }
            } else {
                let c = ac.find_iter(mmap_c).count();
                if c > 0 { vec![(0, format!("{}{}", MATCH_META_BINARY_PREFIX, c), None, Some(c))] }
                else { Vec::new() }
            }
        } else {
            let bom_len = if (enc == UTF_16LE && mmap_c.starts_with(b"\xff\xfe"))
                || (enc == UTF_16BE && mmap_c.starts_with(b"\xfe\xff"))
            {
                2
            } else if enc == UTF_8 && mmap_c.starts_with(b"\xef\xbb\xbf") {
                3
            } else {
                0
            };
            let searchable_mmap = &mmap_c[bom_len..];
            do_search_with_mmap(searchable_mmap, enc, pat_upper, ac, is_exact, existence_only, &stop_flag, max_per_file)
        }
    };

    let res = apply_match_limit(res, max_per_file);
    if res.is_empty() { None } else { Some(Ok(res)) }
}



#[pyfunction]
#[pyo3(signature = (root_paths, pattern, extensions=None, mode_bits=None, filename_filter=None, exclude_hidden=false, stop_event=None, progress_callback=None, results_callback=None, batch_size=None, _flush_ms=None, max_per_file=None, max_check_cells=None, max_json_depth=None, max_json_size=None, options=None, **_kwargs))]
#[allow(clippy::too_many_arguments)]
pub fn search_dir(
    py: Python,
    root_paths: Vec<String>,
    pattern: String,
    mut extensions: Option<Vec<String>>,
    mut mode_bits: Option<u32>,
    mut filename_filter: Option<Vec<String>>,
    mut exclude_hidden: bool,
    mut stop_event: Option<pyo3::PyObject>,
    mut progress_callback: Option<pyo3::PyObject>,
    mut results_callback: Option<pyo3::PyObject>,
    mut batch_size: Option<usize>,
    mut _flush_ms: Option<u64>,
    mut max_per_file: Option<usize>,
    mut max_check_cells: Option<u64>,
    mut max_json_depth: Option<usize>,
    mut max_json_size: Option<u64>,
    options: Option<Py<SearchOptions>>,
    _kwargs: Option<pyo3::PyObject>,
) -> Result<(FileMatches, SkippedEntries), PyErr> {
    if let Some(config) = options.as_ref() {
        let config = config.bind(py).borrow();
        if config.extensions.is_some() { extensions = config.extensions.clone(); }
        if config.mode_bits.is_some() { mode_bits = config.mode_bits; }
        if config.filename_filter.is_some() { filename_filter = config.filename_filter.clone(); }
        exclude_hidden = config.exclude_hidden;
        if config.stop_event.is_some() { stop_event = config.stop_event.as_ref().map(|event| event.clone_ref(py)); }
        if config.progress_callback.is_some() { progress_callback = config.progress_callback.as_ref().map(|callback| callback.clone_ref(py)); }
        if config.results_callback.is_some() { results_callback = config.results_callback.as_ref().map(|callback| callback.clone_ref(py)); }
        if config.batch_size.is_some() { batch_size = config.batch_size; }
        if config.flush_ms.is_some() { _flush_ms = config.flush_ms; }
        if config.max_per_file.is_some() { max_per_file = config.max_per_file; }
        if config.max_check_cells.is_some() { max_check_cells = config.max_check_cells; }
        if config.max_json_depth.is_some() { max_json_depth = config.max_json_depth; }
        if config.max_json_size.is_some() { max_json_size = config.max_json_size; }
    }
    let norm_pattern = crate::utils::normalize_unicode(&pattern);
    let (is_json, is_xml, is_exact, is_excel, exclude_binary, existence_only) = parse_search_mode(mode_bits);
    let patterns = generate_search_patterns(&norm_pattern, is_xml, is_json);
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
    let callback_state = Arc::new(CallbackState::new());
    let processed_files = Arc::new(AtomicU64::new(0)); // M4: 실제 처리 파일 수 카운터

    // N2: JoinHandle을 보관하여 done_flag 설정 후 모니터 스레드가 완전히 종료됨을 보장합니다.
    let monitor_handle = if stop_event.is_some() || progress_callback.is_some() {
        let flag_clone = stop_flag.clone();
        let done_clone = done_flag.clone();
        let stop_evt_mon = stop_event.as_ref().map(|obj| obj.clone_ref(py));
        let progress_cb_mon = progress_callback.as_ref().map(|obj| obj.clone_ref(py));
        let processed_files_mon = processed_files.clone();
        let callback_state_mon = callback_state.clone();
        
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
                        if let Err(error) = cb.bind(py).call1((cnt,)) {
                            callback_state_mon.record_error(&flag_clone, "progress", error);
                            return true;
                        }
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
        let batch_size_limit = batch_size.unwrap_or(100).max(1);
        let queue_capacity = batch_size_limit.saturating_mul(4).max(1);
        let (tx, rx) = crossbeam_channel::bounded::<(String, Vec<RawMatch>)>(queue_capacity);
        let cb_clone = cb.clone_ref(py);
        let done_dispatcher = done_flag.clone();
        let callback_state_dispatcher = callback_state.clone();
        let stop_flag_dispatcher = stop_flag.clone();
        let flush_interval_ms = _flush_ms.unwrap_or(20).clamp(1, 1_000);
        
        let handle = std::thread::spawn(move || {
            let mut batch = Vec::new();
            loop {
                if batch.is_empty() {
                    match rx.recv_timeout(std::time::Duration::from_millis(flush_interval_ms)) {
                        Ok(res) => batch.push(res),
                        Err(crossbeam_channel::RecvTimeoutError::Timeout) => {
                            if done_dispatcher.load(Ordering::Relaxed) && rx.is_empty() { break; }
                            continue;
                        }
                        Err(crossbeam_channel::RecvTimeoutError::Disconnected) => break,
                    }
                }
                while batch.len() < batch_size_limit {
                    match rx.try_recv() {
                        Ok(res) => batch.push(res),
                        Err(_) => break,
                    }
                }
                if !batch.is_empty() {
                    Python::with_gil(|py| {
                        let typed_batch = batch
                            .drain(..)
                            .map(|(path, matches)| (path, to_python_matches(matches)))
                            .collect::<Vec<_>>();
                        if !callback_state_dispatcher.failed.load(Ordering::Relaxed) {
                            if let Err(error) = cb_clone.bind(py).call1((typed_batch,)) {
                                callback_state_dispatcher.record_error(&stop_flag_dispatcher, "results", error);
                            }
                        }
                    });
                }
                // 중지 시에도 이미 채널에 들어온 결과는 모두 전달합니다.
                // done 플래그는 walker와 모든 worker가 송신을 마친 뒤 설정됩니다.
                if done_dispatcher.load(Ordering::Relaxed) && rx.is_empty() { break; }
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
                            is_exact, is_json, is_xml, is_excel,
                            exclude_hidden, exclude_binary, existence_only,
                            stop_flag: stop_ref.clone(),
                            max_per_file: max_per_file.unwrap_or(5000),
                            max_check_cells: max_check_cells.unwrap_or(500_000),
                            max_json_depth: max_json_depth.unwrap_or(20_000),
                            max_json_size: max_json_size.unwrap_or(DEFAULT_MAX_JSON_SIZE),
                        });

                        if let Some(r) = res {
                            let f_path = path.to_string_lossy().to_string();
                            match r {
                                Ok(matches) => {
                                    if !matches.is_empty() {
                                        if let Some(tx) = &tx_worker {
                                            let _ = tx.send((f_path, matches));
                                        } else {
                                            let mut g = res_ref.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
                                            g.push((f_path, matches));
                                        }
                                    }
                                }
                                Err(e) => {
                                    let mut s = skip_ref.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
                                    s.push((f_path, e));
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

    let final_res = to_python_file_matches(results.lock().unwrap_or_else(|poisoned| poisoned.into_inner()).clone());
    let final_skip = skipped.lock().unwrap_or_else(|poisoned| poisoned.into_inner()).clone();
    if let Some(error) = callback_state.error_message() {
        return Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(error));
    }
    Ok((final_res, final_skip))
}

#[pyfunction]
#[pyo3(signature = (file_list, search_string, mode_bits=None, exclude_hidden=false, stop_event=None, progress_callback=None, results_callback=None, batch_size=None, _flush_ms=None, max_per_file=None, max_check_cells=None, max_json_depth=None, max_json_size=None, options=None, **_kwargs))]
#[allow(clippy::too_many_arguments)]
fn search_files_list(
    py: Python<'_>,
    file_list: Vec<String>,
    search_string: String,
    mut mode_bits: Option<u32>,
    mut exclude_hidden: bool,
    mut stop_event: Option<PyObject>,
    mut progress_callback: Option<PyObject>,
    mut results_callback: Option<PyObject>,
    mut batch_size: Option<usize>,
    mut _flush_ms: Option<u64>,
    mut max_per_file: Option<usize>,
    mut max_check_cells: Option<u64>,
    mut max_json_depth: Option<usize>,
    mut max_json_size: Option<u64>,
    options: Option<Py<SearchOptions>>,
    _kwargs: Option<PyObject>,
) -> Result<(FileMatches, SkippedEntries), PyErr> {
    if let Some(config) = options.as_ref() {
        let config = config.bind(py).borrow();
        if config.mode_bits.is_some() { mode_bits = config.mode_bits; }
        exclude_hidden = config.exclude_hidden;
        if config.stop_event.is_some() { stop_event = config.stop_event.as_ref().map(|event| event.clone_ref(py)); }
        if config.progress_callback.is_some() { progress_callback = config.progress_callback.as_ref().map(|callback| callback.clone_ref(py)); }
        if config.results_callback.is_some() { results_callback = config.results_callback.as_ref().map(|callback| callback.clone_ref(py)); }
        if config.batch_size.is_some() { batch_size = config.batch_size; }
        if config.flush_ms.is_some() { _flush_ms = config.flush_ms; }
        if config.max_per_file.is_some() { max_per_file = config.max_per_file; }
        if config.max_check_cells.is_some() { max_check_cells = config.max_check_cells; }
        if config.max_json_depth.is_some() { max_json_depth = config.max_json_depth; }
        if config.max_json_size.is_some() { max_json_size = config.max_json_size; }
    }
    let stop_flag = Arc::new(AtomicBool::new(false));
    let done_flag = Arc::new(AtomicBool::new(false));
    let progress_counter = Arc::new(AtomicU64::new(0));
    let callback_state = Arc::new(CallbackState::new());

    let stop_flag_mon = stop_flag.clone();
    let done_mon = done_flag.clone();
        let stop_event_mon = stop_event.as_ref().map(|obj| obj.clone_ref(py));
        let progress_cb_mon = progress_callback.as_ref().map(|obj| obj.clone_ref(py));
        let progress_cnt_mon = progress_counter.clone();
        let callback_state_mon = callback_state.clone();

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
                        if let Err(error) = cb.bind(py).call1((progress_cnt_mon.load(Ordering::Relaxed),)) {
                            callback_state_mon.record_error(&stop_flag_mon, "progress", error);
                            return true;
                        }
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
        let batch_size_limit = batch_size.unwrap_or(100).max(1);
        let queue_capacity = batch_size_limit.saturating_mul(4).max(1);
        let (tx, rx) = crossbeam_channel::bounded::<(String, Vec<RawMatch>)>(queue_capacity);
        let cb_clone = cb.clone_ref(py);
        let done_dispatcher = done_flag.clone();
        let callback_state_dispatcher = callback_state.clone();
        let stop_flag_dispatcher = stop_flag.clone();
        let flush_interval_ms = _flush_ms.unwrap_or(20).clamp(1, 1_000);
        
        let handle = std::thread::spawn(move || {
            let mut batch = Vec::new();
            loop {
                if batch.is_empty() {
                    match rx.recv_timeout(std::time::Duration::from_millis(flush_interval_ms)) {
                        Ok(res) => batch.push(res),
                        Err(crossbeam_channel::RecvTimeoutError::Timeout) => {
                            if done_dispatcher.load(Ordering::Relaxed) && rx.is_empty() { break; }
                            continue;
                        }
                        Err(crossbeam_channel::RecvTimeoutError::Disconnected) => break,
                    }
                }
                while batch.len() < batch_size_limit {
                    match rx.try_recv() {
                        Ok(res) => batch.push(res),
                        Err(_) => break,
                    }
                }
                if !batch.is_empty() {
                    Python::with_gil(|py| {
                        let typed_batch = batch
                            .drain(..)
                            .map(|(path, matches)| (path, to_python_matches(matches)))
                            .collect::<Vec<_>>();
                        if !callback_state_dispatcher.failed.load(Ordering::Relaxed) {
                            if let Err(error) = cb_clone.bind(py).call1((typed_batch,)) {
                                callback_state_dispatcher.record_error(&stop_flag_dispatcher, "results", error);
                            }
                        }
                    });
                }
                // 중지된 검색도 이미 큐에 적재된 결과는 모두 전달합니다.
                if done_dispatcher.load(Ordering::Relaxed) && rx.is_empty() { break; }
            }
        });
        (tx, handle)
    });

    py.allow_threads(|| {
        let norm_pattern = crate::utils::normalize_unicode(&search_string);
        let pat_upper = norm_pattern.to_lowercase().to_uppercase();
        let pat_bytes_v = norm_pattern.to_lowercase().as_bytes().to_vec();
        let (is_json, is_xml, is_exact, is_excel, exclude_binary, existence_only) = parse_search_mode(mode_bits);
        let patterns = generate_search_patterns(&norm_pattern, is_xml, is_json);

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
                ac: &ac, is_exact, is_json, is_xml, is_excel,
                exclude_hidden, exclude_binary, existence_only,
                stop_flag: stop_flag.clone(),
                max_per_file: max_per_file.unwrap_or(5000),
                max_check_cells: max_check_cells.unwrap_or(500_000),
                max_json_depth: max_json_depth.unwrap_or(20_000),
                max_json_size: max_json_size.unwrap_or(DEFAULT_MAX_JSON_SIZE),
            });

            if let Some(r) = res {
                match r {
                    Ok(m) => {
                        if let Some(tx) = &tx_main { let _ = tx.send((f_path, m)); }
                        else {
                            let mut g = results.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
                            g.push((f_path, m));
                        }
                    }
                    Err(e) => {
                        let mut g = skipped.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
                        g.push((f_path, e));
                    }
                }
            }
            progress_counter.fetch_add(1, Ordering::Relaxed);
        });

        // B1: done_flag를 먼저 설정하여 dispatcher와 monitor 스레드가 종료 루프에 진입할 수 있도록 합니다.
        done_flag.store(true, Ordering::SeqCst);
        if let Some((_, handle)) = results_dispatcher { let _ = handle.join(); }
        if let Some(h) = monitor_handle { let _ = h.join(); }
        let final_res = to_python_file_matches(results.lock().unwrap_or_else(|poisoned| poisoned.into_inner()).clone());
        let final_skip = skipped.lock().unwrap_or_else(|poisoned| poisoned.into_inner()).clone();
        if let Some(error) = callback_state.error_message() {
            return Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(error));
        }
        Ok((final_res, final_skip))
    })
}

#[pyfunction]
#[pyo3(signature = (paths, keyword, extensions=None, mode_bits=None, filename_filter=None, exclude_hidden=false, stop_event=None, results_callback=None, max_json_depth=20000, max_json_size=524288000, options=None))]
#[allow(clippy::too_many_arguments)]
fn find_files_with_keyword(
    py: Python<'_>,
    paths: Vec<String>,
    keyword: String,
    mut extensions: Option<Vec<String>>,
    mut mode_bits: Option<u32>,
    mut filename_filter: Option<Vec<String>>,
    mut exclude_hidden: bool,
    mut stop_event: Option<PyObject>,
    mut results_callback: Option<PyObject>,
    mut max_json_depth: usize,
    mut max_json_size: u64,
    options: Option<Py<SearchOptions>>,
) -> Result<(KeywordFileHits, SkippedEntries), PyErr> {
    if let Some(config) = options.as_ref() {
        let config = config.bind(py).borrow();
        if config.extensions.is_some() { extensions = config.extensions.clone(); }
        if config.mode_bits.is_some() { mode_bits = config.mode_bits; }
        if config.filename_filter.is_some() { filename_filter = config.filename_filter.clone(); }
        exclude_hidden = config.exclude_hidden;
        if config.stop_event.is_some() { stop_event = config.stop_event.as_ref().map(|event| event.clone_ref(py)); }
        if config.results_callback.is_some() { results_callback = config.results_callback.as_ref().map(|callback| callback.clone_ref(py)); }
        if config.max_json_depth.is_some() { max_json_depth = config.max_json_depth.unwrap_or(max_json_depth); }
        if config.max_json_size.is_some() { max_json_size = config.max_json_size.unwrap_or(max_json_size); }
    }
    let (is_json, is_xml, is_exact, _is_excel, exclude_binary, _existence_only) = parse_search_mode(mode_bits);
    let norm_keyword = crate::utils::normalize_unicode(&keyword);
    let patterns = generate_search_patterns(&norm_keyword, is_xml, is_json);
    let ac_shared = Arc::new(AhoCorasickBuilder::new()
        .ascii_case_insensitive(true)
        .match_kind(MatchKind::LeftmostFirst)
        .build(&patterns)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("{}", e)))?);

    let stop_flag = Arc::new(AtomicBool::new(false));
    let is_done = Arc::new(AtomicBool::new(false));
    let callback_state = Arc::new(CallbackState::new());
    let stop_flag_mon = stop_flag.clone();
    let is_done_mon = is_done.clone();
    let stop_evt_mon = stop_event.as_ref().map(|obj| obj.clone_ref(py));

    let monitor_handle = if stop_event.is_some() {
        Some(std::thread::spawn(move || {
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
        }))
    } else {
        None
    };

    // M1: `_results_dispatcher` → `results_dispatcher` (언더스코어 제거)
    // `_` prefix 변수는 Rust 컴파일러에 의해 즉시 drop되므로 tx도 함께 소멸되어 콜백 스레드가 동작하지 않았음.
    let results_dispatcher = results_callback.as_ref().map(|cb| {
        let batch_size_limit = 100usize;
        let queue_capacity = batch_size_limit.saturating_mul(4).max(1);
        let (tx, rx) = crossbeam_channel::bounded::<(String, u64)>(queue_capacity);
        let cb_clone = cb.clone_ref(py);
        let done_dispatcher = is_done.clone();
        let callback_state_dispatcher = callback_state.clone();
        let stop_flag_dispatcher = stop_flag.clone();
        
        let handle = std::thread::spawn(move || {
            let mut batch = Vec::new();
            loop {
                if batch.is_empty() {
                    match rx.recv_timeout(std::time::Duration::from_millis(20)) {
                        Ok(res) => batch.push(res),
                        Err(crossbeam_channel::RecvTimeoutError::Timeout) => {
                            if done_dispatcher.load(Ordering::Relaxed) && rx.is_empty() { break; }
                            continue;
                        }
                        Err(crossbeam_channel::RecvTimeoutError::Disconnected) => break,
                    }
                }
                while batch.len() < batch_size_limit {
                    match rx.try_recv() {
                        Ok(res) => batch.push(res),
                        Err(_) => break,
                    }
                }
                if !batch.is_empty() {
                    Python::with_gil(|py| {
                        if !callback_state_dispatcher.failed.load(Ordering::Relaxed) {
                            if let Err(error) = cb_clone.bind(py).call1((std::mem::take(&mut batch),)) {
                                callback_state_dispatcher.record_error(&stop_flag_dispatcher, "results", error);
                            }
                        } else {
                            batch.clear();
                        }
                    });
                }
                if done_dispatcher.load(Ordering::Relaxed) && rx.is_empty() { break; }
            }
        });
        (tx, handle)
    });

    py.allow_threads(|| {
        let results = Arc::new(Mutex::new(Vec::new()));
        let skipped = Arc::new(Mutex::new(Vec::<(String, String)>::new()));
        let exts = extensions.map(|v| v.iter().map(|s| s.trim_start_matches('.').to_lowercase()).collect::<HashSet<String>>());
        let glob_set = build_glob_set(&filename_filter.unwrap_or_default());

        paths.into_par_iter().for_each(|root| {
            let mut builder = WalkBuilder::new(&root);
            builder.hidden(exclude_hidden).ignore(false).git_ignore(false);
            let walker = builder.build_parallel();
            let res_ref = Arc::clone(&results);
            let skipped_ref = Arc::clone(&skipped);
            let ac_ref = Arc::clone(&ac_shared);
            let stop_ref = Arc::clone(&stop_flag);
            let kw_orig = norm_keyword.clone();
            let ext_s = exts.clone();
            let glob_s = glob_set.clone();
            // M1: results_dispatcher에서 tx 채널 추출
            let tx_kw = results_dispatcher.as_ref().map(|(tx, _)| tx.clone());

            walker.run(move || {
                let res_inner = Arc::clone(&res_ref);
                let skipped_inner = Arc::clone(&skipped_ref);
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

                        let is_json_file = is_json && path.extension().is_some_and(|e| e.eq_ignore_ascii_case("json"));
                        if is_json_file && f_size > max_json_size {
                            let f_path = path.to_string_lossy().to_string();
                            let mut g = skipped_inner.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
                            g.push((f_path, encode_skip_reason(REASON_ERR_MEMORY_GUARD, "Large JSON")));
                            return ignore::WalkState::Continue;
                        }

                        let f_path = path.to_string_lossy().to_string();
                        let snapshot = match load_file_snapshot(file, f_size, meta.modified().ok()) {
                            Ok(snapshot) => snapshot,
                            Err(error) => {
                                let mut g = skipped_inner.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
                                g.push((f_path, encode_skip_reason(REASON_ERR_MMAP, error)));
                                return ignore::WalkState::Continue;
                            }
                        };
                        let bytes = snapshot.as_slice();
                        let is_match = if is_json_file {
                                 let encoding = detect_encoding(bytes);
                                 let decoded = if encoding == UTF_8 {
                                     None
                                 } else {
                                     Some(decode_bytes(bytes, encoding))
                                 };
                                 let searchable = decoded
                                     .as_ref()
                                     .map(|value| value.as_bytes())
                                     .unwrap_or(bytes);
                                 check_json_file(searchable, &kw_inner, &ac_inner, is_exact, stop_inner.clone(), max_json_depth)
                             } else if is_xml && path.extension().is_some_and(|e| e.eq_ignore_ascii_case("xml")) {
                                  let encoding = detect_encoding(bytes);
                                 let decoded = if encoding == UTF_8 {
                                     None
                                 } else {
                                      Some(decode_bytes(bytes, encoding))
                                 };
                                 let searchable = decoded
                                     .as_ref()
                                     .map(|value| value.as_bytes())
                                      .unwrap_or(bytes);
                                 check_xml_file(searchable, &kw_inner, &ac_inner, is_exact, stop_inner.clone())
                             } else if exclude_binary && is_binary(bytes) {
                                 false
                             } else {
                                 ac_inner.find(bytes).is_some()
                             };

                            if is_match {
                                let f_path = path.to_string_lossy().to_string();
                                // M1: tx 채널이 있으면 스트리밍, 없으면 공유 벡터에 직접 저장
                                if let Some(tx) = &tx_inner {
                                    let _ = tx.send((f_path, f_size));
                                } else {
                                    let mut g = res_inner.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
                                    g.push((f_path, f_size));
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
        if let Some(handle) = monitor_handle { let _ = handle.join(); }
        let final_res = results.lock().unwrap_or_else(|poisoned| poisoned.into_inner()).clone();
        let final_skipped = skipped.lock().unwrap_or_else(|poisoned| poisoned.into_inner()).clone();
        if let Some(error) = callback_state.error_message() {
            return Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(error));
        }
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

    m.add_class::<SearchMatch>()?;
    m.add_class::<SearchOptions>()?;
    m.add_function(wrap_pyfunction!(search_file, m)?)?;
    m.add_function(wrap_pyfunction!(search_dir, m)?)?;
    m.add_function(wrap_pyfunction!(search_files_list, m)?)?;
    m.add_function(wrap_pyfunction!(find_files_with_keyword, m)?)?;
    m.add("API_VERSION", 6)?;
    // Cargo.toml version 필드를 빌드 시점에 자동으로 읽어 Python 측에 노출합니다.
    m.add("ENGINE_VERSION", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}


fn extract_line_content_bytes(
    mmap: &[u8],
    start: usize,
    end: usize,
    match_start: Option<usize>,
    match_len: Option<usize>,
) -> String {
    let mut line_end = end;
    if line_end > start && mmap[line_end - 1] == b'\r' {
        line_end -= 1;
    }
    let line_len = line_end - start;
    if line_len > 4096 {
        const PREVIEW_MAX_BYTES: usize = 1024;
        let mut preview_start = start;
        let mut preview_end = (start + PREVIEW_MAX_BYTES).min(line_end);

        // 긴 줄은 매치 위치 주변을 우선 표시하여 검색어가 미리보기에서 사라지지 않게 합니다.
        if let Some(hit_start) = match_start {
            let hit_start = hit_start.clamp(start, line_end);
            let hit_len = match_len.unwrap_or(0).min(line_end.saturating_sub(hit_start));
            let desired_start = hit_start.saturating_sub(PREVIEW_MAX_BYTES / 2);
            preview_start = desired_start.max(start);
            preview_end = (preview_start + PREVIEW_MAX_BYTES).min(line_end);
            if preview_end < hit_start.saturating_add(hit_len) {
                preview_end = (hit_start.saturating_add(hit_len)).min(line_end);
                preview_start = preview_end.saturating_sub(PREVIEW_MAX_BYTES).max(start);
            }
        }

        // UTF-8 문자의 중간을 자르지 않도록 경계를 보정합니다.
        while preview_start > start && (mmap[preview_start] & 0xC0) == 0x80 { preview_start -= 1; }
        while preview_end < line_end && (mmap[preview_end] & 0xC0) == 0x80 { preview_end += 1; }

        let preview_bytes = &mmap[preview_start..preview_end];
        let preview_text = match simdutf8::basic::from_utf8(preview_bytes) {
            Ok(s) => s.trim().to_string(),
            Err(_) => String::from_utf8_lossy(preview_bytes).trim().to_string(),
        };
        let prefix = if preview_start > start { "..." } else { "" };
        let suffix = if preview_end < line_end { "..." } else { "" };
        return format!("__SF_LONG_LINE__|{}{}{}", prefix, preview_text, suffix);
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
        let matches = do_search_with_mmap(data, UTF_8, "hello", &ac, false, true, &stop_flag, 5000);
        assert_eq!(matches.len(), 1);
    }

    #[test]
    fn long_line_preview_includes_match_region() {
        let ac = build_test_ac("needle");
        let stop_flag = Arc::new(AtomicBool::new(false));
        let mut data = vec![b'A'; 12_000];
        data[8_000..8_006].copy_from_slice(b"needle");
        let matches = do_search_with_mmap(&data, UTF_8, "needle", &ac, false, false, &stop_flag, 5000);
        assert_eq!(matches.len(), 1);
        assert!(matches[0].1.contains("needle"));
    }

    #[test]
    fn match_limit_returns_only_allowed_matches_and_a_marker() {
        let matches = vec![
            (1, "one".to_string(), None, None),
            (2, "two".to_string(), None, None),
            (3, "three".to_string(), None, None),
        ];
        let limited = apply_match_limit(matches, 2);

        assert_eq!(limited.len(), 3);
        assert_eq!(limited[0].1, "one");
        assert_eq!(limited[1].1, "two");
        assert_eq!(limited[2].1, MATCH_META_TRUNCATED);
    }

    #[test]
    fn exact_match_handles_final_carriage_return() {
        let ac = build_test_ac("needle");
        let stop_flag = Arc::new(AtomicBool::new(false));
        let data = b"prefix\nneedle\r";
        let matches = do_search_with_mmap(data, UTF_8, "NEEDLE", &ac, true, false, &stop_flag, 5000);
        assert_eq!(matches.len(), 1);
        assert_eq!(matches[0].0, 2);
    }

    #[test]
    fn no_bom_utf16_is_treated_as_text_not_binary() {
        let mut data = Vec::new();
        for unit in "needle".encode_utf16() {
            data.extend_from_slice(&unit.to_le_bytes());
        }
        let encoding = detect_encoding(&data);
        assert!(encoding != UTF_8);
        assert_eq!(encoding, UTF_16LE);
        assert!(!(encoding == UTF_8 && is_binary(&data)));

        let ac = build_test_ac("needle");
        let stop_flag = Arc::new(AtomicBool::new(false));
        let matches = do_search_with_mmap(&data, encoding, "NEEDLE", &ac, false, false, &stop_flag, 5000);
        assert_eq!(matches.len(), 1);
        assert!(matches[0].1.contains("needle"));
    }
}
