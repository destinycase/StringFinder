mod casefold_table;
mod excel_date_formats;
mod excel_search;
mod json_search;
mod textkey;
mod types;
mod ue_archive_search;
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
use std::sync::{Arc, Condvar, Mutex};
use std::time::SystemTime;

use crate::excel_search::{search_excel_file, ExcelFileError};
use crate::json_search::search_json_file_with_policy;
use crate::types::{RawMatch, SearchMatch, SearchOptions};
use crate::ue_archive_search::{search_archive_file, ArchiveSearchError};
use crate::utils::{
    build_glob_set, decode_structured_bytes, detect_encoding, generate_search_patterns, is_binary,
    match_filename_glob, parse_search_mode,
};
use crate::xml_search::{search_xml_file, XmlSearchError};

// 내부 타입 에일리어스 및 에러 마커 정의
type RawFileMatches = Vec<(String, Vec<RawMatch>)>;
type FileMatches = Vec<(String, Vec<SearchMatch>)>;
type SkippedEntries = Vec<(String, String)>;

// 호출자는 모든 송신자를 닫고 GIL을 해제한 상태에서 이 함수를 호출합니다.
// 하나가 실패해도 나머지 스레드를 끝까지 정리한 뒤 오류를 돌려줍니다.
fn join_auxiliary_threads(
    dispatcher: Option<std::thread::JoinHandle<()>>,
    monitor: Option<std::thread::JoinHandle<()>>,
) -> Option<String> {
    let mut error: Option<String> = None;
    for (name, handle) in [
        ("results dispatcher", dispatcher),
        ("monitor thread", monitor),
    ] {
        if let Some(handle) = handle {
            if handle.join().is_err() {
                let message = format!("{name} terminated unexpectedly");
                if let Some(previous) = error.as_mut() {
                    previous.push_str("; ");
                    previous.push_str(&message);
                } else {
                    error = Some(message);
                }
            }
        }
    }
    error
}

fn merge_completion_errors(
    callback_error: Option<String>,
    thread_error: Option<String>,
) -> Option<String> {
    match (callback_error, thread_error) {
        (Some(callback), Some(thread)) => Some(format!("{callback}; {thread}")),
        (callback, thread) => callback.or(thread),
    }
}

const REASON_ERR_MMAP: &str = "ERR_MMAP";
const REASON_ERR_OPEN: &str = "ERR_OPEN";
const REASON_ERR_ENCODING: &str = "ERR_ENCODING";
const REASON_ERR_METADATA: &str = "ERR_METADATA";
const REASON_ERR_WALK: &str = "ERR_WALK";
const REASON_ERR_TOO_LARGE: &str = "ERR_TOO_LARGE";
const REASON_ERR_JSON_SIZE_LIMIT: &str = "ERR_JSON_SIZE_LIMIT";
const REASON_ERR_JSON_PARSE: &str = "ERR_JSON_PARSE";
const REASON_ERR_ARCHIVE_FORMAT: &str = "ERR_ARCHIVE_FORMAT";
const REASON_ERR_XML_PARSE: &str = "ERR_XML_PARSE";
const REASON_ERR_XML_UNSUPPORTED_DTD: &str = "ERR_XML_UNSUPPORTED_DTD";
const REASON_ERR_EXCEL_PROCESS: &str = "ERR_EXCEL_PROCESS";
const REASON_ERR_EXCEL_PANIC: &str = "ERR_EXCEL_PANIC";
const REASON_ERR_RESOURCE_BUDGET: &str = "ERR_RESOURCE_BUDGET";

// Fallback for direct engine API calls that omit the setting. Application
// search paths pass the user's configured value and do not use this fallback.
const DEFAULT_MAX_SEARCH_FILE_SIZE_BYTES: u64 = 1024 * 1024 * 1024;
const MATCH_META_BINARY_PREFIX: &str = "__SF_BINARY_MATCH__|";
const MATCH_META_TRUNCATED: &str = "__SF_TRUNCATED__";
// Shared marker for long-line result metadata and offset handling.
const MATCH_META_LONG_LINE_PREFIX: &str = "__SF_LONG_LINE__|";

const MONITOR_INTERVAL_MS: u64 = 100;
const LARGE_STRUCTURED_FILE_BYTES: u64 = 8 * 1024 * 1024;

struct StructuredMemoryLimiter {
    capacity: u64,
    reserved: Mutex<u64>,
    available: Condvar,
}

struct StructuredMemoryPermit {
    limiter: Arc<StructuredMemoryLimiter>,
    reserved: u64,
}

enum StructuredPermitError {
    Cancelled,
    InsufficientBudget(u64),
}

impl StructuredMemoryLimiter {
    fn new(capacity: u64) -> Self {
        Self {
            capacity: capacity.max(1),
            reserved: Mutex::new(0),
            available: Condvar::new(),
        }
    }

    fn acquire(
        self: &Arc<Self>,
        requested: u64,
        stop_flag: &Arc<AtomicBool>,
    ) -> Result<StructuredMemoryPermit, StructuredPermitError> {
        if requested > self.capacity {
            return Err(StructuredPermitError::InsufficientBudget(requested));
        }
        let reservation = requested.max(1);
        let mut reserved = self
            .reserved
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        loop {
            if stop_flag.load(Ordering::Relaxed) {
                return Err(StructuredPermitError::Cancelled);
            }
            if reserved.saturating_add(reservation) <= self.capacity {
                *reserved += reservation;
                return Ok(StructuredMemoryPermit {
                    limiter: self.clone(),
                    reserved: reservation,
                });
            }
            let (next, _) = self
                .available
                .wait_timeout(
                    reserved,
                    std::time::Duration::from_millis(MONITOR_INTERVAL_MS),
                )
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            reserved = next;
        }
    }
}

impl Drop for StructuredMemoryPermit {
    fn drop(&mut self) {
        let mut reserved = self
            .limiter
            .reserved
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        *reserved = reserved.saturating_sub(self.reserved);
        self.limiter.available.notify_all();
    }
}

fn estimated_structured_memory(
    path: &Path,
    is_json: bool,
    is_archive: bool,
    is_xml: bool,
    is_excel: bool,
) -> u64 {
    let extension = path
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    let memory_profile =
        if is_excel || ["xlsx", "xlsm", "xlsb", "xls"].contains(&extension.as_str()) {
            Some((8, 128 * 1024 * 1024))
        } else if (is_json && extension == "json")
            || (is_archive && extension == "archive")
            || (is_xml && ["xml", "sf_xml"].contains(&extension.as_str()))
        {
            Some((5, 64 * 1024 * 1024))
        } else {
            None
        };
    let Some((multiplier, fixed_overhead)) = memory_profile else {
        return 0;
    };
    let file_size = std::fs::metadata(path).map(|meta| meta.len()).unwrap_or(0);
    if file_size < LARGE_STRUCTURED_FILE_BYTES {
        return 0;
    }
    if multiplier == 8 {
        return file_size
            .saturating_mul(multiplier)
            .saturating_add(fixed_overhead);
    }
    file_size
        .saturating_mul(multiplier)
        .saturating_add(1)
        .saturating_div(2)
        .saturating_add(fixed_overhead)
}

fn acquire_structured_permit(
    limiter: &Option<Arc<StructuredMemoryLimiter>>,
    path: &Path,
    is_json: bool,
    is_archive: bool,
    is_xml: bool,
    is_excel: bool,
    stop_flag: &Arc<AtomicBool>,
) -> Result<Option<StructuredMemoryPermit>, StructuredPermitError> {
    let Some(limiter) = limiter else {
        return Ok(None);
    };
    let estimate = estimated_structured_memory(path, is_json, is_archive, is_xml, is_excel);
    if estimate == 0 {
        return Ok(None);
    }
    limiter.acquire(estimate, stop_flag).map(Some)
}

fn has_recycle_bin_component(path: &Path) -> bool {
    path.components().any(|component| {
        component
            .as_os_str()
            .to_str()
            .is_some_and(|value| value.eq_ignore_ascii_case("$RECYCLE.BIN"))
    })
}

fn is_direct_child_of_filesystem_root(path: &Path) -> bool {
    path.is_absolute()
        && path
            .components()
            .filter(|component| matches!(component, std::path::Component::Normal(_)))
            .count()
            == 1
}

fn should_exclude_recycle_path(path: &Path) -> bool {
    if has_recycle_bin_component(path) {
        return true;
    }

    let Some(parent) = path.parent() else {
        return false;
    };
    if !is_direct_child_of_filesystem_root(parent) {
        return false;
    }
    let Some(root) = parent.parent() else {
        return false;
    };
    let Some(alias_name) = parent.file_name() else {
        return false;
    };
    if !root.join("$RECYCLE.BIN").join(alias_name).is_dir() {
        return false;
    }

    // Anti-ransomware canaries can expose a root-level directory alias while
    // the final file path lives below $RECYCLE.BIN. The cheap sibling-directory
    // check above ensures canonicalization is limited to alias candidates.
    std::fs::canonicalize(path)
        .ok()
        .is_some_and(|resolved| has_recycle_bin_component(&resolved))
}

struct CallbackState {
    failed: AtomicBool,
    message: Mutex<Option<String>>,
}

impl CallbackState {
    fn new() -> Self {
        Self {
            failed: AtomicBool::new(false),
            message: Mutex::new(None),
        }
    }

    fn record_error(&self, stop_flag: &Arc<AtomicBool>, name: &str, error: PyErr) {
        if !self.failed.swap(true, Ordering::SeqCst) {
            let mut message = self
                .message
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            *message = Some(format!("{} callback failed: {}", name, error));
        }
        stop_flag.store(true, Ordering::SeqCst);
    }

    fn error_message(&self) -> Option<String> {
        self.message
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .clone()
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

fn walk_error_entries(error: &ignore::Error) -> SkippedEntries {
    // Only error handling allocates these records. Do not parse Display text
    // for paths: wrappers and aggregate errors have a structured path already.
    static UNKNOWN_ID: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
    let mut entries = Vec::new();
    let mut pending = vec![(error, None::<&Path>)];
    while let Some((current, path)) = pending.pop() {
        match current {
            ignore::Error::WithPath { path, err } => pending.push((err, Some(path))),
            ignore::Error::WithDepth { err, .. } | ignore::Error::WithLineNumber { err, .. } => {
                pending.push((err, path))
            }
            ignore::Error::Partial(errors) if !errors.is_empty() => {
                pending.extend(errors.iter().rev().map(|err| (err, path)));
            }
            _ => {
                let path = match current {
                    ignore::Error::Loop { child, .. } => Some(child.as_path()),
                    _ => path,
                };
                let identifier = path
                    .map(|path| path.to_string_lossy().into_owned())
                    .unwrap_or_else(|| {
                        format!(
                            "__SF_WALK_UNKNOWN__|{}",
                            UNKNOWN_ID.fetch_add(1, Ordering::Relaxed)
                        )
                    });
                let detail = match path {
                    Some(path) => format!("{}: {}", path.display(), current),
                    None => current.to_string(),
                };
                entries.push((identifier, encode_skip_reason(REASON_ERR_WALK, detail)));
            }
        }
    }
    entries
}

fn encode_xml_skip_reason(error: XmlSearchError) -> String {
    match error {
        XmlSearchError::Parse(detail) => encode_skip_reason(REASON_ERR_XML_PARSE, detail),
        XmlSearchError::UnsupportedDtd(detail) => {
            encode_skip_reason(REASON_ERR_XML_UNSUPPORTED_DTD, detail)
        }
    }
}

fn encode_excel_skip_reason(error: ExcelFileError) -> String {
    match error {
        ExcelFileError::Open(detail) => encode_skip_reason(REASON_ERR_EXCEL_PROCESS, detail),
        ExcelFileError::Panic(detail) => encode_skip_reason(REASON_ERR_EXCEL_PANIC, detail),
    }
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
    let Ok(meta) = file.metadata() else {
        return false;
    };
    if meta.len() != expected_len {
        return false;
    }
    expected_modified.is_none_or(|expected| meta.modified().ok() == Some(expected))
}

fn load_file_snapshot(
    mut file: File,
    expected_len: u64,
    expected_modified: Option<SystemTime>,
) -> Result<FileSnapshot, String> {
    if expected_len < 16 * 1024 {
        let mut bytes = Vec::new();
        file.read_to_end(&mut bytes).map_err(|e| e.to_string())?;
        return Ok(FileSnapshot::Owned(bytes));
    }

    // A shared lock protects the mmap path on platforms that enforce file
    // sharing. If it cannot be acquired, use a read-copy snapshot instead.
    if fs2::FileExt::try_lock_shared(&file).is_ok() {
        match unsafe { Mmap::map(&file) } {
            Ok(mmap) if metadata_matches(&file, expected_len, expected_modified) => {
                return Ok(FileSnapshot::Mapped {
                    _lock_file: file,
                    mmap,
                });
            }
            Ok(_) | Err(_) => {}
        }
        let _ = fs2::FileExt::unlock(&file);
    }

    let mut bytes = Vec::new();
    file.read_to_end(&mut bytes).map_err(|e| e.to_string())?;
    Ok(FileSnapshot::Owned(bytes))
}

fn search_walk_builder(
    root_path: &Path,
    exclude_hidden: bool,
    include_junctions: bool,
) -> WalkBuilder {
    let mut builder = WalkBuilder::new(root_path);
    builder.hidden(exclude_hidden);
    builder.follow_links(include_junctions);
    // Search results must not depend on repository ignore rules; the
    // precise-search scanner also traverses files listed in .gitignore/.ignore.
    builder.ignore(false);
    builder.git_ignore(false);
    builder.git_exclude(false);
    builder.git_global(false);
    builder
}

fn size_check_metadata(
    entry: &ignore::DirEntry,
    fresh: bool,
    limit: u64,
) -> Option<std::fs::Metadata> {
    if fresh {
        return std::fs::metadata(entry.path()).ok();
    }
    entry.metadata().ok().and_then(|metadata| {
        if metadata.len() > limit {
            // A formerly large file may have shrunk since enumeration.
            std::fs::metadata(entry.path()).ok()
        } else {
            Some(metadata)
        }
    })
}

/// Distinguish Windows mount-point junctions from symbolic links. Only linked
/// directories need the reparse query; ordinary files retain the cheap path.
fn is_junction(path: &Path) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::{MetadataExt, OpenOptionsExt};
        use std::os::windows::io::AsRawHandle;
        let Ok(metadata) = std::fs::symlink_metadata(path) else {
            return false;
        };
        if metadata.file_attributes() & 0x400 == 0 {
            return false;
        }
        let Ok(file) = std::fs::OpenOptions::new()
            .read(true)
            .custom_flags(0x02000000 | 0x00200000)
            .open(path)
        else {
            return false;
        };
        #[link(name = "kernel32")]
        extern "system" {
            fn DeviceIoControl(
                handle: *mut std::ffi::c_void,
                code: u32,
                input: *mut std::ffi::c_void,
                input_size: u32,
                output: *mut std::ffi::c_void,
                output_size: u32,
                returned: *mut u32,
                overlapped: *mut std::ffi::c_void,
            ) -> i32;
        }
        let mut buffer = [0u8; 16384];
        let mut returned = 0;
        // The handle and output buffer remain alive throughout this synchronous call.
        let ok = unsafe {
            DeviceIoControl(
                file.as_raw_handle(),
                0x000900A8,
                std::ptr::null_mut(),
                0,
                buffer.as_mut_ptr().cast(),
                buffer.len() as u32,
                &mut returned,
                std::ptr::null_mut(),
            )
        };
        ok != 0
            && returned >= 4
            && u32::from_le_bytes(buffer[..4].try_into().unwrap()) == 0xA0000003
    }
    #[cfg(not(windows))]
    {
        let _ = path;
        false
    }
}

fn claim_walk_entry(
    path: &Path,
    include_junctions: bool,
    visited: &Mutex<std::collections::HashSet<std::path::PathBuf>>,
) -> bool {
    let junction = is_junction(path);
    if (!include_junctions && junction) || (path.is_symlink() && !junction) {
        return false;
    }
    if include_junctions {
        // Atomic insertion is shared by all parallel walkers and all roots.
        let resolved = std::fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf());
        return visited
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .insert(resolved);
    }
    true
}

#[pyfunction]
#[pyo3(signature = (path, pattern, mode_bits=None, stop_event=None, max_per_file=10000,  max_json_depth=20000, max_json_size=1073741824, options=None))]
#[allow(clippy::too_many_arguments)]
fn search_file(
    py: Python,
    path: String,
    pattern: String,
    mut mode_bits: Option<u32>,
    mut stop_event: Option<pyo3::PyObject>,
    mut max_per_file: usize,

    mut max_json_depth: usize,
    mut max_json_size: u64,
    options: Option<Py<SearchOptions>>,
) -> Result<Vec<SearchMatch>, PyErr> {
    validate_query_length(&pattern)?;
    let mut encoding = None;
    let mut include_junctions = false;
    if let Some(config) = options.as_ref() {
        let config = config.bind(py).borrow();
        encoding = crate::utils::requested_encoding(config.encoding.as_deref());
        include_junctions = config.include_junctions;
        if config.mode_bits.is_some() {
            mode_bits = config.mode_bits;
        }
        if config.stop_event.is_some() {
            stop_event = config.stop_event.as_ref().map(|event| event.clone_ref(py));
        }
        if config.max_per_file.is_some() {
            max_per_file = config.max_per_file.unwrap_or(max_per_file);
        }

        if config.max_json_depth.is_some() {
            max_json_depth = config.max_json_depth.unwrap_or(max_json_depth);
        }
        if config.max_json_size.is_some() {
            max_json_size = config.max_json_size.unwrap_or(max_json_size);
        }
    }

    let _ = include_junctions; // No traversal in the single-file entry point.
    let (is_json, is_xml, is_exact, is_excel, exclude_binary) = parse_search_mode(mode_bits);
    let is_archive = mode_bits.unwrap_or(0) & types::MODE_ARCHIVE != 0;
    let (matcher, ac) = build_search_matchers(&pattern, is_xml, is_json || is_archive)?;

    let stop_flag = Arc::new(AtomicBool::new(false));
    // 검색 중에는 100ms 간격의 취소 확인을 유지하되, 정상 완료 시 채널로 즉시 깨워
    // 짧은 파일 검색이 감시 스레드의 sleep 종료를 기다리지 않도록 합니다.
    let (monitor_handle, monitor_done) = if let Some(evt) = stop_event {
        let flag_clone = stop_flag.clone();
        let (done_tx, done_rx) = std::sync::mpsc::channel::<()>();
        let handle = std::thread::spawn(move || loop {
            if flag_clone.load(Ordering::Relaxed) {
                break;
            }
            let is_stopped = Python::with_gil(|py| {
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
            match done_rx.recv_timeout(std::time::Duration::from_millis(MONITOR_INTERVAL_MS)) {
                Ok(()) | Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => break,
                Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {}
            }
        });
        (Some(handle), Some(done_tx))
    } else {
        (None, None)
    };

    let res = py.allow_threads(|| {
        search_file_internal(InternalSearchParams {
            encoding,
            path: Path::new(&path),
            matcher: &matcher,
            ac: &ac,
            is_exact,
            is_json,
            is_archive,
            is_xml,
            is_excel,
            exclude_hidden: false,
            allow_duplicate_json_keys: mode_bits.unwrap_or(0)
                & types::MODE_ALLOW_DUPLICATE_JSON_KEYS
                != 0,
            exclude_binary,

            stop_flag,
            max_per_file,
            max_json_depth,
            max_json_size,
        })
    });

    if let Some(done_tx) = monitor_done {
        let _ = done_tx.send(());
    }
    // The monitor needs the GIL to check the Python cancellation event.
    // Release it while joining, including when the search returns an error.
    let thread_error = if let Some(handle) = monitor_handle {
        py.allow_threads(|| join_auxiliary_threads(None, Some(handle)))
    } else {
        None
    };
    if let Some(thread_error) = thread_error {
        let message = match &res {
            Some(Err(search_error)) => format!("{search_error}; {thread_error}"),
            _ => thread_error,
        };
        return Err(pyo3::exceptions::PyRuntimeError::new_err(message));
    }

    match res {
        Some(Ok(m)) => Ok(to_python_matches(apply_match_limit(m, max_per_file))),
        Some(Err(e)) if e.starts_with(REASON_ERR_JSON_SIZE_LIMIT) => {
            Ok(to_python_matches(vec![(0, e, None, None)]))
        }
        Some(Err(e)) => Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e)),
        None => Ok(Vec::new()),
    }
}

/// 일반 텍스트를 이 크기의 조각(줄 경계에 맞춤)으로 나눠, 조각이 CPU 캐시에 있는 동안
/// 변환 필요 여부 검사와 검색을 이어서 합니다.
const TEXT_CHUNK_SIZE: usize = 256 * 1024;

#[allow(clippy::too_many_arguments)]
fn do_search_with_mmap(
    mmap: &[u8],
    encoding: &'static Encoding,
    matcher: &textkey::Matcher,
    is_exact: bool,

    stop_flag: &Arc<AtomicBool>,
    max_per_file: usize,
) -> Vec<RawMatch> {
    let mut results = Vec::new();
    if encoding == UTF_8 {
        search_utf8_text(
            mmap,
            matcher,
            is_exact,
            stop_flag,
            max_per_file,
            &mut results,
        );
    } else {
        // Non-UTF8 일반 텍스트는 파일 전체를 String으로 복사하지 않고 청크 단위로 디코딩합니다.
        search_non_utf8_chunks(
            mmap,
            encoding,
            matcher,
            is_exact,
            stop_flag,
            max_per_file,
            &mut results,
        );
    }
    results
}

#[inline]
fn is_ascii_chunk(bytes: &[u8]) -> bool {
    // 표준 구현은 한 번에 여러 바이트를 보고, 비ASCII를 만나면 바로 멈춥니다(한글 조각은 즉시 끝남).
    bytes.is_ascii()
}

/// UTF-8 일반 텍스트 검색 상태입니다.
///
/// 파일을 줄로 나누지 않습니다. 변환이 필요한 글자가 있는 줄만 `key()`로 바꿔 비교하고,
/// 나머지는 원본 그대로 `key(검색어)`를 ASCII 대소문자 무시로 찾습니다(결과가 같음이 증명된 경로).
struct Utf8TextSearch<'a> {
    buf: &'a [u8],
    matcher: &'a textkey::Matcher,
    is_exact: bool,
    max_per_file: usize,
    stop_flag: &'a Arc<AtomicBool>,
    counter: textkey::LineCounter,
    // 같은 줄의 여러 일치에서 줄 경계를 다시 찾지 않도록 마지막 줄을 기억합니다.
    line: Option<(usize, usize)>,
    last_exact_line_start: Option<usize>,
    results: &'a mut Vec<RawMatch>,
}

impl Utf8TextSearch<'_> {
    fn full(&self) -> bool {
        self.results.len() > self.max_per_file
    }

    fn bounds(&mut self, position: usize) -> (usize, usize) {
        if let Some((start, end)) = self.line {
            if start <= position && position < end {
                return (start, end);
            }
        }
        let bounds = textkey::line_bounds(self.buf, position);
        self.line = Some(bounds);
        bounds
    }

    /// 원본 그대로 찾아도 되는 구간입니다. 중지되었거나 상한을 넘으면 참을 돌려줍니다.
    fn raw_region(&mut self, start: usize, end: usize) -> bool {
        if start >= end {
            return false;
        }
        let finder = self.matcher.raw_finder();
        for hit in finder.find_iter(&self.buf[start..end]) {
            if self.full() {
                return true;
            }
            if self.results.len().is_multiple_of(1000) && self.stop_flag.load(Ordering::Relaxed) {
                return true;
            }
            self.raw_hit(start + hit.start(), hit.len());
        }
        false
    }

    /// 원본에서 찾은 일치 하나를 기록합니다(줄에 변환 대상이 없음이 확인된 경우).
    fn raw_hit(&mut self, match_start: usize, match_len: usize) {
        let (line_start, line_end) = self.bounds(match_start);
        if self.is_exact {
            if self.last_exact_line_start == Some(line_start) {
                return;
            }
            self.last_exact_line_start = Some(line_start);
            let text = String::from_utf8_lossy(&self.buf[line_start..line_end]);
            if self.matcher.equals_trimmed(&text) {
                let line = self.counter.line_at(self.buf, line_start);
                let content =
                    extract_line_content_bytes(self.buf, line_start, line_end, None, None);
                self.results.push((line, content, None, None));
            }
            return;
        }
        let line = self.counter.line_at(self.buf, match_start);
        // Each hit retains its line preview and byte range.
        let content = extract_line_content_bytes(
            self.buf,
            line_start,
            line_end,
            Some(match_start),
            Some(match_len),
        );
        let (offset, length) = if content.starts_with(MATCH_META_LONG_LINE_PREFIX) {
            (None, None)
        } else {
            (Some(match_start), Some(match_len))
        };
        self.results.push((line, content, offset, length));
    }

    /// 변환이 필요한 줄입니다. 원본 위치를 알 수 없으므로 위치는 생략합니다(틀린 위치 금지).
    fn converted_line(&mut self, line_start: usize, line_end: usize) {
        let text = String::from_utf8_lossy(&self.buf[line_start..line_end]);
        let line = self.counter.line_at(self.buf, line_start);
        if self.is_exact {
            if self.matcher.equals_trimmed(&text) {
                let content =
                    extract_line_content_bytes(self.buf, line_start, line_end, None, None);
                self.results.push((line, content, None, None));
            }
            return;
        }
        let hits = self.matcher.hits(&text).len();
        if hits == 0 {
            return;
        }
        let content = extract_line_content_bytes(self.buf, line_start, line_end, None, None);
        for _ in 0..hits {
            if self.full() {
                return;
            }
            self.results.push((line, content.clone(), None, None));
        }
    }
}

fn search_utf8_text(
    buf: &[u8],
    matcher: &textkey::Matcher,
    is_exact: bool,
    stop_flag: &Arc<AtomicBool>,
    max_per_file: usize,
    results: &mut Vec<RawMatch>,
) {
    let ascii_pattern = matcher.pat_ascii();
    let mut search = Utf8TextSearch {
        buf,
        matcher,
        is_exact,
        max_per_file,
        stop_flag,
        counter: textkey::LineCounter::new(),
        line: None,
        last_exact_line_start: None,
        results,
    };
    if let Some(triggers) = matcher.triggers() {
        search_utf8_with_triggers(&mut search, triggers);
        return;
    }
    let mut chunk_start = 0usize;
    while chunk_start < buf.len() {
        if search.full() || stop_flag.load(Ordering::Relaxed) {
            return;
        }
        let mut chunk_end = (chunk_start + TEXT_CHUNK_SIZE).min(buf.len());
        if chunk_end < buf.len() {
            // 조각 끝을 줄바꿈 위치에 맞춥니다. 검색어에는 줄바꿈이 없으므로 일치가 조각에 걸치지 않습니다.
            chunk_end = memchr::memchr2(b'\n', b'\r', &buf[chunk_end..])
                .map_or(buf.len(), |index| chunk_end + index);
        }
        if is_ascii_chunk(&buf[chunk_start..chunk_end]) {
            // ASCII 본문의 key는 ASCII뿐이므로 비ASCII 검색어는 일치할 수 없습니다.
            if ascii_pattern && search.raw_region(chunk_start, chunk_end) {
                return;
            }
        } else {
            let mut cursor = chunk_start;
            loop {
                let flagged = textkey::next_flagged(&buf[..chunk_end], cursor, ascii_pattern);
                let raw_end = flagged.map_or(chunk_end, |position| {
                    textkey::line_bounds(buf, position).0.max(cursor)
                });
                if search.raw_region(cursor, raw_end) {
                    return;
                }
                let Some(position) = flagged else {
                    break;
                };
                let (line_start, line_end) = textkey::line_bounds(buf, position);
                search.converted_line(line_start, line_end);
                if search.full() {
                    return;
                }
                cursor = line_end;
                if cursor >= chunk_end {
                    break;
                }
            }
        }
        chunk_start = chunk_end;
    }
}

/// 유발 글자 경로: 원본에서 `key(검색어)`를 찾고(단일 검색어라 빠른 사전 거르기가 유지됨),
/// ASCII가 아닌 조각에서만 유발 글자의 첫 바이트를 SIMD로 따로 찾습니다.
///
/// 유발 글자가 있는 줄과, 일치 바로 뒤에 변환 대상 글자가 붙은 줄만 `key()`로 바꿔 비교합니다.
fn search_utf8_with_triggers(search: &mut Utf8TextSearch<'_>, triggers: &textkey::Triggers) {
    let buf = search.buf;
    let finder = search.matcher.raw_finder();
    let mut convert_lines: Vec<(usize, usize)> = Vec::new();
    let mut chunk_start = 0usize;
    while chunk_start < buf.len() {
        if search.full() || search.stop_flag.load(Ordering::Relaxed) {
            return;
        }
        let mut chunk_end = (chunk_start + TEXT_CHUNK_SIZE).min(buf.len());
        if chunk_end < buf.len() {
            // 조각 끝을 줄바꿈 위치에 맞춥니다. 검색어에는 줄바꿈이 없으므로 일치가 조각에 걸치지 않습니다.
            chunk_end = memchr::memchr2(b'\n', b'\r', &buf[chunk_end..])
                .map_or(buf.len(), |index| chunk_end + index);
        }
        convert_lines.clear();
        if !is_ascii_chunk(&buf[chunk_start..chunk_end]) {
            triggers.scan(buf, chunk_start, chunk_end, |position| {
                convert_lines.push(textkey::line_bounds(buf, position));
            });
            convert_lines.sort_unstable();
            convert_lines.dedup();
        }
        let mut next_convert = 0usize;
        let mut converted_until = chunk_start;
        // 같은 줄의 원본 일치는 줄이 끝날 때까지 모았다가, 줄이 변환되지 않았을 때만 기록합니다.
        let mut pending: Vec<(usize, usize)> = Vec::new();
        let mut pending_line: Option<(usize, usize)> = None;
        for hit in finder.find_iter(&buf[chunk_start..chunk_end]) {
            let start = chunk_start + hit.start();
            if start < converted_until {
                continue;
            }
            let bounds = search.bounds(start);
            if pending_line != Some(bounds) {
                for (hit_start, hit_len) in pending.drain(..) {
                    search.raw_hit(hit_start, hit_len);
                }
                pending_line = Some(bounds);
            }
            // 이 일치보다 앞에 있는 변환 대상 줄을 먼저 처리합니다.
            while next_convert < convert_lines.len() && convert_lines[next_convert].0 < bounds.0 {
                let (line_start, line_end) = convert_lines[next_convert];
                search.converted_line(line_start, line_end);
                next_convert += 1;
            }
            let in_trigger_line =
                next_convert < convert_lines.len() && convert_lines[next_convert].0 == bounds.0;
            if in_trigger_line || textkey::variant_at(buf, start + hit.len()) {
                if in_trigger_line {
                    next_convert += 1;
                }
                // 이미 모은 같은 줄의 원본 일치는 버리고 줄 전체를 변환해 다시 셉니다.
                pending.clear();
                pending_line = None;
                search.converted_line(bounds.0, bounds.1);
                converted_until = bounds.1.max(start + 1);
            } else {
                pending.push((start, hit.len()));
            }
            if search.full() {
                return;
            }
            if search.results.len().is_multiple_of(1000) && search.stop_flag.load(Ordering::Relaxed)
            {
                return;
            }
        }
        for (hit_start, hit_len) in pending.drain(..) {
            search.raw_hit(hit_start, hit_len);
        }
        for &(line_start, line_end) in &convert_lines[next_convert..] {
            search.converted_line(line_start, line_end);
            if search.full() {
                return;
            }
        }
        chunk_start = chunk_end;
    }
}

fn apply_match_limit(matches: Vec<RawMatch>, max_per_file: usize) -> Vec<RawMatch> {
    // line 0의 __SF_ 항목은 실제 매치가 아닌 파일 단위 메타데이터입니다.
    // 매치 상한 계산에서 제외하고, 잘림 이후에도 반드시 보존합니다.
    let (mut visible_matches, mut metadata): (Vec<_>, Vec<_>) = matches
        .into_iter()
        .partition(|item| !(item.0 == 0 && item.1.starts_with("__SF_")));
    let is_single_error_marker =
        visible_matches.len() == 1 && visible_matches[0].1.starts_with("ERR_");
    if visible_matches.len() > max_per_file && !is_single_error_marker {
        visible_matches.truncate(max_per_file);
        if !metadata.iter().any(|item| item.1 == MATCH_META_TRUNCATED) {
            metadata.insert(0, (0, MATCH_META_TRUNCATED.to_string(), None, None));
        }
    }
    visible_matches.extend(metadata);
    visible_matches
}

fn detect_text_search_encoding(data: &[u8], pattern: &str, is_exact: bool) -> &'static Encoding {
    let prefix = &data[..data.len().min(65536)];
    // Literal ASCII matches are byte-compatible with CP949. Keep the existing
    // sampled fast path; invalid decoded result snippets trigger a full recheck.
    // Exact/Unicode searches cannot use that proof and validate the full input.
    if !is_exact && pattern.is_ascii() {
        let mut end = prefix.len();
        while end < data.len() && end < prefix.len() + 3 && data[end] & 0xc0 == 0x80 {
            end += 1;
        }
        let encoding = detect_encoding(&data[..end]);
        // UTF-8 boundary extension may split a CP949 pair. Do not let that
        // artificial failure override a valid CP949 sample with UTF-16.
        if encoding == UTF_16LE || encoding == UTF_16BE {
            return detect_encoding(prefix);
        }
        return encoding;
    }
    detect_encoding(data)
}

/// 디코딩한 줄 하나를 비교합니다. 디코딩한 위치는 원본 인코딩의 위치가 아니므로 위치는 보고하지 않습니다.
fn collect_decoded_line(
    line: &str,
    line_number: usize,
    matcher: &textkey::Matcher,
    is_exact: bool,

    max_per_file: usize,
    results: &mut Vec<RawMatch>,
) -> bool {
    if is_exact {
        if matcher.equals_trimmed(line) {
            let content = extract_line_content_bytes(line.as_bytes(), 0, line.len(), None, None);
            results.push((line_number, content, None, None));
            return results.len() > max_per_file;
        }
        return false;
    }
    for hit in matcher.hits(line) {
        let content = extract_line_content_bytes(
            line.as_bytes(),
            0,
            line.len(),
            hit.map(|(start, _)| start),
            hit.map(|(_, length)| length),
        );
        results.push((line_number, content, None, None));
        if results.len() > max_per_file {
            return true;
        }
    }
    false
}

/// 디코딩이 필요한 텍스트(UTF-16·CP949 등)를 청크 단위로 검색합니다.
/// 줄바꿈은 LF, CRLF, CR이며, 청크 끝의 CR은 다음 청크의 LF와 합쳐질 수 있으므로 기다립니다.
#[allow(clippy::too_many_arguments)]
fn search_non_utf8_chunks(
    mmap: &[u8],
    encoding: &'static Encoding,
    matcher: &textkey::Matcher,
    is_exact: bool,

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
        if stop_flag.load(Ordering::Relaxed) {
            return;
        }
        let end = (input_offset + DECODE_CHUNK_SIZE).min(mmap.len());
        let last = end == mmap.len();
        // encoding_rs는 출력 버퍼가 가득 차면 입력을 소비하지 않으므로 충분한
        // 청크 출력 공간을 미리 확보합니다. 필요 시 내부적으로 더 작은 결과를 생성합니다.
        let mut decoded = String::with_capacity((end - input_offset).saturating_mul(3));
        let (_status, consumed, _had_errors) =
            decoder.decode_to_string(&mmap[input_offset..end], &mut decoded, last);
        if consumed == 0 && end > input_offset {
            // Decoder가 입력을 소비하지 못하는 경우 무한 루프를 방지합니다.
            break;
        }
        input_offset += consumed;
        let finished = input_offset >= mmap.len();
        pending.push_str(&decoded);

        let bytes = pending.as_bytes();
        let mut line_start = 0usize;
        while let Some(relative) = memchr::memchr2(b'\n', b'\r', &bytes[line_start..]) {
            let terminator = line_start + relative;
            let terminator_len = if bytes[terminator] == b'\n' {
                1
            } else if terminator + 1 < bytes.len() {
                if bytes[terminator + 1] == b'\n' {
                    2
                } else {
                    1
                }
            } else if finished {
                1
            } else {
                // 청크 끝의 CR: 다음 청크가 LF로 시작하면 CRLF 한 번입니다.
                break;
            };
            if results.len() > max_per_file {
                return;
            }
            if line_number.is_multiple_of(1000) && stop_flag.load(Ordering::Relaxed) {
                return;
            }
            if collect_decoded_line(
                &pending[line_start..terminator],
                line_number,
                matcher,
                is_exact,
                max_per_file,
                results,
            ) {
                return;
            }
            line_number += 1;
            line_start = terminator + terminator_len;
        }
        if line_start > 0 {
            pending.drain(..line_start);
        }

        if consumed == 0 {
            break;
        }
    }

    if !pending.is_empty() && results.len() <= max_per_file && !stop_flag.load(Ordering::Relaxed) {
        let line = pending.strip_suffix('\r').unwrap_or(&pending);
        collect_decoded_line(line, line_number, matcher, is_exact, max_per_file, results);
    }
}

struct InternalSearchParams<'a> {
    encoding: Option<&'static encoding_rs::Encoding>,
    path: &'a Path,
    matcher: &'a textkey::Matcher,
    ac: &'a aho_corasick::AhoCorasick,
    is_exact: bool,
    is_json: bool,
    is_archive: bool,
    allow_duplicate_json_keys: bool,
    is_xml: bool,
    is_excel: bool,
    exclude_hidden: bool,
    exclude_binary: bool,

    stop_flag: Arc<AtomicBool>,
    max_per_file: usize,
    max_json_depth: usize,
    max_json_size: u64,
}

fn search_file_internal(params: InternalSearchParams) -> Option<Result<Vec<RawMatch>, String>> {
    let InternalSearchParams {
        encoding,
        path,
        matcher,
        ac,
        is_exact,
        is_json,
        is_archive,
        allow_duplicate_json_keys,
        is_xml,
        is_excel,
        exclude_hidden,
        exclude_binary,
        stop_flag,
        max_per_file,
        max_json_depth,
        max_json_size,
    } = params;

    if should_exclude_recycle_path(path) {
        return None;
    }

    let ext = path
        .extension()
        .and_then(|s| s.to_str())
        .unwrap_or("")
        .to_lowercase();
    let ext_l = format!(".{}", ext);

    if is_excel || ["xlsx", "xlsb", "xls", "xlsm"].contains(&ext.as_str()) {
        let metadata = match std::fs::metadata(path) {
            Ok(metadata) => metadata,
            Err(error) => return Some(Err(encode_skip_reason(REASON_ERR_METADATA, error))),
        };
        let file_size_limit = max_json_size;
        if metadata.len() > file_size_limit {
            return Some(Err(encode_skip_reason(
                REASON_ERR_TOO_LARGE,
                format!("{} bytes", metadata.len()),
            )));
        }
        if exclude_hidden {
            #[cfg(windows)]
            {
                use std::os::windows::fs::MetadataExt;
                if (metadata.file_attributes() & 0x02) != 0 {
                    return None;
                }
            }
        }
        let r_raw = match search_excel_file(path, matcher, is_exact, stop_flag, max_per_file) {
            Ok(matches) => matches,
            Err(error) => return Some(Err(encode_excel_skip_reason(error))),
        };
        let r = apply_match_limit(r_raw, max_per_file);
        return if r.is_empty() { None } else { Some(Ok(r)) };
    }

    let file = match File::open(path) {
        Ok(f) => f,
        Err(e) => return Some(Err(encode_skip_reason(REASON_ERR_OPEN, e))),
    };
    let meta = match file.metadata() {
        Ok(m) => m,
        Err(e) => return Some(Err(encode_skip_reason(REASON_ERR_METADATA, e))),
    };

    if exclude_hidden {
        #[cfg(windows)]
        {
            use std::os::windows::fs::MetadataExt;
            if (meta.file_attributes() & 0x02) != 0 {
                return None;
            }
        }
    }

    let f_len = meta.len();
    let f_modified = meta.modified().ok();
    if f_len == 0 {
        return None;
    }
    let file_size_limit = max_json_size;
    if f_len > file_size_limit {
        return Some(Err(encode_skip_reason(
            REASON_ERR_TOO_LARGE,
            format!("{} bytes", f_len),
        )));
    }

    let file_snapshot = match load_file_snapshot(file, f_len, f_modified) {
        Ok(snapshot) => snapshot,
        Err(e) => return Some(Err(encode_skip_reason(REASON_ERR_MMAP, e))),
    };
    let mmap_c = file_snapshot.as_slice();

    let is_archive_document = is_archive && ext_l == ".archive";
    let structured_document = (is_json && ext_l == ".json")
        || is_archive_document
        || (is_xml && (ext_l == ".xml" || ext_l == ".sf_xml"));
    let enc = if let Some(encoding) = encoding {
        if !crate::utils::selected_encoding_valid(mmap_c, encoding) {
            return Some(Err(encode_skip_reason(
                REASON_ERR_ENCODING,
                "Invalid bytes for selected encoding",
            )));
        }
        encoding
    } else if structured_document {
        detect_encoding(mmap_c)
    } else {
        detect_text_search_encoding(mmap_c, matcher.pat_key(), is_exact)
    };
    let decoded = if structured_document {
        match decode_structured_bytes(mmap_c, enc) {
            Ok(decoded) => decoded,
            Err(error) => return Some(Err(error)),
        }
    } else {
        None
    };
    // 구조화된 문서는 파서가 전체 버퍼를 요구하므로 기존 디코딩 경로를 유지합니다.
    // 일반 Non-UTF8 텍스트는 아래의 청크 디코딩 경로에서 처리하여 파일 전체 String 복사를 피합니다.
    let final_mmap = decoded.as_ref().map(|s| s.as_bytes()).unwrap_or(mmap_c);

    let res: Result<Vec<RawMatch>, String> = if is_archive_document {
        search_archive_file(
            final_mmap,
            matcher,
            is_exact,
            stop_flag.clone(),
            max_per_file,
            max_json_depth,
        )
        .map_err(|error| match error {
            ArchiveSearchError::Json(detail) => encode_skip_reason(REASON_ERR_JSON_PARSE, detail),
            ArchiveSearchError::Format(detail) => {
                encode_skip_reason(REASON_ERR_ARCHIVE_FORMAT, detail)
            }
        })
    } else if is_json && ext_l == ".json" {
        {
            search_json_file_with_policy(
                final_mmap,
                matcher,
                ac,
                is_exact,
                stop_flag.clone(),
                max_per_file,
                max_json_depth,
                allow_duplicate_json_keys,
            )
            .map_err(|error| encode_skip_reason(REASON_ERR_JSON_PARSE, error))
        }
    } else if is_xml && (ext_l == ".xml" || ext_l == ".sf_xml") {
        {
            search_xml_file(
                final_mmap,
                matcher,
                ac,
                is_exact,
                stop_flag.clone(),
                max_per_file,
            )
            .map_err(encode_xml_skip_reason)
        }
    } else {
        // 인코딩 감지에 성공한 텍스트(UTF-16/EUC-KR 등)는 NUL 바이트가 포함될 수
        // 있으므로 원본 바이트만 보고 바이너리로 판정하지 않습니다.
        let bin = enc == UTF_8 && is_binary(mmap_c);
        if exclude_binary && bin {
            return None;
        }
        Ok(if bin {
            {
                let c = ac.find_iter(mmap_c).count();
                if c > 0 {
                    vec![(
                        0,
                        format!("{}{}", MATCH_META_BINARY_PREFIX, c),
                        None,
                        Some(c),
                    )]
                } else {
                    Vec::new()
                }
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
            let mut matches = do_search_with_mmap(
                searchable_mmap,
                enc,
                matcher,
                is_exact,
                &stop_flag,
                max_per_file,
            );
            if enc == UTF_8 && matches.iter().any(|m| m.1.contains('\u{fffd}')) {
                let verified_encoding = encoding.unwrap_or_else(|| detect_encoding(mmap_c));
                if verified_encoding != UTF_8 {
                    matches = do_search_with_mmap(
                        mmap_c,
                        verified_encoding,
                        matcher,
                        is_exact,
                        &stop_flag,
                        max_per_file,
                    );
                }
            }
            matches
        })
    };

    let res = match res {
        Ok(mut matches) => {
            if is_xml && decoded.is_some() {
                // UTF-8 conversion preserves line numbers, not original byte offsets.
                for item in &mut matches {
                    item.2 = None;
                    item.3 = None;
                }
            }
            apply_match_limit(matches, max_per_file)
        }
        Err(error) => return Some(Err(error)),
    };
    if res.is_empty() {
        None
    } else {
        Some(Ok(res))
    }
}

#[pyfunction]
#[pyo3(signature = (root_paths, pattern, extensions=None, mode_bits=None, filename_filter=None, exclude_hidden=false, stop_event=None, progress_callback=None, results_callback=None, batch_size=None, _flush_ms=None, max_per_file=None,  max_json_depth=None, max_json_size=None, options=None, **_kwargs))]
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

    mut max_json_depth: Option<usize>,
    mut max_json_size: Option<u64>,
    options: Option<Py<SearchOptions>>,
    _kwargs: Option<pyo3::PyObject>,
) -> Result<(FileMatches, SkippedEntries), PyErr> {
    validate_query_length(&pattern)?;
    let mut structured_memory_budget = None;
    let mut encoding = None;
    let mut include_junctions = false;
    if let Some(config) = options.as_ref() {
        let config = config.bind(py).borrow();
        encoding = crate::utils::requested_encoding(config.encoding.as_deref());
        include_junctions = config.include_junctions;
        if config.extensions.is_some() {
            extensions = config.extensions.clone();
        }
        if config.mode_bits.is_some() {
            mode_bits = config.mode_bits;
        }
        if config.filename_filter.is_some() {
            filename_filter = config.filename_filter.clone();
        }
        exclude_hidden = config.exclude_hidden;
        if config.stop_event.is_some() {
            stop_event = config.stop_event.as_ref().map(|event| event.clone_ref(py));
        }
        if config.progress_callback.is_some() {
            progress_callback = config
                .progress_callback
                .as_ref()
                .map(|callback| callback.clone_ref(py));
        }
        if config.results_callback.is_some() {
            results_callback = config
                .results_callback
                .as_ref()
                .map(|callback| callback.clone_ref(py));
        }
        if config.batch_size.is_some() {
            batch_size = config.batch_size;
        }
        if config.flush_ms.is_some() {
            _flush_ms = config.flush_ms;
        }
        if config.max_per_file.is_some() {
            max_per_file = config.max_per_file;
        }

        if config.max_json_depth.is_some() {
            max_json_depth = config.max_json_depth;
        }
        if config.max_json_size.is_some() {
            max_json_size = config.max_json_size;
        }
        structured_memory_budget = config.structured_memory_budget;
    }

    let (is_json, is_xml, is_exact, is_excel, exclude_binary) = parse_search_mode(mode_bits);
    let is_archive = mode_bits.unwrap_or(0) & types::MODE_ARCHIVE != 0;
    let common_file_size_limit = max_json_size.unwrap_or(DEFAULT_MAX_SEARCH_FILE_SIZE_BYTES);
    let (matcher, ac) = build_search_matchers(&pattern, is_xml, is_json || is_archive)?;
    let matcher = Arc::new(matcher);
    let ac = Arc::new(ac);

    let extensions_set = extensions.map(|exts| {
        exts.into_iter()
            .map(|s| s.to_lowercase().trim_start_matches('.').to_string())
            .collect::<HashSet<_>>()
    });
    let filename_glob_set = filename_filter.as_ref().and_then(|f| build_glob_set(f));

    let stop_flag = Arc::new(AtomicBool::new(false));
    let structured_limiter = structured_memory_budget
        .filter(|budget| *budget > 0)
        .map(|budget| Arc::new(StructuredMemoryLimiter::new(budget)));
    let done_flag = Arc::new(AtomicBool::new(false));
    let callback_state = Arc::new(CallbackState::new());
    let processed_files = Arc::new(AtomicU64::new(0)); // M4: 실제 처리 파일 수 카운터
    let (monitor_done_tx, monitor_done_rx) = std::sync::mpsc::channel::<()>();

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
                    if done_clone.load(Ordering::Relaxed) {
                        return false;
                    }
                    if let Some(obj) = &stop_evt_mon {
                        if let Ok(res) = obj.bind(py).call_method0("is_set") {
                            if let Ok(true) = res.extract::<bool>() {
                                return true;
                            }
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
                if is_stopped {
                    flag_clone.store(true, Ordering::SeqCst);
                    break;
                }
                match monitor_done_rx
                    .recv_timeout(std::time::Duration::from_millis(MONITOR_INTERVAL_MS))
                {
                    Ok(()) | Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => break,
                    Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {}
                }
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
                            if done_dispatcher.load(Ordering::Relaxed) && rx.is_empty() {
                                break;
                            }
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
                                callback_state_dispatcher.record_error(
                                    &stop_flag_dispatcher,
                                    "results",
                                    error,
                                );
                            }
                        }
                    });
                }
                // 중지 시에도 이미 채널에 들어온 결과는 모두 전달합니다.
                // done 플래그는 walker와 모든 worker가 송신을 마친 뒤 설정됩니다.
                if done_dispatcher.load(Ordering::Relaxed) && rx.is_empty() {
                    break;
                }
            }
        });
        (tx, handle)
    });

    let visited_paths = Arc::new(Mutex::new(std::collections::HashSet::new()));
    let results = Arc::new(Mutex::new(Vec::new()));
    let skipped = Arc::new(Mutex::new(Vec::new()));
    let tx_main = results_dispatcher.as_ref().map(|(tx, _)| tx.clone());

    let thread_error = py.allow_threads(|| {
        for root_path in root_paths {
            if stop_flag.load(Ordering::Relaxed) {
                break;
            }
            search_walk_builder(Path::new(&root_path), exclude_hidden, include_junctions)
                .build_parallel()
                .run(|| {
                    let visited = visited_paths.clone();
                    let res_ref = results.clone();
                    let skip_ref = skipped.clone();
                    let ac_ref = ac.clone();
                    let stop_ref = stop_flag.clone();
                    let matcher = matcher.clone();
                    let tx_worker = tx_main.clone();
                    let ext_s = extensions_set.as_ref();
                    let glob_s = filename_glob_set.clone();
                    let file_counter = processed_files.clone(); // M4: 카운터 공유
                    let limiter_ref = structured_limiter.clone();

                    Box::new(move |entry| {
                        if stop_ref.load(Ordering::Relaxed) {
                            return ignore::WalkState::Quit;
                        }
                        let entry = match entry {
                            Ok(e) => e,
                            Err(e) => {
                                if let Ok(mut s) = skip_ref.lock() {
                                    s.extend(walk_error_entries(&e));
                                }
                                return ignore::WalkState::Continue;
                            }
                        };
                        let Some(file_type) = entry.file_type() else {
                            return ignore::WalkState::Continue;
                        };
                        if (include_junctions || file_type.is_dir() || file_type.is_symlink())
                            && !claim_walk_entry(entry.path(), include_junctions, &visited)
                        {
                            return ignore::WalkState::Skip;
                        }
                        if file_type.is_dir() && has_recycle_bin_component(entry.path()) {
                            return ignore::WalkState::Skip;
                        }
                        if !file_type.is_file() {
                            return ignore::WalkState::Continue;
                        }

                        let path = entry.path();
                        if should_exclude_recycle_path(path) {
                            return ignore::WalkState::Continue;
                        }
                        if let Some(s) = ext_s {
                            let ext_str = path
                                .extension()
                                .and_then(|s| s.to_str())
                                .unwrap_or("")
                                .to_lowercase();
                            if !s.contains(&ext_str) {
                                return ignore::WalkState::Continue;
                            }
                        }
                        if glob_s.is_some() {
                            let filename = path.file_name().and_then(|s| s.to_str()).unwrap_or("");
                            if !match_filename_glob(filename, &glob_s) {
                                return ignore::WalkState::Continue;
                            }
                        }

                        // Directory entries already carry metadata on Windows.
                        // Keep fresh lookups for resource reservations, links and
                        // Excel. Recheck cached oversized files before excluding
                        // them; opened-file metadata remains the final size check.
                        let excel_extension = path
                            .extension()
                            .and_then(|ext| ext.to_str())
                            .is_some_and(|ext| {
                                ["xlsx", "xlsm", "xls", "xlsb"]
                                    .iter()
                                    .any(|known| ext.eq_ignore_ascii_case(known))
                            });
                        let metadata = size_check_metadata(
                            &entry,
                            include_junctions
                                || is_json
                                || is_archive
                                || is_xml
                                || is_excel
                                || excel_extension,
                            common_file_size_limit,
                        );
                        if let Some(metadata) = metadata {
                            if metadata.len() > common_file_size_limit {
                                let f_path = path.to_string_lossy().to_string();
                                let mut skipped = skip_ref
                                    .lock()
                                    .unwrap_or_else(|poisoned| poisoned.into_inner());
                                skipped.push((
                                    f_path,
                                    encode_skip_reason(
                                        REASON_ERR_TOO_LARGE,
                                        format!("{} bytes", metadata.len()),
                                    ),
                                ));
                                file_counter.fetch_add(1, Ordering::Relaxed);
                                return ignore::WalkState::Continue;
                            }
                        }

                        let _memory_permit = match acquire_structured_permit(
                            &limiter_ref,
                            path,
                            is_json,
                            is_archive,
                            is_xml,
                            is_excel,
                            &stop_ref,
                        ) {
                            Ok(permit) => permit,
                            Err(StructuredPermitError::Cancelled) => {
                                return ignore::WalkState::Quit
                            }
                            Err(StructuredPermitError::InsufficientBudget(estimate)) => {
                                let f_path = path.to_string_lossy().to_string();
                                let mut skipped = skip_ref
                                    .lock()
                                    .unwrap_or_else(|poisoned| poisoned.into_inner());
                                skipped.push((
                                    f_path,
                                    encode_skip_reason(REASON_ERR_RESOURCE_BUDGET, estimate),
                                ));
                                file_counter.fetch_add(1, Ordering::Relaxed);
                                return ignore::WalkState::Continue;
                            }
                        };
                        let res = search_file_internal(InternalSearchParams {
                            encoding,
                            allow_duplicate_json_keys: mode_bits.unwrap_or(0)
                                & types::MODE_ALLOW_DUPLICATE_JSON_KEYS
                                != 0,
                            path,
                            matcher: &matcher,
                            ac: &ac_ref,
                            is_exact,
                            is_json,
                            is_archive,
                            is_xml,
                            is_excel,
                            exclude_hidden,
                            exclude_binary,

                            stop_flag: stop_ref.clone(),
                            max_per_file: max_per_file.unwrap_or(10_000),
                            max_json_depth: max_json_depth.unwrap_or(20_000),
                            max_json_size: max_json_size
                                .unwrap_or(DEFAULT_MAX_SEARCH_FILE_SIZE_BYTES),
                        });

                        if let Some(r) = res {
                            let f_path = path.to_string_lossy().to_string();
                            match r {
                                Ok(matches) => {
                                    if !matches.is_empty() {
                                        if let Some(tx) = &tx_worker {
                                            let _ = tx.send((f_path, matches));
                                        } else {
                                            let mut g = res_ref
                                                .lock()
                                                .unwrap_or_else(|poisoned| poisoned.into_inner());
                                            g.push((f_path, matches));
                                        }
                                    }
                                }
                                Err(e) => {
                                    let mut s = skip_ref
                                        .lock()
                                        .unwrap_or_else(|poisoned| poisoned.into_inner());
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
        let _ = monitor_done_tx.send(());
        drop(tx_main);
        let dispatcher_handle = results_dispatcher.map(|(tx, handle)| {
            drop(tx);
            handle
        });
        join_auxiliary_threads(dispatcher_handle, monitor_handle)
    });

    let final_res = to_python_file_matches(
        results
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .clone(),
    );
    let final_skip = skipped
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
        .clone();
    if let Some(error) = merge_completion_errors(callback_state.error_message(), thread_error) {
        return Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(error));
    }
    Ok((final_res, final_skip))
}

#[pyfunction]
#[pyo3(signature = (file_list, search_string, mode_bits=None, exclude_hidden=false, stop_event=None, progress_callback=None, results_callback=None, batch_size=None, _flush_ms=None, max_per_file=None,  max_json_depth=None, max_json_size=None, options=None, **_kwargs))]
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

    mut max_json_depth: Option<usize>,
    mut max_json_size: Option<u64>,
    options: Option<Py<SearchOptions>>,
    _kwargs: Option<PyObject>,
) -> Result<(FileMatches, SkippedEntries), PyErr> {
    validate_query_length(&search_string)?;
    let mut structured_memory_budget = None;
    let mut encoding = None;
    let mut include_junctions = false;
    if let Some(config) = options.as_ref() {
        let config = config.bind(py).borrow();
        encoding = crate::utils::requested_encoding(config.encoding.as_deref());
        include_junctions = config.include_junctions;
        if config.mode_bits.is_some() {
            mode_bits = config.mode_bits;
        }
        exclude_hidden = config.exclude_hidden;
        if config.stop_event.is_some() {
            stop_event = config.stop_event.as_ref().map(|event| event.clone_ref(py));
        }
        if config.progress_callback.is_some() {
            progress_callback = config
                .progress_callback
                .as_ref()
                .map(|callback| callback.clone_ref(py));
        }
        if config.results_callback.is_some() {
            results_callback = config
                .results_callback
                .as_ref()
                .map(|callback| callback.clone_ref(py));
        }
        if config.batch_size.is_some() {
            batch_size = config.batch_size;
        }
        if config.flush_ms.is_some() {
            _flush_ms = config.flush_ms;
        }
        if config.max_per_file.is_some() {
            max_per_file = config.max_per_file;
        }

        if config.max_json_depth.is_some() {
            max_json_depth = config.max_json_depth;
        }
        if config.max_json_size.is_some() {
            max_json_size = config.max_json_size;
        }
        structured_memory_budget = config.structured_memory_budget;
    }
    let _ = include_junctions; // Candidate list was already selected by the caller.

    let stop_flag = Arc::new(AtomicBool::new(false));
    let structured_limiter = structured_memory_budget
        .filter(|budget| *budget > 0)
        .map(|budget| Arc::new(StructuredMemoryLimiter::new(budget)));
    let done_flag = Arc::new(AtomicBool::new(false));
    let progress_counter = Arc::new(AtomicU64::new(0));
    let callback_state = Arc::new(CallbackState::new());
    let (monitor_done_tx, monitor_done_rx) = std::sync::mpsc::channel::<()>();

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
                            if let Ok(true) = res.extract::<bool>() {
                                return true;
                            }
                        }
                    }
                    if let Some(cb) = &progress_cb_mon {
                        if let Err(error) = cb
                            .bind(py)
                            .call1((progress_cnt_mon.load(Ordering::Relaxed),))
                        {
                            callback_state_mon.record_error(&stop_flag_mon, "progress", error);
                            return true;
                        }
                    }
                    false
                });
                if is_stopped {
                    stop_flag_mon.store(true, Ordering::SeqCst);
                    break;
                }
                match monitor_done_rx
                    .recv_timeout(std::time::Duration::from_millis(MONITOR_INTERVAL_MS))
                {
                    Ok(()) | Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => break,
                    Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {}
                }
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
                            if done_dispatcher.load(Ordering::Relaxed) && rx.is_empty() {
                                break;
                            }
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
                                callback_state_dispatcher.record_error(
                                    &stop_flag_dispatcher,
                                    "results",
                                    error,
                                );
                            }
                        }
                    });
                }
                // 중지된 검색도 이미 큐에 적재된 결과는 모두 전달합니다.
                if done_dispatcher.load(Ordering::Relaxed) && rx.is_empty() {
                    break;
                }
            }
        });
        (tx, handle)
    });

    py.allow_threads(|| {
        let (is_json, is_xml, is_exact, is_excel, exclude_binary) = parse_search_mode(mode_bits);
        let is_archive = mode_bits.unwrap_or(0) & types::MODE_ARCHIVE != 0;
        let common_file_size_limit = max_json_size.unwrap_or(DEFAULT_MAX_SEARCH_FILE_SIZE_BYTES);
        let (matcher, ac) = build_search_matchers(&search_string, is_xml, is_json || is_archive)?;
        let ac = Arc::new(ac);

        let results = Arc::new(Mutex::new(Vec::new()));
        let skipped = Arc::new(Mutex::new(Vec::new()));
        let tx_main = results_dispatcher.as_ref().map(|(tx, _)| tx.clone());

        file_list.into_par_iter().for_each(|f_path| {
            if stop_flag.load(Ordering::Relaxed) {
                return;
            }
            let path = Path::new(&f_path);
            if let Ok(metadata) = std::fs::metadata(path) {
                if should_exclude_recycle_path(path) {
                    progress_counter.fetch_add(1, Ordering::Relaxed);
                    return;
                }
                if exclude_hidden {
                    #[cfg(windows)]
                    {
                        use std::os::windows::fs::MetadataExt;
                        if (metadata.file_attributes() & 0x02) != 0 {
                            progress_counter.fetch_add(1, Ordering::Relaxed);
                            return;
                        }
                    }
                }
                if metadata.len() > common_file_size_limit {
                    let mut entries = skipped
                        .lock()
                        .unwrap_or_else(|poisoned| poisoned.into_inner());
                    entries.push((
                        f_path.clone(),
                        encode_skip_reason(
                            REASON_ERR_TOO_LARGE,
                            format!("{} bytes", metadata.len()),
                        ),
                    ));
                    progress_counter.fetch_add(1, Ordering::Relaxed);
                    return;
                }
            }
            let _memory_permit = match acquire_structured_permit(
                &structured_limiter,
                path,
                is_json,
                is_archive,
                is_xml,
                is_excel,
                &stop_flag,
            ) {
                Ok(permit) => permit,
                Err(StructuredPermitError::Cancelled) => return,
                Err(StructuredPermitError::InsufficientBudget(estimate)) => {
                    let mut entries = skipped
                        .lock()
                        .unwrap_or_else(|poisoned| poisoned.into_inner());
                    entries.push((
                        f_path,
                        encode_skip_reason(REASON_ERR_RESOURCE_BUDGET, estimate),
                    ));
                    progress_counter.fetch_add(1, Ordering::Relaxed);
                    return;
                }
            };
            let res = search_file_internal(InternalSearchParams {
                encoding,
                allow_duplicate_json_keys: mode_bits.unwrap_or(0)
                    & types::MODE_ALLOW_DUPLICATE_JSON_KEYS
                    != 0,
                path,
                matcher: &matcher,
                ac: &ac,
                is_exact,
                is_json,
                is_archive,
                is_xml,
                is_excel,
                exclude_hidden,
                exclude_binary,

                stop_flag: stop_flag.clone(),
                max_per_file: max_per_file.unwrap_or(10_000),
                max_json_depth: max_json_depth.unwrap_or(20_000),
                max_json_size: max_json_size.unwrap_or(DEFAULT_MAX_SEARCH_FILE_SIZE_BYTES),
            });

            if let Some(r) = res {
                match r {
                    Ok(m) => {
                        if let Some(tx) = &tx_main {
                            let _ = tx.send((f_path, m));
                        } else {
                            let mut g = results
                                .lock()
                                .unwrap_or_else(|poisoned| poisoned.into_inner());
                            g.push((f_path, m));
                        }
                    }
                    Err(e) => {
                        let mut g = skipped
                            .lock()
                            .unwrap_or_else(|poisoned| poisoned.into_inner());
                        g.push((f_path, e));
                    }
                }
            }
            progress_counter.fetch_add(1, Ordering::Relaxed);
        });

        // B1: done_flag를 먼저 설정하여 dispatcher와 monitor 스레드가 종료 루프에 진입할 수 있도록 합니다.
        done_flag.store(true, Ordering::SeqCst);
        let _ = monitor_done_tx.send(());
        drop(tx_main);
        let dispatcher_handle = results_dispatcher.map(|(tx, handle)| {
            drop(tx);
            handle
        });
        let thread_error = join_auxiliary_threads(dispatcher_handle, monitor_handle);
        let final_res = to_python_file_matches(
            results
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner())
                .clone(),
        );
        let final_skip = skipped
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .clone();
        if let Some(error) = merge_completion_errors(callback_state.error_message(), thread_error) {
            return Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(error));
        }
        Ok((final_res, final_skip))
    })
}

// Kept in sync with Constants.MAX_SEARCH_QUERY_LENGTH by contract tests.
const MAX_SEARCH_QUERY_LENGTH: usize = 1000;

fn validate_query_length(query: &str) -> PyResult<()> {
    if query.chars().take(MAX_SEARCH_QUERY_LENGTH + 1).count() > MAX_SEARCH_QUERY_LENGTH {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "Search text is limited to {MAX_SEARCH_QUERY_LENGTH} characters (received {}).",
            query.chars().count()
        )));
    }
    Ok(())
}

/// 검색어 하나로 공통 비교기와, 원본 위치 찾기·바이너리 개수용 자동자를 만듭니다.
///
/// 비교(일치 여부)는 항상 비교기(`key()` 기준)가 정합니다. 자동자는 원본 바이트에서 위치를 찾거나
/// 바이너리 파일의 일치 개수를 셀 때만 쓰며, `key(검색어)`와 정규화한 검색어의 표기
/// (대소문자·XML 엔티티·JSON 이스케이프 변형)를 모두 담습니다.
fn build_search_matchers(
    pattern: &str,
    is_xml: bool,
    is_json_like: bool,
) -> PyResult<(textkey::Matcher, aho_corasick::AhoCorasick)> {
    let matcher =
        textkey::Matcher::new(pattern).map_err(PyErr::new::<pyo3::exceptions::PyValueError, _>)?;
    let norm_pattern = crate::utils::normalize_unicode(pattern);
    let mut patterns = generate_search_patterns(matcher.pat_key(), is_xml, is_json_like);
    patterns.extend(generate_search_patterns(
        &norm_pattern,
        is_xml,
        is_json_like,
    ));
    let mut seen = HashSet::new();
    patterns.retain(|pattern| seen.insert(pattern.clone()));
    let ac = AhoCorasickBuilder::new()
        .ascii_case_insensitive(true)
        .match_kind(MatchKind::LeftmostFirst)
        .build(&patterns)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("{}", e)))?;
    Ok((matcher, ac))
}

#[pymodule]
fn sf_engine(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // [Stability] 커스텀 패닉 훅 설치:
    // Excel 파서의 unwind 패닉은 해당 처리 경계에서 사용자용 오류로 변환합니다.
    // 원시 패닉 출력 억제는 예외 처리를 대신하지 않습니다.
    // 보조 스레드는 GIL을 해제한 상태에서 모두 정리하고 join 실패를 오류로 전달합니다.
    std::panic::set_hook(Box::new(|_info| {
        // 기존 출력 억제 정책 유지. 모든 패닉이 복구된다는 의미는 아닙니다.
    }));

    m.add_class::<SearchMatch>()?;
    m.add_class::<SearchOptions>()?;
    m.add_class::<excel_search::SparseExcelWorkbook>()?;
    m.add_function(wrap_pyfunction!(search_file, m)?)?;
    m.add_function(wrap_pyfunction!(search_dir, m)?)?;
    m.add_function(wrap_pyfunction!(search_files_list, m)?)?;
    m.add("API_VERSION", 8)?;
    m.add("MAX_SEARCH_QUERY_LENGTH", MAX_SEARCH_QUERY_LENGTH)?;
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
            let hit_len = match_len
                .unwrap_or(0)
                .min(line_end.saturating_sub(hit_start));
            let desired_start = hit_start.saturating_sub(PREVIEW_MAX_BYTES / 2);
            preview_start = desired_start.max(start);
            preview_end = (preview_start + PREVIEW_MAX_BYTES).min(line_end);
            if preview_end < hit_start.saturating_add(hit_len) {
                preview_end = (hit_start.saturating_add(hit_len)).min(line_end);
                preview_start = preview_end.saturating_sub(PREVIEW_MAX_BYTES).max(start);
            }
        }

        // UTF-8 문자의 중간을 자르지 않도록 경계를 보정합니다.
        while preview_start > start && (mmap[preview_start] & 0xC0) == 0x80 {
            preview_start -= 1;
        }
        while preview_end < line_end && (mmap[preview_end] & 0xC0) == 0x80 {
            preview_end += 1;
        }

        let preview_bytes = &mmap[preview_start..preview_end];
        let preview_text = match simdutf8::basic::from_utf8(preview_bytes) {
            Ok(s) => s.trim().to_string(),
            Err(_) => String::from_utf8_lossy(preview_bytes).trim().to_string(),
        };
        let prefix = if preview_start > start { "..." } else { "" };
        let suffix = if preview_end < line_end { "..." } else { "" };
        return format!("{MATCH_META_LONG_LINE_PREFIX}{prefix}{preview_text}{suffix}");
    }
    let line_bytes = &mmap[start..line_end];
    match simdutf8::basic::from_utf8(line_bytes) {
        Ok(s) => s.trim().to_string(),
        Err(_) => String::from_utf8_lossy(line_bytes).trim().to_string(),
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn auxiliary_threads_join_normally_without_an_error() {
        let completed = Arc::new(AtomicU64::new(0));
        let first = completed.clone();
        let second = completed.clone();
        let dispatcher = std::thread::spawn(move || {
            first.fetch_add(1, Ordering::SeqCst);
        });
        let monitor = std::thread::spawn(move || {
            second.fetch_add(1, Ordering::SeqCst);
        });
        assert!(join_auxiliary_threads(Some(dispatcher), Some(monitor)).is_none());
        assert_eq!(completed.load(Ordering::SeqCst), 2);
        assert!(join_auxiliary_threads(None, None).is_none());
    }

    #[test]
    fn dispatcher_panic_does_not_skip_joining_the_monitor() {
        let (release, wait) = std::sync::mpsc::channel::<()>();
        let (finished, received) = std::sync::mpsc::channel();
        let completed = Arc::new(AtomicBool::new(false));
        let monitor_completed = completed.clone();
        let dispatcher = std::thread::spawn(|| panic!("test dispatcher failure"));
        let monitor = std::thread::spawn(move || {
            wait.recv().unwrap();
            monitor_completed.store(true, Ordering::SeqCst);
        });
        let cleanup = std::thread::spawn(move || {
            finished
                .send(join_auxiliary_threads(Some(dispatcher), Some(monitor)))
                .unwrap();
        });
        assert!(matches!(
            received.recv_timeout(std::time::Duration::from_millis(50)),
            Err(std::sync::mpsc::RecvTimeoutError::Timeout)
        ));
        release.send(()).unwrap();
        let error = received
            .recv_timeout(std::time::Duration::from_secs(2))
            .unwrap()
            .unwrap();
        cleanup.join().unwrap();
        assert!(completed.load(Ordering::SeqCst));
        assert_eq!(error, "results dispatcher terminated unexpectedly");
    }

    #[test]
    fn monitor_panic_is_reported_without_a_dispatcher() {
        let monitor = std::thread::spawn(|| panic!("test monitor failure"));
        assert_eq!(
            join_auxiliary_threads(None, Some(monitor)).as_deref(),
            Some("monitor thread terminated unexpectedly")
        );
    }

    #[test]
    fn completion_errors_preserve_both_thread_failures_and_callback_error() {
        let dispatcher = std::thread::spawn(|| panic!("test dispatcher failure"));
        let monitor = std::thread::spawn(|| panic!("test monitor failure"));
        let thread_error = join_auxiliary_threads(Some(dispatcher), Some(monitor));
        assert_eq!(
            merge_completion_errors(Some("results callback failed: probe".into()), thread_error)
                .as_deref(),
            Some("results callback failed: probe; results dispatcher terminated unexpectedly; monitor thread terminated unexpectedly")
        );
    }

    #[test]
    fn completion_errors_preserve_existing_callback_only_behavior() {
        assert!(merge_completion_errors(None, None).is_none());
        assert_eq!(
            merge_completion_errors(Some("original callback error".into()), None).as_deref(),
            Some("original callback error")
        );
        assert_eq!(
            merge_completion_errors(None, Some("monitor thread terminated unexpectedly".into()))
                .as_deref(),
            Some("monitor thread terminated unexpectedly")
        );
    }
    use super::*;

    #[test]
    fn walk_errors_preserve_paths_and_individual_causes() {
        let errors = ignore::Error::Partial(vec![
            ignore::Error::WithPath {
                path: "missing-folder".into(),
                err: Box::new(ignore::Error::WithDepth {
                    depth: 2,
                    err: Box::new(ignore::Error::Io(std::io::Error::from_raw_os_error(2))),
                }),
            },
            ignore::Error::WithPath {
                path: "restricted-folder".into(),
                err: Box::new(ignore::Error::Io(std::io::Error::from_raw_os_error(5))),
            },
        ]);
        let entries = walk_error_entries(&errors);
        assert_eq!(entries.len(), 2);
        assert_eq!(entries[0].0, "missing-folder");
        assert_eq!(entries[1].0, "restricted-folder");
        assert!(entries[0].1.contains("os error 2"));
        assert!(!entries[0].1.contains("os error 5"));
        assert!(entries[1].1.contains("os error 5"));
    }

    #[test]
    fn walk_errors_without_paths_remain_distinct_and_loops_identify_child() {
        let error = ignore::Error::Io(std::io::Error::from_raw_os_error(5));
        assert_ne!(
            walk_error_entries(&error)[0].0,
            walk_error_entries(&error)[0].0
        );
        let error = ignore::Error::Loop {
            ancestor: "root".into(),
            child: "root/link".into(),
        };
        assert_eq!(walk_error_entries(&error)[0].0, "root/link");
    }

    #[test]
    fn query_length_counts_unicode_characters_not_bytes() {
        for value in ["a", "가", "😀"] {
            assert!(validate_query_length(&value.repeat(MAX_SEARCH_QUERY_LENGTH)).is_ok());
            assert!(validate_query_length(&value.repeat(MAX_SEARCH_QUERY_LENGTH + 1)).is_err());
        }
    }
    use std::fs;

    #[test]
    fn normal_search_walker_ignores_gitignore_and_ignore_rules() {
        let unique = SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!("sf-gitignore-test-{unique}"));
        fs::create_dir_all(&root).unwrap();
        fs::write(root.join(".gitignore"), "ignored.json\n").unwrap();
        fs::write(root.join(".ignore"), "custom-ignored.json\n").unwrap();
        let ignored_file = root.join("ignored.json");
        fs::write(&ignored_file, "{}\n").unwrap();
        let custom_ignored_file = root.join("custom-ignored.json");
        fs::write(&custom_ignored_file, "{}\n").unwrap();

        let found: HashSet<_> = search_walk_builder(root.as_path(), false, false)
            .build()
            .filter_map(Result::ok)
            .map(|entry| entry.path().to_path_buf())
            .collect();

        let _ = fs::remove_dir_all(&root);
        assert!(
            found.contains(&ignored_file),
            "normal search must ignore .gitignore rules"
        );
        assert!(
            found.contains(&custom_ignored_file),
            "normal search must ignore .ignore rules"
        );
    }

    fn build_test_ac(pattern: &str) -> aho_corasick::AhoCorasick {
        AhoCorasickBuilder::new()
            .ascii_case_insensitive(true)
            .build([pattern])
            .unwrap()
    }

    #[test]
    fn non_utf8_chunk_boundaries_preserve_lines_and_matches() {
        for encoding in [UTF_16LE, UTF_16BE, encoding_rs::EUC_KR] {
            // Include a split CRLF, a split hit, short lines, and a final long line.
            let text = format!(
                "{}\r\nneedle\r\n{}needle\n{}needle",
                "x".repeat(32767),
                "한\n".repeat(20000),
                "x".repeat(65533)
            );
            let bytes = if encoding == encoding_rs::EUC_KR {
                encoding.encode(&text).0.into_owned()
            } else {
                text.encode_utf16()
                    .flat_map(|unit| {
                        if encoding == UTF_16LE {
                            unit.to_le_bytes()
                        } else {
                            unit.to_be_bytes()
                        }
                    })
                    .collect::<Vec<_>>()
            };
            for exact in [false, true] {
                let expected: Vec<_> = text
                    .lines()
                    .enumerate()
                    .filter_map(|(i, line)| {
                        let matched = if exact {
                            line == "needle"
                        } else {
                            line.contains("needle")
                        };
                        matched.then(|| {
                            let start = line.find("needle");
                            (
                                i + 1,
                                extract_line_content_bytes(
                                    line.as_bytes(),
                                    0,
                                    line.len(),
                                    start,
                                    Some(6),
                                ),
                                None,
                                None,
                            )
                        })
                    })
                    .collect();
                let mut actual = Vec::new();
                search_non_utf8_chunks(
                    &bytes,
                    encoding,
                    &textkey::Matcher::new("needle").unwrap(),
                    exact,
                    &Arc::new(AtomicBool::new(false)),
                    5000,
                    &mut actual,
                );
                assert_eq!(actual, expected, "encoding={encoding:?}, exact={exact}");
            }
        }
    }

    #[test]
    fn text_encoding_detection_preserves_full_search_policy() {
        let mut bytes = b"needle\n".to_vec();
        bytes.resize(65536, b'a');
        bytes.extend_from_slice(&encoding_rs::EUC_KR.encode("한글").0);
        assert_eq!(detect_text_search_encoding(&bytes, "needle", false), UTF_8);
        assert_eq!(
            detect_text_search_encoding(&bytes, "needle", true),
            encoding_rs::EUC_KR
        );
        assert_eq!(
            detect_text_search_encoding(&bytes, "한글", false),
            encoding_rs::EUC_KR
        );
        let utf16: Vec<u8> = "needle".encode_utf16().flat_map(u16::to_le_bytes).collect();
        assert_eq!(detect_text_search_encoding(&utf16, "n", false), UTF_16LE);
    }

    #[test]
    fn decoded_lines_count_each_occurrence_and_obey_limit() {
        let text = "needle needle needle 한글";
        for encoding in [UTF_16LE, UTF_16BE, encoding_rs::EUC_KR] {
            let bytes = if encoding == encoding_rs::EUC_KR {
                encoding.encode(text).0.into_owned()
            } else {
                text.encode_utf16()
                    .flat_map(|unit| {
                        if encoding == UTF_16LE {
                            unit.to_le_bytes()
                        } else {
                            unit.to_be_bytes()
                        }
                    })
                    .collect()
            };
            for limit in [1, 2, 3, 100] {
                let mut results = Vec::new();
                search_non_utf8_chunks(
                    &bytes,
                    encoding,
                    &textkey::Matcher::new("needle").unwrap(),
                    false,
                    &Arc::new(AtomicBool::new(false)),
                    limit,
                    &mut results,
                );
                assert_eq!(results.len(), 3.min(limit + 1));
                assert!(results.iter().all(|m| m.0 == 1));
            }
        }
    }

    #[test]
    fn non_utf8_cancelled_search_preserves_previously_collected_results() {
        let bytes = "needle\n"
            .repeat(20000)
            .encode_utf16()
            .flat_map(u16::to_le_bytes)
            .collect::<Vec<_>>();
        let original = vec![(1, "already delivered".to_string(), None, None)];
        let mut actual = original.clone();
        search_non_utf8_chunks(
            &bytes,
            UTF_16LE,
            &textkey::Matcher::new("needle").unwrap(),
            false,
            &Arc::new(AtomicBool::new(true)),
            5000,
            &mut actual,
        );
        assert_eq!(actual, original);
    }

    #[test]
    fn mmap_failures_use_the_correct_protocol_code() {
        assert_eq!(
            encode_skip_reason(REASON_ERR_MMAP, "failed"),
            "ERR_MMAP|failed"
        );
    }

    #[test]
    fn long_line_preview_includes_match_region() {
        let stop_flag = Arc::new(AtomicBool::new(false));
        let mut data = vec![b'A'; 12_000];
        data[8_000..8_006].copy_from_slice(b"needle");
        let matches = do_search_with_mmap(
            &data,
            UTF_8,
            &textkey::Matcher::new("needle").unwrap(),
            false,
            &stop_flag,
            5000,
        );
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
    fn match_limit_preserves_json_depth_notice_without_counting_it() {
        let matches = vec![
            (1, "one".to_string(), None, None),
            (2, "two".to_string(), None, None),
            (3, "three".to_string(), None, None),
            (
                0,
                format!(
                    "{}{}",
                    crate::json_search::JSON_DEPTH_LIMIT_MARKER_PREFIX,
                    20
                ),
                None,
                None,
            ),
        ];

        let limited = apply_match_limit(matches, 2);

        assert_eq!(limited.len(), 4);
        assert_eq!(limited[0].1, "one");
        assert_eq!(limited[1].1, "two");
        assert_eq!(limited[2].1, MATCH_META_TRUNCATED);
        assert_eq!(limited[3].1, "__SF_JSON_DEPTH_LIMIT__|20");
    }

    #[test]
    fn match_limit_does_not_treat_metadata_as_an_extra_match() {
        let matches = vec![
            (1, "one".to_string(), None, None),
            (2, "two".to_string(), None, None),
            (
                0,
                "__SF_EXCEL_SHEET_ERR__|Sheet2|parse failure".to_string(),
                None,
                None,
            ),
        ];

        let limited = apply_match_limit(matches, 2);

        assert_eq!(limited.len(), 3);
        assert!(!limited.iter().any(|item| item.1 == MATCH_META_TRUNCATED));
        assert!(limited[2].1.starts_with("__SF_EXCEL_SHEET_ERR__|"));
    }

    #[test]
    fn structured_memory_limiter_rejects_oversized_request_and_releases_permit() {
        let limiter = Arc::new(StructuredMemoryLimiter::new(100));
        let stop_flag = Arc::new(AtomicBool::new(false));

        assert!(matches!(
            limiter.acquire(101, &stop_flag),
            Err(StructuredPermitError::InsufficientBudget(101))
        ));

        let permit = match limiter.acquire(80, &stop_flag) {
            Ok(permit) => permit,
            Err(_) => panic!("a request within the budget must be accepted"),
        };
        assert_eq!(*limiter.reserved.lock().unwrap(), 80);
        drop(permit);
        assert_eq!(*limiter.reserved.lock().unwrap(), 0);
    }

    #[test]
    fn plain_files_do_not_require_structured_memory_metadata() {
        assert_eq!(
            estimated_structured_memory(
                Path::new("definitely-missing.txt"),
                false,
                false,
                false,
                false
            ),
            0
        );
    }

    #[test]
    fn recycle_bin_component_detection_is_case_insensitive() {
        assert!(has_recycle_bin_component(Path::new(
            r"D:\$Recycle.Bin\canary\file.xlsx"
        )));
        assert!(should_exclude_recycle_path(Path::new(
            r"D:\$Recycle.Bin\canary\file.xlsx"
        )));
        assert!(!has_recycle_bin_component(Path::new(
            r"D:\documents\file.xlsx"
        )));
        assert!(is_direct_child_of_filesystem_root(Path::new(
            r"D:\documents"
        )));
        assert!(!is_direct_child_of_filesystem_root(Path::new(
            r"D:\documents\nested"
        )));
    }

    #[test]
    fn exact_match_handles_final_carriage_return() {
        let stop_flag = Arc::new(AtomicBool::new(false));
        let data = b"prefix\nneedle\r";
        let matches = do_search_with_mmap(
            data,
            UTF_8,
            &textkey::Matcher::new("needle").unwrap(),
            true,
            &stop_flag,
            5000,
        );
        assert_eq!(matches.len(), 1);
        assert_eq!(matches[0].0, 2);
    }

    #[test]
    fn invalid_utf8_is_not_a_synthetic_exact_match() {
        let matcher = textkey::Matcher::new("INVALID_UTF8").unwrap();
        assert!(!matcher.equals_trimmed(&String::from_utf8_lossy(b"\xff")));
        assert!(matcher.equals_trimmed("invalid_utf8"));
    }

    #[test]
    fn cached_size_checks_revalidate_shrinking_files_and_opened_file_growth() {
        let unique = SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!("sf-size-cache-{unique}"));
        fs::create_dir_all(&root).unwrap();
        let path = root.join("sample.txt");
        fs::write(&path, b"formerly too big").unwrap();
        let entry = search_walk_builder(&root, false, false)
            .build()
            .filter_map(Result::ok)
            .find(|entry| entry.path() == path)
            .unwrap();
        fs::write(&path, b"needle").unwrap();
        assert_eq!(size_check_metadata(&entry, false, 8).unwrap().len(), 6);

        let small_entry = search_walk_builder(&root, false, false)
            .build()
            .filter_map(Result::ok)
            .find(|entry| entry.path() == path)
            .unwrap();
        fs::write(&path, b"needle has grown").unwrap();
        assert_eq!(
            size_check_metadata(&small_entry, true, 8).unwrap().len(),
            16
        );
        let ac = build_test_ac("needle");
        let result = search_file_internal(InternalSearchParams {
            encoding: None,
            allow_duplicate_json_keys: false,
            path: &path,
            matcher: &textkey::Matcher::new("needle").unwrap(),
            ac: &ac,
            is_exact: false,
            is_json: false,
            is_archive: false,
            is_xml: false,
            is_excel: false,
            exclude_hidden: false,
            exclude_binary: false,
            stop_flag: Arc::new(AtomicBool::new(false)),
            max_per_file: 10,
            max_json_depth: 100,
            max_json_size: 8,
        });
        assert_eq!(result.unwrap().unwrap_err(), "ERR_TOO_LARGE|16 bytes");
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn ascii_exact_comparison_follows_the_key_contract_and_white_space() {
        // 정확히 일치 = key(줄)의 앞뒤 White_Space를 지운 결과가 key(검색어)와 같음.
        for query in ["s", "ſ", "K", "\u{212a}", "ss", "ß", "needle", "가"] {
            let matcher = textkey::Matcher::new(query).unwrap();
            let pattern = textkey::key_reference(query);
            for byte in 0..=127u8 {
                for text in [
                    vec![byte],
                    vec![byte, b's', byte],
                    vec![byte, b'K', byte],
                    vec![byte, b'n', b'e', b'e', b'd', b'l', b'e', byte],
                ] {
                    let line = std::str::from_utf8(&text).unwrap();
                    let reference = textkey::key_reference(line).trim() == pattern;
                    assert_eq!(
                        matcher.equals_trimmed(line),
                        reference,
                        "byte={byte}, query={query:?}, text={text:?}"
                    );
                }
            }
        }
    }

    /// 기준 구현: B안으로 줄을 나눠 줄마다 key를 적용하고, 겹치지 않는 일치 개수를 셉니다.
    fn reference_text_results(text: &str, pattern: &str, exact: bool) -> Vec<(usize, usize)> {
        let pattern_key = textkey::key_reference(pattern);
        let mut expected = Vec::new();
        for (index, line) in textkey::split_lines(text).enumerate() {
            let line_key = textkey::key_reference(line);
            let count = if exact {
                usize::from(line_key.trim() == pattern_key)
            } else {
                line_key.matches(pattern_key.as_str()).count()
            };
            if count > 0 {
                expected.push((index + 1, count));
            }
        }
        expected
    }

    #[test]
    fn utf8_text_search_matches_the_line_by_line_reference() {
        let alphabet: Vec<char> = "aAeEkKsStT \n\r\r\n-\u{DF}\u{1E9E}\u{17F}\u{212A}\u{FB06}\u{130}\u{301}\u{308}\u{30C}\u{316}\u{1F0}\u{C9}\u{E9}\u{410}\u{430}\u{3A3}\u{3C3}\u{AC00}\u{AC01}\u{AC1C}\u{1100}\u{1161}\u{11A8}\u{3B9}\u{390}"
            .chars()
            .collect();
        let patterns = [
            "ka",
            "ss",
            "st",
            "e",
            "k",
            "te",
            "\u{E9}",
            "\u{AC00}",
            "\u{AC01}",
            "\u{430}\u{430}",
            "\u{3C3}",
            "\u{1F0}",
            "\u{1100}\u{1161}",
            "a\u{AC00}",
            "E\u{301}",
            "\u{3B9}",
        ];
        let mut state = 0x1234_5678_9ABC_DEF1u64;
        let mut next = |bound: usize| {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            (state % bound as u64) as usize
        };
        let stop_flag = Arc::new(AtomicBool::new(false));
        for round in 0..30_000 {
            let text: String = (0..next(40))
                .map(|_| alphabet[next(alphabet.len())])
                .collect();
            let pattern = patterns[round % patterns.len()];
            let matcher = textkey::Matcher::new(pattern).unwrap();
            for exact in [false, true] {
                let results = do_search_with_mmap(
                    text.as_bytes(),
                    UTF_8,
                    &matcher,
                    exact,
                    &stop_flag,
                    10_000,
                );
                let mut actual: Vec<(usize, usize)> = Vec::new();
                for (line, _content, offset, length) in &results {
                    if let (Some(offset), Some(length)) = (offset, length) {
                        let piece = &text.as_bytes()[*offset..offset + length];
                        assert_eq!(
                            textkey::key_reference(std::str::from_utf8(piece).unwrap()),
                            textkey::key_reference(pattern),
                            "wrong position text={text:?} pattern={pattern:?}"
                        );
                    }
                    match actual.last_mut() {
                        Some((last, count)) if last == line => *count += 1,
                        _ => actual.push((*line, 1)),
                    }
                }
                assert_eq!(
                    actual,
                    reference_text_results(&text, pattern, exact),
                    "text={text:?} pattern={pattern:?} exact={exact}"
                );
            }
        }
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
        let _ = ac;
        let matcher = textkey::Matcher::new("needle").unwrap();
        let matches = do_search_with_mmap(&data, encoding, &matcher, false, &stop_flag, 5000);
        assert_eq!(matches.len(), 1);
        assert!(matches[0].1.contains("needle"));
    }
}
