use pyo3::prelude::*;
use serde::Deserialize;

// [상급 감사 이슈 해결] 문자열 기반 모드 인식을 비트플래그 상수로 변환
pub const MODE_NORMAL: u32 = 0;
pub const MODE_JSON: u32 = 1 << 0;
pub const MODE_XML: u32 = 1 << 1;
pub const MODE_ARCHIVE: u32 = 1 << 2;
pub const MODE_EXACT: u32 = 1 << 3;
pub const MODE_EXCEL: u32 = 1 << 4;
pub const MODE_EXCLUDE_BINARY: u32 = 1 << 5;
pub const MODE_EXISTENCE_ONLY: u32 = 1 << 6;



#[derive(Deserialize)]
#[allow(dead_code)]
pub struct ArchiveSource {
    #[serde(rename = "Text")]
    pub text: String,
}

#[derive(Deserialize)]
#[allow(dead_code)]
pub struct ArchiveTranslation {
    #[serde(rename = "Text")]
    pub text: String,
}

#[derive(Deserialize)]
#[allow(dead_code)]
pub struct ArchiveChild {
    #[serde(rename = "Key")]
    pub key: String,
    #[serde(rename = "Source")]
    pub source: ArchiveSource,
    #[serde(rename = "Translation")]
    pub translation: ArchiveTranslation,
}

#[derive(Deserialize)]
#[allow(dead_code)]
pub struct ArchiveSubnamespace {
    #[serde(rename = "Namespace")]
    pub namespace: String,
    #[serde(rename = "Children")]
    pub children: Vec<ArchiveChild>,
}

#[derive(Deserialize)]
#[allow(dead_code)]
pub struct ArchiveData {
    #[serde(rename = "Subnamespaces")]
    pub subnamespaces: Vec<ArchiveSubnamespace>,
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
}

#[pymethods]
impl SearchMatch {
    #[new]
    #[pyo3(signature = (line, content, offset=None, length=None))]
    pub fn new(line: usize, content: String, offset: Option<usize>, length: Option<usize>) -> Self {
        SearchMatch {
            line,
            content,
            offset,
            length,
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "SearchMatch(line={}, content='{}', offset={:?}, length={:?})",
            self.line, self.content, self.offset, self.length
        )
    }
}
