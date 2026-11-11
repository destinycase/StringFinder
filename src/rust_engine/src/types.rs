use pyo3::exceptions::PyIndexError;
use pyo3::prelude::*;

// [상급 감사 이슈 해결] 문자열 기반 모드 인식을 비트플래그 상수로 변환
pub const MODE_NORMAL: u32 = 0;
pub const MODE_JSON: u32 = 1 << 0;
pub const MODE_XML: u32 = 1 << 1;
// UE 현지화 archive 값만 검색입니다. 세션에 저장되지 않는 실행 시 비트입니다.
pub const MODE_ARCHIVE: u32 = 1 << 2;
pub const MODE_EXACT: u32 = 1 << 3;
pub const MODE_EXCEL: u32 = 1 << 4;
pub const MODE_EXCLUDE_BINARY: u32 = 1 << 5;
pub const MODE_ALLOW_DUPLICATE_JSON_KEYS: u32 = 1 << 7;

pub type RawMatch = (usize, String, Option<usize>, Option<usize>);

/// Shared configuration for the Rust search entry points.
///
/// The legacy functions still accept their original arguments.  This object
/// provides a stable, named-options surface for new callers without forcing a
/// breaking change on existing Python code.
#[pyclass(module = "sf_engine")]
#[derive(Default)]
pub struct SearchOptions {
    #[pyo3(get)]
    pub include_junctions: bool,
    #[pyo3(get)]
    pub encoding: Option<String>,
    #[pyo3(get)]
    pub mode_bits: Option<u32>,
    #[pyo3(get)]
    pub extensions: Option<Vec<String>>,
    #[pyo3(get)]
    pub filename_filter: Option<Vec<String>>,
    #[pyo3(get)]
    pub exclude_hidden: bool,
    #[pyo3(get)]
    pub stop_event: Option<PyObject>,
    #[pyo3(get)]
    pub progress_callback: Option<PyObject>,
    #[pyo3(get)]
    pub results_callback: Option<PyObject>,
    #[pyo3(get)]
    pub batch_size: Option<usize>,
    #[pyo3(get)]
    pub flush_ms: Option<u64>,
    #[pyo3(get)]
    pub max_per_file: Option<usize>,

    #[pyo3(get)]
    pub max_json_depth: Option<usize>,
    #[pyo3(get)]
    pub max_json_size: Option<u64>,
    #[pyo3(get)]
    pub structured_memory_budget: Option<u64>,
}

#[pymethods]
impl SearchOptions {
    #[new]
    #[pyo3(signature = (
        mode_bits=None,
        extensions=None,
        filename_filter=None,
        exclude_hidden=false,
        stop_event=None,
        progress_callback=None,
        results_callback=None,
        batch_size=None,
        flush_ms=None,
        max_per_file=None,

        max_json_depth=None,
        max_json_size=None,
        structured_memory_budget=None,
        include_junctions=false,
        encoding=None,
    ))]
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        mode_bits: Option<u32>,
        extensions: Option<Vec<String>>,
        filename_filter: Option<Vec<String>>,
        exclude_hidden: bool,
        stop_event: Option<PyObject>,
        progress_callback: Option<PyObject>,
        results_callback: Option<PyObject>,
        batch_size: Option<usize>,
        flush_ms: Option<u64>,
        max_per_file: Option<usize>,

        max_json_depth: Option<usize>,
        max_json_size: Option<u64>,
        structured_memory_budget: Option<u64>,
        include_junctions: bool,
        encoding: Option<String>,
    ) -> Self {
        Self {
            include_junctions,
            encoding,
            mode_bits,
            extensions,
            filename_filter,
            exclude_hidden,
            stop_event,
            progress_callback,
            results_callback,
            batch_size,
            flush_ms,
            max_per_file,

            max_json_depth,
            max_json_size,
            structured_memory_budget,
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "SearchOptions(mode_bits={:?}, exclude_hidden={}, max_per_file={:?}, max_json_depth={:?}, max_json_size={:?}, structured_memory_budget={:?})",
            self.mode_bits, self.exclude_hidden, self.max_per_file, self.max_json_depth, self.max_json_size, self.structured_memory_budget
        )
    }
}

#[pyclass]
#[derive(Clone, Debug)]
pub struct SearchMatch {
    #[pyo3(get)]
    pub line: usize,
    #[pyo3(get)]
    pub content: String,
    #[pyo3(get)]
    pub offset: Option<usize>,
    #[pyo3(get)]
    pub length: Option<usize>,
    // 대부분 "match" 같은 고정 값이라 결과마다 문자열을 할당하지 않습니다.
    #[pyo3(get)]
    pub kind: std::borrow::Cow<'static, str>,
    #[pyo3(get)]
    pub code: Option<String>,
    #[pyo3(get)]
    pub detail: Option<String>,
}

#[pymethods]
impl SearchMatch {
    #[new]
    #[pyo3(signature = (line, content, offset=None, length=None, kind="match".to_string(), code=None, detail=None))]
    pub fn new(
        line: usize,
        content: String,
        offset: Option<usize>,
        length: Option<usize>,
        kind: String,
        code: Option<String>,
        detail: Option<String>,
    ) -> Self {
        SearchMatch {
            line,
            content,
            offset,
            length,
            kind: std::borrow::Cow::Owned(kind),
            code,
            detail,
        }
    }

    fn __len__(&self) -> usize {
        4
    }

    fn __getitem__(&self, index: isize, py: Python<'_>) -> PyResult<PyObject> {
        let index = if index < 0 { index + 4 } else { index };
        match index {
            0 => Ok(self.line.into_pyobject(py)?.unbind().into()),
            1 => Ok(self.content.clone().into_pyobject(py)?.unbind().into()),
            2 => Ok(self.offset.into_pyobject(py)?.unbind()),
            3 => Ok(self.length.into_pyobject(py)?.unbind()),
            _ => Err(PyIndexError::new_err("SearchMatch index out of range")),
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "SearchMatch(line={}, content='{}', offset={:?}, length={:?}, kind='{}')",
            self.line, self.content, self.offset, self.length, self.kind
        )
    }
}

impl From<RawMatch> for SearchMatch {
    fn from((line, content, offset, length): RawMatch) -> Self {
        // 대부분은 일반 일치 줄입니다. 표시 접두어(`__SF_`, `ERR_`)로 시작할 때만 아래 분류를 봅니다.
        let (kind, code, detail) = if !content.starts_with("__SF_") && !content.starts_with("ERR_")
        {
            ("match", None, None)
        } else if content == "__SF_TRUNCATED__" {
            ("truncated", Some("TRUNCATED"), None)
        } else if let Some(detail) = content.strip_prefix("__SF_JSON_DEPTH_LIMIT__|") {
            ("partial", Some("JSON_DEPTH_LIMIT"), Some(detail))
        } else if let Some(detail) = content.strip_prefix("__SF_BINARY_MATCH__|") {
            ("binary", Some("BINARY"), Some(detail))
        } else if let Some(detail) = content.strip_prefix("__SF_LONG_LINE__|") {
            ("long_line", Some("LONG_LINE"), Some(detail))
        } else if let Some(detail) = content.strip_prefix("__SF_DECODE_PARTIAL__|") {
            ("partial", Some("DECODE_PARTIAL"), Some(detail))
        } else if let Some(detail) = content.strip_prefix("__SF_EXCEL_SHEET_ERR__|") {
            ("sheet_error", Some("EXCEL_SHEET_ERROR"), Some(detail))
        } else if let Some(detail) = content.strip_prefix("__SF_EXCEL_PANIC__|") {
            ("error", Some("ERR_EXCEL_PANIC"), Some(detail))
        } else if let Some((code, detail)) = content
            .split_once('|')
            .filter(|(code, _)| code.starts_with("ERR_"))
        {
            ("error", Some(code), Some(detail))
        } else {
            ("match", None, None)
        };
        let kind = std::borrow::Cow::Borrowed(kind);
        let code = code.map(str::to_string);
        let detail = detail.map(str::to_string);
        Self {
            line,
            content,
            offset,
            length,
            kind,
            code,
            detail,
        }
    }
}
