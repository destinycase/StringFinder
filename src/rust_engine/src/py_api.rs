//! Python에서 부르는 함수(search_file, search_dir, 강조 위치, 인코딩 판별)와 넘길 모양 바꾸기.
//! 8.1.5(엔진 1.0.1)에서 lib.rs에서 옮겼습니다. 8.1.6(엔진 1.0.2)에서 search_dir를 단계별 함수로 나눴습니다.

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

    max_json_depth: Option<usize>,
    max_json_size: Option<u64>,
    options: Option<Py<SearchOptions>>,
    _kwargs: Option<pyo3::PyObject>,
) -> Result<(FileMatches, SkippedEntries), PyErr> {
    validate_query_length(&pattern)?;
    // 1. 설정 정리 → 2. 준비 → 3. 스레드 띄우기 → 4. 폴더 돌기 → 5. 마무리
    let mut config = DirSearchConfig {
        extensions,
        mode_bits,
        filename_filter,
        exclude_hidden,
        stop_event,
        progress_callback,
        results_callback,
        batch_size,
        flush_ms: _flush_ms,
        max_per_file,
        max_json_depth,
        max_json_size,
        structured_memory_budget: None,
        encoding: None,
        include_junctions: false,
        max_total_matches: None,
    };
    if let Some(options) = options.as_ref() {
        config.apply_options(py, &options.bind(py).borrow());
    }
    let plan = Arc::new(DirSearchPlan::build(&pattern, &config)?);
    let shared = DirSearchShared::new(config.structured_memory_budget);

    let (monitor_done_tx, monitor_done_rx) = std::sync::mpsc::channel::<()>();
    let monitor_handle = spawn_dir_monitor(
        py,
        &shared,
        config.stop_event.as_ref(),
        config.progress_callback.as_ref(),
        monitor_done_rx,
    );
    let results_dispatcher = config.results_callback.as_ref().map(|callback| {
        spawn_results_dispatcher(py, callback, config.batch_size, config.flush_ms, &shared)
    });
    let tx_main = results_dispatcher.as_ref().map(|(tx, _)| tx.clone());

    let thread_error = py.allow_threads(|| {
        for root_path in root_paths {
            if shared.stop_flag.load(Ordering::Relaxed) {
                break;
            }
            search_walk_builder(
                Path::new(&root_path),
                plan.exclude_hidden,
                plan.include_junctions,
            )
            .build_parallel()
            .run(|| {
                // 걸어 다니는 스레드마다 한 번 복사합니다(파일마다 만들지 않음).
                let plan = plan.clone();
                let shared = shared.clone();
                let tx_worker = tx_main.clone();
                Box::new(move |entry| visit_dir_entry(&plan, &shared, tx_worker.as_ref(), entry))
            });
        }
        finish_dir_search(
            &shared,
            monitor_done_tx,
            tx_main,
            results_dispatcher,
            monitor_handle,
        )
    });

    let final_res = to_python_file_matches(std::mem::take(
        &mut *shared
            .results
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner()),
    ));
    let final_skip = std::mem::take(
        &mut *shared
            .skipped
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner()),
    );
    if let Some(error) =
        merge_completion_errors(shared.callback_state.error_message(), thread_error)
    {
        return Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(error));
    }
    Ok((final_res, final_skip))
}

/// search_dir의 최종 설정입니다. 예전 방식 인자로 채운 뒤 설정 묶음(`SearchOptions`)이 있으면 그 값으로 덮습니다.
pub(crate) struct DirSearchConfig {
    pub(crate) extensions: Option<Vec<String>>,
    pub(crate) mode_bits: Option<u32>,
    pub(crate) filename_filter: Option<Vec<String>>,
    pub(crate) exclude_hidden: bool,
    pub(crate) stop_event: Option<pyo3::PyObject>,
    pub(crate) progress_callback: Option<pyo3::PyObject>,
    pub(crate) results_callback: Option<pyo3::PyObject>,
    pub(crate) batch_size: Option<usize>,
    pub(crate) flush_ms: Option<u64>,
    pub(crate) max_per_file: Option<usize>,
    pub(crate) max_json_depth: Option<usize>,
    pub(crate) max_json_size: Option<u64>,
    pub(crate) structured_memory_budget: Option<u64>,
    pub(crate) encoding: Option<&'static Encoding>,
    pub(crate) include_junctions: bool,
    pub(crate) max_total_matches: Option<u64>,
}

impl DirSearchConfig {
    /// 설정 묶음의 값을 적용합니다. 값이 있는(`Some`) 항목만 인자를 덮고,
    /// 인코딩·정션·전체 상한·숨김 제외·메모리 예산은 설정 묶음 값을 그대로 씁니다.
    pub(crate) fn apply_options(&mut self, py: Python, config: &SearchOptions) {
        self.encoding = crate::utils::requested_encoding(config.encoding.as_deref());
        self.include_junctions = config.include_junctions;
        self.max_total_matches = config.max_total_matches;
        if config.extensions.is_some() {
            self.extensions = config.extensions.clone();
        }
        if config.mode_bits.is_some() {
            self.mode_bits = config.mode_bits;
        }
        if config.filename_filter.is_some() {
            self.filename_filter = config.filename_filter.clone();
        }
        self.exclude_hidden = config.exclude_hidden;
        if config.stop_event.is_some() {
            self.stop_event = config.stop_event.as_ref().map(|event| event.clone_ref(py));
        }
        if config.progress_callback.is_some() {
            self.progress_callback = config
                .progress_callback
                .as_ref()
                .map(|callback| callback.clone_ref(py));
        }
        if config.results_callback.is_some() {
            self.results_callback = config
                .results_callback
                .as_ref()
                .map(|callback| callback.clone_ref(py));
        }
        if config.batch_size.is_some() {
            self.batch_size = config.batch_size;
        }
        if config.flush_ms.is_some() {
            self.flush_ms = config.flush_ms;
        }
        if config.max_per_file.is_some() {
            self.max_per_file = config.max_per_file;
        }

        if config.max_json_depth.is_some() {
            self.max_json_depth = config.max_json_depth;
        }
        if config.max_json_size.is_some() {
            self.max_json_size = config.max_json_size;
        }
        self.structured_memory_budget = config.structured_memory_budget;
    }
}

/// 폴더 검색 동안 바뀌지 않는 값: 검색 방식, 비교기, 확장자·파일명 조건, 크기·개수 상한.
/// 걸어 다니는 스레드들이 `Arc`로 함께 읽습니다.
pub(crate) struct DirSearchPlan {
    pub(crate) is_json: bool,
    pub(crate) is_xml: bool,
    pub(crate) is_exact: bool,
    pub(crate) is_excel: bool,
    pub(crate) is_archive: bool,
    pub(crate) exclude_binary: bool,
    pub(crate) allow_duplicate_json_keys: bool,
    pub(crate) exclude_hidden: bool,
    pub(crate) include_junctions: bool,
    pub(crate) encoding: Option<&'static Encoding>,
    pub(crate) common_file_size_limit: u64,
    pub(crate) max_per_file: usize,
    pub(crate) max_json_depth: usize,
    pub(crate) max_json_size: u64,
    pub(crate) max_total_matches: Option<u64>,
    pub(crate) matcher: textkey::Matcher,
    pub(crate) ac: aho_corasick::AhoCorasick,
    pub(crate) extensions: Option<HashSet<String>>,
    pub(crate) filename_glob: Option<globset::GlobSet>,
}

impl DirSearchPlan {
    pub(crate) fn build(pattern: &str, config: &DirSearchConfig) -> PyResult<Self> {
        let (is_json, is_xml, is_exact, is_excel, exclude_binary) =
            parse_search_mode(config.mode_bits);
        let is_archive = config.mode_bits.unwrap_or(0) & types::MODE_ARCHIVE != 0;
        let (matcher, ac) = build_search_matchers(
            exact_search_pattern(pattern, is_exact),
            is_xml,
            is_json || is_archive,
        )?;
        Ok(Self {
            is_json,
            is_xml,
            is_exact,
            is_excel,
            is_archive,
            exclude_binary,
            allow_duplicate_json_keys: config.mode_bits.unwrap_or(0)
                & types::MODE_ALLOW_DUPLICATE_JSON_KEYS
                != 0,
            exclude_hidden: config.exclude_hidden,
            include_junctions: config.include_junctions,
            encoding: config.encoding,
            common_file_size_limit: config
                .max_json_size
                .unwrap_or(DEFAULT_MAX_SEARCH_FILE_SIZE_BYTES),
            max_per_file: config.max_per_file.unwrap_or(10_000),
            max_json_depth: config.max_json_depth.unwrap_or(20_000),
            max_json_size: config
                .max_json_size
                .unwrap_or(DEFAULT_MAX_SEARCH_FILE_SIZE_BYTES),
            max_total_matches: config.max_total_matches,
            matcher,
            ac,
            extensions: normalized_extension_set(config.extensions.clone()),
            filename_glob: config
                .filename_filter
                .as_ref()
                .and_then(|f| build_glob_set(f)),
        })
    }
}

/// 확장자 조건을 비교용으로 바꿉니다: 소문자, 앞의 점 제거.
pub(crate) fn normalized_extension_set(extensions: Option<Vec<String>>) -> Option<HashSet<String>> {
    extensions.map(|exts| {
        exts.into_iter()
            .map(|s| s.to_lowercase().trim_start_matches('.').to_string())
            .collect::<HashSet<_>>()
    })
}

/// 폴더 검색의 스레드들이 함께 쓰는 값입니다. 복사하면 같은 값을 가리키는 손잡이가 하나 더 생깁니다.
#[derive(Clone)]
pub(crate) struct DirSearchShared {
    /// 중지 요청(사용자 중지, 콜백 오류, 전체 상한 도달).
    pub(crate) stop_flag: Arc<AtomicBool>,
    /// 폴더 돌기와 모든 작업이 끝났음. 결과 전달·감시 스레드가 이것을 보고 끝납니다.
    pub(crate) done_flag: Arc<AtomicBool>,
    pub(crate) callback_state: Arc<CallbackState>,
    /// 실제로 처리한 파일 수(진행 표시용).
    pub(crate) processed_files: Arc<AtomicU64>,
    pub(crate) visited_paths: Arc<Mutex<HashSet<std::path::PathBuf>>>,
    pub(crate) results: Arc<Mutex<RawFileMatches>>,
    pub(crate) skipped: Arc<Mutex<SkippedEntries>>,
    /// 전체 상한을 넘을 만큼 보냈으면 더 찾지 않습니다(검수 S-6).
    pub(crate) sent_matches: Arc<AtomicU64>,
    pub(crate) structured_limiter: Option<Arc<StructuredMemoryLimiter>>,
}

impl DirSearchShared {
    pub(crate) fn new(structured_memory_budget: Option<u64>) -> Self {
        Self {
            stop_flag: Arc::new(AtomicBool::new(false)),
            done_flag: Arc::new(AtomicBool::new(false)),
            callback_state: Arc::new(CallbackState::new()),
            processed_files: Arc::new(AtomicU64::new(0)),
            visited_paths: Arc::new(Mutex::new(HashSet::new())),
            results: Arc::new(Mutex::new(Vec::new())),
            skipped: Arc::new(Mutex::new(Vec::new())),
            sent_matches: Arc::new(AtomicU64::new(0)),
            structured_limiter: structured_memory_budget
                .filter(|budget| *budget > 0)
                .map(|budget| Arc::new(StructuredMemoryLimiter::new(budget))),
        }
    }

    fn push_skipped(&self, path: &Path, reason: String) {
        let mut skipped = self
            .skipped
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        skipped.push((path.to_string_lossy().to_string(), reason));
    }
}

/// 감시 스레드: 0.1초마다 Python의 중지 요청을 확인하고 처리한 파일 수를 알립니다.
/// 중지 요청도 진행 콜백도 없으면 띄우지 않습니다.
pub(crate) fn spawn_dir_monitor(
    py: Python,
    shared: &DirSearchShared,
    stop_event: Option<&pyo3::PyObject>,
    progress_callback: Option<&pyo3::PyObject>,
    monitor_done_rx: std::sync::mpsc::Receiver<()>,
) -> Option<std::thread::JoinHandle<()>> {
    if stop_event.is_none() && progress_callback.is_none() {
        return None;
    }
    let flag_clone = shared.stop_flag.clone();
    let done_clone = shared.done_flag.clone();
    let stop_evt_mon = stop_event.map(|obj| obj.clone_ref(py));
    let progress_cb_mon = progress_callback.map(|obj| obj.clone_ref(py));
    let processed_files_mon = shared.processed_files.clone();
    let callback_state_mon = shared.callback_state.clone();

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
}

pub(crate) type ResultSender = crossbeam_channel::Sender<(String, Vec<RawMatch>)>;

/// 결과 전달 스레드: 찾은 결과를 묶음으로 모아 Python 콜백에 넘깁니다.
/// 통로(보내는 쪽)와 스레드 손잡이를 돌려줍니다. 통로를 쥔 값이 모두 내려놓이고 `done_flag`가 서면 끝납니다.
pub(crate) fn spawn_results_dispatcher(
    py: Python,
    callback: &pyo3::PyObject,
    batch_size: Option<usize>,
    flush_ms: Option<u64>,
    shared: &DirSearchShared,
) -> (ResultSender, std::thread::JoinHandle<()>) {
    let batch_size_limit = batch_size.unwrap_or(100).max(1);
    let queue_capacity = batch_size_limit.saturating_mul(4).max(1);
    let (tx, rx) = crossbeam_channel::bounded::<(String, Vec<RawMatch>)>(queue_capacity);
    let cb_clone = callback.clone_ref(py);
    let done_dispatcher = shared.done_flag.clone();
    let callback_state_dispatcher = shared.callback_state.clone();
    let stop_flag_dispatcher = shared.stop_flag.clone();
    let flush_interval_ms = flush_ms.unwrap_or(20).clamp(1, 1_000);

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
}

/// 폴더 검색 마무리. 순서가 중요합니다(바꾸면 검색이 끝나지 않거나 마지막 결과 묶음이 빠집니다).
/// 1) 종료 표시를 세워 두 스레드가 끝날 수 있게 하고
/// 2) 감시 스레드를 깨우고
/// 3) 결과 통로의 보내는 쪽을 모두 내려놓은 뒤
/// 4) 두 스레드를 기다립니다.
///
/// 통로와 손잡이를 값으로 받아, 이 함수가 끝나면 남는 손잡이가 없습니다.
pub(crate) fn finish_dir_search(
    shared: &DirSearchShared,
    monitor_done_tx: std::sync::mpsc::Sender<()>,
    tx_main: Option<ResultSender>,
    results_dispatcher: Option<(ResultSender, std::thread::JoinHandle<()>)>,
    monitor_handle: Option<std::thread::JoinHandle<()>>,
) -> Option<String> {
    // A3: done_flag를 먼저 설정하여 dispatcher와 monitor 스레드가 종료 루프에 진입할 수 있도록 합니다.
    shared.done_flag.store(true, Ordering::SeqCst);
    let _ = monitor_done_tx.send(());
    drop(tx_main);
    let dispatcher_handle = results_dispatcher.map(|(tx, handle)| {
        drop(tx);
        handle
    });
    join_auxiliary_threads(dispatcher_handle, monitor_handle)
}

/// 이 파일을 검색할지(크기 확인 전): 휴지통 안이 아니고, 확장자 조건과 파일명 조건에 맞아야 합니다.
/// 확장자는 대소문자를 가리지 않습니다(조건은 `normalized_extension_set`으로 소문자).
pub(crate) fn wants_file(
    path: &Path,
    extensions: Option<&HashSet<String>>,
    filename_glob: &Option<globset::GlobSet>,
) -> bool {
    if should_exclude_recycle_path(path) {
        return false;
    }
    if let Some(s) = extensions {
        let ext_str = path
            .extension()
            .and_then(|s| s.to_str())
            .unwrap_or("")
            .to_lowercase();
        if !s.contains(&ext_str) {
            return false;
        }
    }
    if filename_glob.is_some() {
        let filename = path.file_name().and_then(|s| s.to_str()).unwrap_or("");
        if !match_filename_glob(filename, filename_glob) {
            return false;
        }
    }
    true
}

/// 크기 상한을 넘으면 건너뛴 사유를 돌려줍니다. 크기를 모르면(`None`) 넘지 않은 것으로 봅니다.
pub(crate) fn too_large_reason(file_len: Option<u64>, size_limit: u64) -> Option<String> {
    match file_len {
        Some(len) if len > size_limit => Some(encode_skip_reason(
            REASON_ERR_TOO_LARGE,
            format!("{} bytes", len),
        )),
        _ => None,
    }
}

/// Excel 확장자인지(검색 방식과 상관없이 크기를 새로 확인할 파일).
pub(crate) fn has_excel_extension(path: &Path) -> bool {
    path.extension()
        .and_then(|ext| ext.to_str())
        .is_some_and(|ext| {
            ["xlsx", "xlsm", "xls", "xlsb"]
                .iter()
                .any(|known| ext.eq_ignore_ascii_case(known))
        })
}

/// 폴더 돌기에서 항목 하나를 처리합니다: 순회 오류 기록, 폴더 중복·휴지통 거르기, 파일이면 검색.
fn visit_dir_entry(
    plan: &DirSearchPlan,
    shared: &DirSearchShared,
    tx: Option<&ResultSender>,
    entry: Result<ignore::DirEntry, ignore::Error>,
) -> ignore::WalkState {
    if shared.stop_flag.load(Ordering::Relaxed) {
        return ignore::WalkState::Quit;
    }
    let entry = match entry {
        Ok(e) => e,
        Err(e) => {
            if let Ok(mut s) = shared.skipped.lock() {
                s.extend(walk_error_entries(&e));
            }
            return ignore::WalkState::Continue;
        }
    };
    let Some(file_type) = entry.file_type() else {
        return ignore::WalkState::Continue;
    };
    if (plan.include_junctions || file_type.is_dir() || file_type.is_symlink())
        && !claim_walk_entry(entry.path(), plan.include_junctions, &shared.visited_paths)
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
    if !wants_file(path, plan.extensions.as_ref(), &plan.filename_glob) {
        return ignore::WalkState::Continue;
    }

    // Directory entries already carry metadata on Windows.
    // Keep fresh lookups for resource reservations, links and
    // Excel. Recheck cached oversized files before excluding
    // them; opened-file metadata remains the final size check.
    let metadata = size_check_metadata(
        &entry,
        plan.include_junctions
            || plan.is_json
            || plan.is_archive
            || plan.is_xml
            || plan.is_excel
            || has_excel_extension(path),
        plan.common_file_size_limit,
    );
    let size_limit = file_size_limit_for(path, plan.is_archive, plan.common_file_size_limit);
    if let Some(reason) = too_large_reason(metadata.map(|m| m.len()), size_limit) {
        shared.push_skipped(path, reason);
        shared.processed_files.fetch_add(1, Ordering::Relaxed);
        return ignore::WalkState::Continue;
    }
    search_dir_file(plan, shared, tx, path)
}

/// 검색하기로 한 파일 하나: 메모리 예약 → 검색 → 결과 보내기(또는 모으기) → 처리 수 세기.
fn search_dir_file(
    plan: &DirSearchPlan,
    shared: &DirSearchShared,
    tx: Option<&ResultSender>,
    path: &Path,
) -> ignore::WalkState {
    let _memory_permit = match acquire_structured_permit(
        &shared.structured_limiter,
        path,
        plan.is_json,
        plan.is_archive,
        plan.is_xml,
        plan.is_excel,
        plan.encoding,
        &shared.stop_flag,
    ) {
        Ok(permit) => permit,
        Err(StructuredPermitError::Cancelled) => return ignore::WalkState::Quit,
        Err(StructuredPermitError::InsufficientBudget(estimate)) => {
            shared.push_skipped(
                path,
                encode_skip_reason(REASON_ERR_RESOURCE_BUDGET, estimate),
            );
            shared.processed_files.fetch_add(1, Ordering::Relaxed);
            return ignore::WalkState::Continue;
        }
    };
    let res = search_file_internal(InternalSearchParams {
        encoding: plan.encoding,
        allow_duplicate_json_keys: plan.allow_duplicate_json_keys,
        path,
        matcher: &plan.matcher,
        ac: &plan.ac,
        is_exact: plan.is_exact,
        is_json: plan.is_json,
        is_archive: plan.is_archive,
        is_xml: plan.is_xml,
        is_excel: plan.is_excel,
        exclude_hidden: plan.exclude_hidden,
        exclude_binary: plan.exclude_binary,

        stop_flag: shared.stop_flag.clone(),
        max_per_file: plan.max_per_file,
        max_json_depth: plan.max_json_depth,
        max_json_size: plan.max_json_size,
    });

    if let Some(r) = res {
        let f_path = path.to_string_lossy().to_string();
        match r {
            Ok(matches) => {
                if !matches.is_empty() {
                    if let Some(limit) = plan.max_total_matches {
                        let real = matches.iter().filter(|m| !m.4.is_metadata()).count() as u64;
                        let total = shared.sent_matches.fetch_add(real, Ordering::Relaxed) + real;
                        if total > limit {
                            shared.stop_flag.store(true, Ordering::SeqCst);
                        }
                    }
                    if let Some(tx) = tx {
                        let _ = tx.send((f_path, matches));
                    } else {
                        let mut g = shared
                            .results
                            .lock()
                            .unwrap_or_else(|poisoned| poisoned.into_inner());
                        g.push((f_path, matches));
                    }
                }
            }
            Err(e) => {
                let mut s = shared
                    .skipped
                    .lock()
                    .unwrap_or_else(|poisoned| poisoned.into_inner());
                s.push((f_path, e));
            }
        }
    }
    shared.processed_files.fetch_add(1, Ordering::Relaxed); // M4: 파일 처리 완료 카운트
    ignore::WalkState::Continue
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

#[cfg(test)]
mod dir_search_tests {
    use super::*;

    fn exts(values: &[&str]) -> Option<HashSet<String>> {
        normalized_extension_set(Some(values.iter().map(|v| v.to_string()).collect()))
    }

    #[test]
    fn extension_filter_ignores_case_and_leading_dot() {
        let set = exts(&[".TXT", "Json"]).unwrap();
        assert!(set.contains("txt") && set.contains("json") && set.len() == 2);
        let set = exts(&["txt"]);
        assert!(wants_file(Path::new("C:/a/B.TXT"), set.as_ref(), &None));
        assert!(!wants_file(Path::new("C:/a/b.json"), set.as_ref(), &None));
        assert!(!wants_file(Path::new("C:/a/noext"), set.as_ref(), &None));
        assert!(wants_file(Path::new("C:/a/noext"), None, &None));
        assert!(normalized_extension_set(None).is_none());
    }

    #[test]
    fn filename_filter_is_a_case_insensitive_substring() {
        let glob = build_glob_set(&["Quest".to_string()]);
        assert!(wants_file(Path::new("C:/a/my_QUEST_01.json"), None, &glob));
        assert!(!wants_file(Path::new("C:/a/item.json"), None, &glob));
        let both = exts(&["json"]);
        assert!(!wants_file(
            Path::new("C:/a/quest.txt"),
            both.as_ref(),
            &glob
        ));
    }

    #[test]
    fn recycle_bin_files_are_never_searched() {
        assert!(!wants_file(
            Path::new("C:/$Recycle.Bin/S-1/x.txt"),
            None,
            &None
        ));
        assert!(!wants_file(
            Path::new("D:/data/$RECYCLE.BIN/x.txt"),
            None,
            &None
        ));
    }

    #[test]
    fn too_large_reason_only_when_known_size_exceeds_limit() {
        assert_eq!(
            too_large_reason(Some(11), 10).as_deref(),
            Some("ERR_TOO_LARGE|11 bytes")
        );
        assert_eq!(too_large_reason(Some(10), 10), None);
        assert_eq!(too_large_reason(None, 10), None);
    }

    #[test]
    fn excel_extension_check_ignores_case() {
        assert!(has_excel_extension(Path::new("a.XLSX")));
        assert!(has_excel_extension(Path::new("a.xlsb")));
        assert!(!has_excel_extension(Path::new("a.csv")));
        assert!(!has_excel_extension(Path::new("xlsx")));
    }

    #[test]
    fn shared_state_starts_empty_and_budget_zero_means_no_limiter() {
        let shared = DirSearchShared::new(Some(0));
        assert!(shared.structured_limiter.is_none());
        assert!(DirSearchShared::new(Some(1024))
            .structured_limiter
            .is_some());
        assert!(!shared.stop_flag.load(Ordering::Relaxed));
        assert!(!shared.done_flag.load(Ordering::Relaxed));
        // 복사본은 같은 값을 가리킵니다.
        let copy = shared.clone();
        copy.stop_flag.store(true, Ordering::SeqCst);
        assert!(shared.stop_flag.load(Ordering::Relaxed));
    }

    /// 마무리는 통로를 모두 내려놓고 두 스레드를 끝까지 기다립니다. 남는 손잡이가 있으면 여기서 멈춥니다.
    #[test]
    fn finish_releases_every_sender_and_joins_both_threads_repeatedly() {
        for round in 0..300 {
            let shared = DirSearchShared::new(None);
            let (tx, rx) = crossbeam_channel::bounded::<(String, Vec<RawMatch>)>(4);
            let done = shared.done_flag.clone();
            let received = Arc::new(AtomicU64::new(0));
            let received_in = received.clone();
            // 결과 전달 스레드처럼: 통로가 닫힐 때까지 받고, 끝나면 종료 표시가 서 있어야 합니다.
            let dispatcher = std::thread::spawn(move || {
                while rx.recv().is_ok() {
                    received_in.fetch_add(1, Ordering::SeqCst);
                }
                assert!(done.load(Ordering::SeqCst));
            });
            let (monitor_tx, monitor_rx) = std::sync::mpsc::channel::<()>();
            let monitor = std::thread::spawn(move || {
                let _ = monitor_rx.recv_timeout(std::time::Duration::from_secs(30));
            });
            let tx_main = Some(tx.clone());
            if round % 2 == 0 {
                tx.send((format!("f{round}"), Vec::new())).unwrap();
            }
            let started = std::time::Instant::now();
            let error = finish_dir_search(
                &shared,
                monitor_tx,
                tx_main,
                Some((tx, dispatcher)),
                Some(monitor),
            );
            assert!(error.is_none());
            assert!(
                started.elapsed() < std::time::Duration::from_secs(5),
                "round {round}"
            );
            assert_eq!(received.load(Ordering::SeqCst), u64::from(round % 2 == 0));
        }
    }
}
