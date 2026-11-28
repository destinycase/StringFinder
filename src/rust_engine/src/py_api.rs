//! Python에서 부르는 함수(search_file, search_dir, 강조 위치, 인코딩 판별)와 넘길 모양 바꾸기.
//! 8.1.5(엔진 1.0.1)에서 lib.rs에서 옮겼습니다. 함수 본문은 그대로입니다.

use super::*;

pub(crate) struct CallbackState {
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

pub(crate) fn to_python_matches(matches: Vec<RawMatch>) -> Vec<SearchMatch> {
    matches.into_iter().map(SearchMatch::from).collect()
}

pub(crate) fn to_python_file_matches(results: RawFileMatches) -> FileMatches {
    results
        .into_iter()
        .map(|(path, matches)| (path, to_python_matches(matches)))
        .collect()
}

pub(crate) fn encode_skip_reason<T: std::fmt::Display>(code: &str, detail: T) -> String {
    format!("{}|{}", code, detail)
}

pub(crate) fn encode_xml_skip_reason(error: XmlSearchError) -> String {
    match error {
        XmlSearchError::Parse(detail) => encode_skip_reason(REASON_ERR_XML_PARSE, detail),
        XmlSearchError::UnsupportedDtd(detail) => {
            encode_skip_reason(REASON_ERR_XML_UNSUPPORTED_DTD, detail)
        }
    }
}

pub(crate) fn encode_excel_skip_reason(error: ExcelFileError) -> String {
    match error {
        ExcelFileError::Open(detail) => encode_skip_reason(REASON_ERR_EXCEL_PROCESS, detail),
        ExcelFileError::Panic(detail) => encode_skip_reason(REASON_ERR_EXCEL_PANIC, detail),
    }
}

#[pyfunction]
#[pyo3(signature = (path, pattern, mode_bits=None, stop_event=None, max_per_file=10000,  max_json_depth=20000, max_json_size=1073741824, options=None))]
#[allow(clippy::too_many_arguments)]
pub(crate) fn search_file(
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
    if let Some(config) = options.as_ref() {
        let config = config.bind(py).borrow();
        encoding = crate::utils::requested_encoding(config.encoding.as_deref());
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

    let (is_json, is_xml, is_exact, is_excel, exclude_binary) = parse_search_mode(mode_bits);
    let is_archive = mode_bits.unwrap_or(0) & types::MODE_ARCHIVE != 0;
    let (matcher, ac) = build_search_matchers(
        exact_search_pattern(&pattern, is_exact),
        is_xml,
        is_json || is_archive,
    )?;

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
        if should_exclude_recycle_path(Path::new(&path)) {
            return None;
        }
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
        Some(Err(e)) => Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e)),
        None => Ok(Vec::new()),
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
    let mut max_total_matches: Option<u64> = None;
    if let Some(config) = options.as_ref() {
        let config = config.bind(py).borrow();
        encoding = crate::utils::requested_encoding(config.encoding.as_deref());
        include_junctions = config.include_junctions;
        max_total_matches = config.max_total_matches;
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
    let (matcher, ac) = build_search_matchers(
        exact_search_pattern(&pattern, is_exact),
        is_xml,
        is_json || is_archive,
    )?;
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
    // 전체 상한을 넘을 만큼 보냈으면 더 찾지 않습니다(검수 S-6). 대기열에 쌓이는 결과를 줄입니다.
    let sent_matches = Arc::new(AtomicU64::new(0));

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
                    let sent_ref = sent_matches.clone();

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
                        let size_limit =
                            file_size_limit_for(path, is_archive, common_file_size_limit);
                        if let Some(metadata) = metadata {
                            if metadata.len() > size_limit {
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
                            encoding,
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
                                        if let Some(limit) = max_total_matches {
                                            let real = matches
                                                .iter()
                                                .filter(|m| !m.4.is_metadata())
                                                .count()
                                                as u64;
                                            let total =
                                                sent_ref.fetch_add(real, Ordering::Relaxed) + real;
                                            if total > limit {
                                                stop_ref.store(true, Ordering::SeqCst);
                                            }
                                        }
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

    let final_res = to_python_file_matches(std::mem::take(
        &mut *results
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner()),
    ));
    let final_skip = std::mem::take(
        &mut *skipped
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner()),
    );
    if let Some(error) = merge_completion_errors(callback_state.error_message(), thread_error) {
        return Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(error));
    }
    Ok((final_res, final_skip))
}

// Kept in sync with Constants.MAX_SEARCH_QUERY_LENGTH by contract tests.
pub(crate) const MAX_SEARCH_QUERY_LENGTH: usize = 1000;

pub(crate) fn validate_query_length(query: &str) -> PyResult<()> {
    if query.chars().take(MAX_SEARCH_QUERY_LENGTH + 1).count() > MAX_SEARCH_QUERY_LENGTH {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "Search text is limited to {MAX_SEARCH_QUERY_LENGTH} characters (received {}).",
            query.chars().count()
        )));
    }
    Ok(())
}

/// 미리보기용: 검색과 같은 비교 기준으로 `text`의 `start_char`번째 글자 이후 첫 일치 위치(글자 번호)를 찾습니다.
#[pyfunction]
#[pyo3(signature = (text, pattern, start_char=0))]
pub(crate) fn find_key_match_char(
    py: Python<'_>,
    text: String,
    pattern: String,
    start_char: usize,
) -> Option<usize> {
    py.allow_threads(|| textkey::find_key_match_char(&text, &pattern, start_char))
}

/// 강조용: 검색과 같은 비교 기준으로 일치 구간을 (시작 글자 번호, 글자 수) 목록으로 돌려줍니다.
#[pyfunction]
#[pyo3(signature = (text, pattern, limit=1000))]
pub(crate) fn find_key_match_spans(
    py: Python<'_>,
    text: String,
    pattern: String,
    limit: usize,
) -> Vec<(usize, usize)> {
    py.allow_threads(|| textkey::find_key_match_spans(&text, &pattern, limit))
}

/// 미리보기용: 엔진의 일반 텍스트 인코딩 판정(파일 전체 기준)을 Python 코덱 이름으로 돌려줍니다.
/// `encoding`에 사용자가 지정한 인코딩(설정값)이 있으면 그 값을 씁니다.
#[pyfunction]
#[pyo3(signature = (path, encoding=None))]
pub(crate) fn detect_file_encoding(
    py: Python<'_>,
    path: String,
    encoding: Option<String>,
) -> PyResult<String> {
    let file = std::fs::File::open(&path)?;
    let length = file.metadata()?.len();
    // 파일을 메모리에 복사하지 않고 매핑해 판정합니다(검색과 같은 방식). BOM도 같은 매핑에서 봅니다.
    let mapped = if length == 0 {
        None
    } else {
        Some(unsafe { Mmap::map(&file) }?)
    };
    let data: &[u8] = mapped.as_deref().unwrap_or(&[]);
    let detected = if let Some(selected) = crate::utils::requested_encoding(encoding.as_deref()) {
        selected
    } else if data.is_empty() {
        UTF_8
    } else {
        py.allow_threads(|| detect_encoding(data))
    };
    let has_bom = |bom: &[u8]| data.starts_with(bom);
    Ok(if detected == UTF_8 {
        if has_bom(b"\xef\xbb\xbf") {
            "utf-8-sig"
        } else {
            "utf-8"
        }
    } else if detected == encoding_rs::EUC_KR {
        "cp949"
    } else if detected == UTF_16LE {
        if has_bom(b"\xff\xfe") {
            "utf-16"
        } else {
            "utf-16-le"
        }
    } else if detected == UTF_16BE {
        if has_bom(b"\xfe\xff") {
            "utf-16"
        } else {
            "utf-16-be"
        }
    } else {
        "utf-8"
    }
    .to_string())
}

/// 정확히 일치에서는 검색어 앞뒤 공백을 검색기를 만들기 전에 지웁니다(검수 F-1).
///
/// 값 쪽도 앞뒤 공백을 지우고 비교하므로, 줄을 고르는 앞 단계까지 같은 검색어를 써야
/// `␠a`가 `a`와 같은 결과를 냅니다. 공백뿐인 검색어는 그대로 두어 지금처럼 아무것과도
/// 일치하지 않게 합니다(`Matcher::equals_trimmed`).
pub(crate) fn exact_search_pattern(pattern: &str, is_exact: bool) -> &str {
    let trimmed = pattern.trim();
    if is_exact && !trimmed.is_empty() {
        trimmed
    } else {
        pattern
    }
}

/// 검색어 하나로 공통 비교기와, 원본 위치 찾기·바이너리 개수용 자동자를 만듭니다.
///
/// 비교(일치 여부)는 항상 비교기(`key()` 기준)가 정합니다. 자동자는 원본 바이트에서 위치를 찾거나
/// 바이너리 파일의 일치 개수를 셀 때만 쓰며, `key(검색어)`와 정규화한 검색어의 표기
/// (대소문자·XML 엔티티·JSON 이스케이프 변형)를 모두 담습니다.
pub(crate) fn build_search_matchers(
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
    // 두 묶음을 합친 뒤에도 긴 변형이 먼저 오도록 순서를 고정합니다(검수 D-3).
    patterns.sort_by(|a, b| b.len().cmp(&a.len()).then_with(|| a.cmp(b)));
    let ac = AhoCorasickBuilder::new()
        .ascii_case_insensitive(true)
        .match_kind(MatchKind::LeftmostFirst)
        .build(&patterns)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("{}", e)))?;
    Ok((matcher, ac))
}
