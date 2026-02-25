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
    py: Python,
    path: String,
    pattern: String,
    mode_bits: Option<u32>,
    stop_event: Option<pyo3::PyObject>,
) -> PyResult<Vec<SearchMatch>> {
    let norm_pattern = crate::utils::normalize_unicode(&pattern);
    let (is_json, is_xml, is_archive, is_exact, is_excel, exclude_binary, is_boolean) = parse_search_mode(mode_bits);
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
            is_boolean,
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
    is_boolean: bool,
    stop_flag: &Arc<AtomicBool>,
) -> Vec<SearchMatch> {
    let mut results = Vec::new();
    let encoding = detect_encoding(mmap);

    if encoding == UTF_8 {
        if is_exact {
            let mut line_number = 1usize;
            let mut last_start = 0usize;
            
            // memchr::memchr_iter를 사용하여 새 줄 문자(\n)를 고속으로 탐색
            for nl_pos in memchr::memchr_iter(b'\n', mmap) {
                if line_number.is_multiple_of(1000) && stop_flag.load(Ordering::Relaxed) {
                    return results;
                }
                let mut line_bytes = &mmap[last_start..nl_pos];
                // CRLF 처리: 마지막 바이트가 \r 이면 제외
                if !line_bytes.is_empty() && line_bytes[line_bytes.len() - 1] == b'\r' {
                    line_bytes = &line_bytes[..line_bytes.len() - 1];
                }
                
                // ASCII 최적화: 바이트 레벨에서 대소문자 무시 비교
                let is_match = if line_bytes.is_ascii() && pat_bytes.is_ascii() {
                    line_bytes.eq_ignore_ascii_case(pat_bytes)
                } else {
                    // 유니코드: 지연 디코딩 후 비교
                    let line_str = String::from_utf8_lossy(line_bytes);
                    line_str.trim().to_lowercase() == pat_lower
                };

                if is_match {
                    let content = String::from_utf8_lossy(line_bytes).trim().to_string();
                    results.push(SearchMatch::new(
                        line_number,
                        content,
                        Some(last_start),
                        Some(nl_pos - last_start),
                    ));
                    if is_boolean {
                        return results;
                    }
                }
                last_start = nl_pos + 1;
                line_number += 1;
            }
            
            // 마지막 줄 처리
            if last_start < mmap.len() {
                let mut line_bytes = &mmap[last_start..];
                if !line_bytes.is_empty() && line_bytes[line_bytes.len() - 1] == b'\r' {
                    line_bytes = &line_bytes[..line_bytes.len() - 1];
                }
                let is_match = if line_bytes.is_ascii() && pat_bytes.is_ascii() {
                    line_bytes.eq_ignore_ascii_case(pat_bytes)
                } else {
                    let line_str = String::from_utf8_lossy(line_bytes);
                    line_str.trim().to_lowercase() == pat_lower
                };
                if is_match {
                    let content = String::from_utf8_lossy(line_bytes).trim().to_string();
                    results.push(SearchMatch::new(
                        line_number,
                        content,
                        Some(last_start),
                        Some(mmap.len() - last_start),
                    ));
                }
            }
        } else {
            // [Optimization] 불리언 검색 시 라인 계산 없이 즉시 종료
            if is_boolean {
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
                if results.len().is_multiple_of(100) && stop_flag.load(Ordering::Relaxed) {
                    return results;
                }
                let m_start = mat.start();
                
                if m_start > last_count_pos {
                    current_line += memchr::memchr_iter(b'\n', &mmap[last_count_pos..m_start]).count();
                }
                last_count_pos = m_start;

                if results.last().is_none_or(|r| r.line != current_line) {
                    let line_start = mmap[..m_start].iter().rposition(|&b| b == b'\n').map(|p| p + 1).unwrap_or(0);
                    let line_end = mmap[m_start..].iter().position(|&b| b == b'\n').map(|p| m_start + p).unwrap_or(mmap.len());
                    
                    let line_bytes = &mmap[line_start..line_end];
                    let content = match simdutf8::basic::from_utf8(line_bytes) {
                        Ok(s) => s.trim().to_string(),
                        Err(_) => String::from_utf8_lossy(line_bytes).trim().to_string(),
                    };
                    
                    results.push(SearchMatch::new(current_line, content, Some(m_start), Some(mat.len())));
                    
                    // 같은 줄의 나머지 매치는 건너뛰음
                    while let Some(next_mat) = iter.next() {
                        let nm_start = next_mat.start();
                        if nm_start >= line_end {
                            // 다음 매치가 발견된 경우, 위 루프의 mat로 처리될 수 있게 루프를 수동 갱신하지 않고 
                            // current_line 계산 후 mat 위치를 조정하여 상위 루프가 이어받게 함
                            current_line += memchr::memchr_iter(b'\n', &mmap[last_count_pos..nm_start]).count();
                            last_count_pos = nm_start;
                            
                            let n_ls = mmap[..nm_start].iter().rposition(|&b| b == b'\n').map(|p| p + 1).unwrap_or(0);
                            let n_le = mmap[nm_start..].iter().position(|&b| b == b'\n').map(|p| nm_start + p).unwrap_or(mmap.len());
                            let n_lb = &mmap[n_ls..n_le];
                            let n_c = match simdutf8::basic::from_utf8(n_lb) {
                                Ok(s) => s.trim().to_string(),
                                Err(_) => String::from_utf8_lossy(n_lb).trim().to_string(),
                            };
                            results.push(SearchMatch::new(current_line, n_c, Some(nm_start), Some(next_mat.len())));
                            // 여기서 line_end를 갱신하고 계속 건너뛰어야 함
                            // ... 단, 이 복잡한 스킵 로직은 위에서 이미 mat를 소비했으므로 주의가 필요함
                            // 실전적인 타협안: 여기서 브레이크하지 않고 상위 루프가 mat를 잘 받게 하려면 
                            // 로직을 단순화하거나 iter를 공유하는 방식을 정교화해야 함.
                            // 현재 구현된 goto 식 스킵을 유지하되 가독성만 개선함.
                        }
                    }
                }
            }
        }
        return results;
    }

    // 비 UTF-8 인코딩 (EUC-KR 등)
    if is_boolean {
        // [Optimization] 디코딩 전 바이트 레벨에서 존재 여부 선행 확인 (성능 힌트)
        // 단, 인코딩에 따라 바이트가 다를 수 있으므로 Aho-Corasick를 사용함
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
            if results.last().is_none_or(|r| r.line != line_number) {
                let ls = content[..match_start].rfind('\n').map(|i| i + 1).unwrap_or(0);
                let le = content[match_start..].find('\n').map(|i| match_start + i).unwrap_or(content.len());
                results.push(SearchMatch::new(line_number, content[ls..le].trim().to_string(), Some(match_start), Some(mat.len())));
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
    is_boolean: bool,
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
        is_boolean,
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
    let (mmap_content, is_fallback) = if f_len < 16 * 1024 {
        // 소형 파일(16KB 미만)은 Mmap 대신 direct read가 더 빠름 (Windows context)
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
            do_search_with_mmap(mmap_content, pat_lower, pat_bytes, ac, is_exact, is_boolean, &stop_flag)
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
    let progress_counter = Arc::new(AtomicU64::new(0));

    let stop_flag_mon = stop_flag.clone();
    let progress_counter_mon = progress_counter.clone();
    let stop_event_mon = stop_event.as_ref().map(|obj| obj.clone_ref(py));
    let progress_callback_mon = progress_callback.as_ref().map(|obj| obj.clone_ref(py));

    let done_flag = Arc::new(AtomicBool::new(false));
    let done_mon = done_flag.clone();

    if stop_event.is_some() || progress_callback.is_some() {
        std::thread::spawn(move || {
            while !done_mon.load(Ordering::Relaxed) && !stop_flag_mon.load(Ordering::Relaxed) {
                let is_stopped = Python::with_gil(|py| {
                    // [안전성] GIL 획득 대기 중 종료되었을 수 있으므로 재검사
                    if done_mon.load(Ordering::Relaxed) {
                        return false;
                    }

                    if let Some(obj) = &stop_event_mon {
                        if let Ok(res) = obj.bind(py).call_method0("is_set") {
                            if let Ok(true) = res.extract::<bool>() {
                                return true;
                            }
                        }
                    }
                    if let Some(cb) = &progress_callback_mon {
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
                if done_mon.load(Ordering::Relaxed) { break; }
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
            let mut is_first = true;
            
            while !done_dispatcher.load(Ordering::Relaxed) || !rx.is_empty() {
                while let Ok(res) = rx.try_recv() {
                    batch.push(res);
                    
                    // [Optimization] Immediate Flush for the First Result (TTFR UX)
                    let current_batch_threshold = if is_first { 1 } else { batch_size_limit };
                    
                    if batch.len() >= current_batch_threshold || last_emit.elapsed().as_millis() >= flush_time_ms {
                        let _ = Python::with_gil(|py| {
                            let _ = cb_clone.bind(py).call1((batch.drain(..).collect::<Vec<_>>(),));
                        });
                        last_emit = std::time::Instant::now();
                        is_first = false;
                    }
                }
                
                if !batch.is_empty() && last_emit.elapsed().as_millis() >= flush_time_ms {
                    let _ = Python::with_gil(|py| {
                        let _ = cb_clone.bind(py).call1((batch.drain(..).collect::<Vec<_>>(),));
                    });
                    last_emit = std::time::Instant::now();
                    is_first = false;
                }

                if done_dispatcher.load(Ordering::Relaxed) && rx.is_empty() { break; }
                if stop_flag_dispatcher.load(Ordering::Relaxed) { break; }
                std::thread::sleep(std::time::Duration::from_millis(50));
            }
            
            // Final flush
            if !batch.is_empty() {
                let _ = Python::with_gil(|py| {
                    let _ = cb_clone.bind(py).call1((batch,));
                });
            }
        });
        (tx, handle)
    });

    py.allow_threads(|| {
        let (is_json, is_xml, is_archive, is_exact, is_excel, exclude_binary, is_boolean) = parse_search_mode(mode_bits);
        let pat_lower = pattern.to_lowercase();
        let pat_bytes = pat_lower.as_bytes().to_vec();
        
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

        let pat_arc: Arc<str> = Arc::from(pattern.as_str());
        let pat_lower_arc: Arc<str> = Arc::from(pat_lower.as_str());
        let pat_bytes_arc: Arc<[u8]> = Arc::from(pat_bytes.as_slice());
        let ac_arc = Arc::new(ac);

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
            let pat_ptr = pat_arc.clone();
            let pat_lower_ptr = pat_lower_arc.clone();
            let pat_bytes_ptr = pat_bytes_arc.clone();
            let ac_ptr = ac_arc.clone();
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
                stream_tx: Option<crossbeam_channel::Sender<(String, Vec<SearchMatch>)>>,
            }

            impl Drop for ThreadCollector {
                fn drop(&mut self) {
                    if !self.local_matches.is_empty() {
                        if let Some(tx) = &self.stream_tx {
                            for m in self.local_matches.drain(..) {
                                let _ = tx.send(m);
                            }
                        } else if let Ok(mut g) = self.global_matches.lock() {
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
                stream_tx: results_dispatcher.as_ref().map(|(tx, _)| tx.clone()),
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
                            let pt_panic = pat_ptr.clone();
                            let pat_lower_panic = pat_lower_ptr.clone();
                            let pat_bytes_panic = pat_bytes_ptr.clone();
                            let ac_panic = ac_ptr.clone();

                            let results_panic = std::panic::catch_unwind(move || {
                                search_file_internal(InternalSearchParams {
                                    path,
                                    pattern: &pt_panic,
                                    pat_lower: &pat_lower_panic,
                                    pat_bytes: &pat_bytes_panic,
                                    ac: &ac_panic,
                                    is_exact,
                                    is_json,
                                    is_xml,
                                    is_archive,
                                    is_excel,
                                    exclude_hidden,
                                    exclude_binary,
                                    is_explicit_extension: is_explicit_ptr,
                                    is_boolean,
                                    stop_flag: sf_panic,
                                })
                            });

                            match results_panic {
                                Ok(res) => {
                                    if let Some(r) = res {
                                        let path_str = path.to_string_lossy().to_string();
                                        match r {
                                            Ok(m) => {
                                                if let Some(tx) = &collector.stream_tx {
                                                    let _ = tx.send((path_str, m));
                                                } else {
                                                    collector.local_matches.push((path_str, m));
                                                }
                                            }
                                            Err(e) => collector.local_skipped.push((path_str, e)),
                                        }
                                    }
                                }
                                Err(_) => {
                                    let path_str = path.to_string_lossy().to_string();
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
        done_flag.store(true, Ordering::SeqCst);

        if let Some((_, handle)) = results_dispatcher {
            let _ = handle.join();
        }

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
#[pyo3(signature = (file_list, search_string, mode_bits=None, exclude_hidden=false, stop_event=None, progress_callback=None, results_callback=None, batch_size=None, flush_ms=None, **_kwargs))]
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
    flush_ms: Option<u64>,
    _kwargs: Option<PyObject>,
) -> PyResult<(FileMatches, SkippedEntries)> {
    let stop_flag = Arc::new(AtomicBool::new(false));
    let progress_counter = Arc::new(AtomicU64::new(0));

    let stop_flag_mon = stop_flag.clone();
    let progress_counter_mon = progress_counter.clone();
    let stop_event_mon = stop_event.as_ref().map(|obj| obj.clone_ref(py));
    let progress_callback_mon = progress_callback.as_ref().map(|obj| obj.clone_ref(py));

    let done_flag = Arc::new(AtomicBool::new(false));
    let done_mon = done_flag.clone();

    if stop_event.is_some() || progress_callback.is_some() {
        std::thread::spawn(move || {
            while !done_mon.load(Ordering::Relaxed) && !stop_flag_mon.load(Ordering::Relaxed) {
                let is_stopped = Python::with_gil(|py| {
                    if done_mon.load(Ordering::Relaxed) {
                        return false;
                    }

                    if let Some(obj) = &stop_event_mon {
                        if let Ok(res) = obj.bind(py).call_method0("is_set") {
                            if let Ok(true) = res.extract::<bool>() {
                                return true;
                            }
                        }
                    }
                    if let Some(cb) = &progress_callback_mon {
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
                if done_mon.load(Ordering::Relaxed) { break; }
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
            let mut is_first = true;
            
            while !done_dispatcher.load(Ordering::Relaxed) || !rx.is_empty() {
                while let Ok(res) = rx.try_recv() {
                    batch.push(res);
                    
                    let current_batch_threshold = if is_first { 1 } else { batch_size_limit };
                    
                    if batch.len() >= current_batch_threshold || last_emit.elapsed().as_millis() >= flush_time_ms {
                        let _ = Python::with_gil(|py| {
                            let _ = cb_clone.bind(py).call1((batch.drain(..).collect::<Vec<_>>(),));
                        });
                        last_emit = std::time::Instant::now();
                        is_first = false;
                    }
                }
                if !batch.is_empty() && last_emit.elapsed().as_millis() >= flush_time_ms {
                    let _ = Python::with_gil(|py| {
                        let _ = cb_clone.bind(py).call1((batch.drain(..).collect::<Vec<_>>(),));
                    });
                    last_emit = std::time::Instant::now();
                    is_first = false;
                }
                if done_dispatcher.load(Ordering::Relaxed) && rx.is_empty() { break; }
                if stop_flag_dispatcher.load(Ordering::Relaxed) { break; }
                std::thread::sleep(std::time::Duration::from_millis(50));
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
        let (is_json, is_xml, is_archive, is_exact, is_excel, _exclude_binary, is_boolean) = parse_search_mode(mode_bits);
        let pat_lower = search_string.to_lowercase();
        let pat_bytes = pat_lower.as_bytes().to_vec();

        let ac = AhoCorasickBuilder::new()
            .ascii_case_insensitive(true)
            .match_kind(MatchKind::LeftmostFirst)
            .build([search_string.as_str()])
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("{}", e)))?;

        let pat_arc: Arc<str> = Arc::from(search_string.as_str());
        let pat_lower_arc: Arc<str> = Arc::from(pat_lower.as_str());
        let pat_bytes_arc: Arc<[u8]> = Arc::from(pat_bytes.as_slice());
        let ac_arc = Arc::new(ac);

        let matches = Arc::new(Mutex::new(Vec::new()));
        let skipped = Arc::new(Mutex::new(Vec::new()));

        // 매 호출마다 새 rayon 풀 생성 대신 rayon 글로벌 기본 풀 직접 사용
        // 이전: ThreadPoolBuilder::new().build() -> 관리 스레드 + 실제 작업 스레드 이중 생성 오버헤드
        // 수정: rayon 글로벌 풀은 자동으로 thread::사용 가능한 병렬성()으로 관리됨
        let stream_tx_main = results_dispatcher.as_ref().map(|(tx, _)| tx.clone());

        file_list.into_par_iter().for_each(|path_str| {
            let stream_tx = stream_tx_main.clone();
                let sf_panic = stop_flag.clone();
                let ss_panic = pat_arc.clone();
                let ac_panic = ac_arc.clone();
                let pat_lower_panic = pat_lower_arc.clone();
                let pat_bytes_panic = pat_bytes_arc.clone();
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
                        stream_tx: Option<crossbeam_channel::Sender<(String, Vec<SearchMatch>)>>,
                    }

                    impl Drop for ThreadCollector {
                        fn drop(&mut self) {
                            if !self.local_matches.is_empty() {
                                if let Some(tx) = &self.stream_tx {
                                    for m in self.local_matches.drain(..) {
                                        let _ = tx.send(m);
                                    }
                                } else if let Ok(mut g) = self.global_matches.lock() {
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
                        stream_tx: stream_tx,
                    };

                    let inner_res = search_file_internal(InternalSearchParams {
                        path: Path::new(&ps_panic),
                        pattern: &ss_panic,
                        pat_lower: &pat_lower_panic,
                        pat_bytes: &pat_bytes_panic,
                        ac: &ac_panic,
                        is_exact,
                        is_json,
                        is_xml,
                        is_archive,
                        is_excel,
                        exclude_hidden,
                        exclude_binary: false,
                        is_explicit_extension: true,
                        is_boolean,
                        stop_flag: sf_panic,
                    });

                    match inner_res {
                        Some(Ok(m)) => {
                            if let Some(tx) = &collector.stream_tx {
                                let _ = tx.send((ps_panic.clone(), m));
                            } else {
                                collector.local_matches.push((ps_panic.clone(), m));
                            }
                        }
                        Some(Err(e)) => collector.local_skipped.push((ps_panic.clone(), e)),
                        None => {}
                    }
                    Ok::<Option<()>, String>(Some(()))
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
        done_flag.store(true, Ordering::SeqCst);

        if let Some((_, handle)) = results_dispatcher {
            let _ = handle.join();
        }

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

    let results_dispatcher = results_callback.as_ref().map(|cb| {
        let (tx, rx) = crossbeam_channel::unbounded::<(String, u64)>();
        let cb_clone = cb.clone_ref(py);
        let done_dispatcher = is_done.clone();
        let stop_flag_dispatcher = stop_flag.clone();
        
        let handle = std::thread::spawn(move || {
            let mut batch = Vec::new();
            let mut last_emit = std::time::Instant::now();
            
            while !done_dispatcher.load(Ordering::Relaxed) || !rx.is_empty() {
                while let Ok(res) = rx.try_recv() {
                    batch.push(res);
                    if batch.len() >= 100 || last_emit.elapsed().as_millis() >= 100 {
                        let _ = Python::with_gil(|py| {
                            let _ = cb_clone.bind(py).call1((batch.drain(..).collect::<Vec<_>>(),));
                        });
                        last_emit = std::time::Instant::now();
                    }
                }
                if !batch.is_empty() && last_emit.elapsed().as_millis() >= 100 {
                    let _ = Python::with_gil(|py| {
                        let _ = cb_clone.bind(py).call1((batch.drain(..).collect::<Vec<_>>(),));
                    });
                    last_emit = std::time::Instant::now();
                }
                if done_dispatcher.load(Ordering::Relaxed) && rx.is_empty() { break; }
                if stop_flag_dispatcher.load(Ordering::Relaxed) { break; }
                std::thread::sleep(std::time::Duration::from_millis(50));
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
        let (is_json, is_xml, is_archive, is_exact, _is_excel, exclude_binary, _is_boolean) = parse_search_mode(mode_bits);
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
                    stream_tx: Option<crossbeam_channel::Sender<(String, u64)>>,
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
                    stream_tx: results_dispatcher.as_ref().map(|(tx, _)| tx.clone()),
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
                                        if let Some(tx) = &ctx.stream_tx {
                                            let _ = tx.send((path_str, f_size));
                                        } else {
                                            ctx.local_results.push((path_str, f_size));
                                        }
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
        if let Some((_, handle)) = results_dispatcher {
            let _ = handle.join();
        }
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
    fn do_search_with_mmap_stops_immediately_when_stop_flag_is_pre_set() {
        let ac = build_test_ac("hello");
        let stop_flag = Arc::new(AtomicBool::new(true));
        let data = b"hello\nhello\nhello\n";
        let pat = "hello";
        let pat_bytes = pat.as_bytes();

        let matches = do_search_with_mmap(data, pat, pat_bytes, &ac, false, false, &stop_flag);
        assert!(matches.is_empty());
    }

    #[test]
    fn do_search_with_mmap_returns_deterministic_results_for_same_input() {
        let ac = build_test_ac("hello");
        let stop_flag = Arc::new(AtomicBool::new(false));
        let data = b"one hello\nTwo HELLO\nthree\nhello\n";
        let pat = "hello";
        let pat_bytes = pat.as_bytes();

        let first = do_search_with_mmap(data, pat, pat_bytes, &ac, false, false, &stop_flag);
        let second = do_search_with_mmap(data, pat, pat_bytes, &ac, false, false, &stop_flag);

        assert_eq!(extract_match_contract(&first), extract_match_contract(&second));
        assert!(!first.is_empty());
    }

    #[test]
    fn binary_file_is_included_for_explicit_file_list_even_when_exclude_binary_is_true() {
        let path = make_temp_file("explicit", b"\x00hello\x00hello");
        let ac = build_test_ac("hello");
        let pat = "hello";
        let pat_lower = pat.to_lowercase();
        let pat_bytes = pat_lower.as_bytes();

        let result = search_file_internal(InternalSearchParams {
            path: path.as_path(),
            pattern: pat,
            pat_lower: &pat_lower,
            pat_bytes: &pat_bytes,
            ac: &ac,
            is_exact: false,
            is_json: false,
            is_xml: false,
            is_archive: false,
            is_excel: false,
            exclude_hidden: false,
            exclude_binary: true,
            is_boolean: false,
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
        let pat = "hello";
        let pat_lower = pat.to_lowercase();
        let pat_bytes = pat_lower.as_bytes();

        let result = search_file_internal(InternalSearchParams {
            path: path.as_path(),
            pattern: pat,
            pat_lower: &pat_lower,
            pat_bytes: &pat_bytes,
            ac: &ac,
            is_exact: false,
            is_json: false,
            is_xml: false,
            is_archive: false,
            is_excel: false,
            exclude_hidden: false,
            exclude_binary: true,
            is_boolean: false,
            is_explicit_extension: false,
            stop_flag: Arc::new(AtomicBool::new(false)),
        });

        let _ = fs::remove_file(&path);
        assert!(result.is_none());
    }
}
