use pyo3::prelude::*;
use aho_corasick::{AhoCorasickBuilder, MatchKind};
use memmap2::Mmap;
use std::fs::File;
use std::path::Path;
use std::collections::HashSet;
use encoding_rs::{Encoding, UTF_8, EUC_KR, UTF_16LE, UTF_16BE};
use pyo3::types::PyModule;
use pyo3::Bound;
use calamine::{Reader, Xlsx, Xlsb, Xls, Data};
use rayon::prelude::*;
use ignore::WalkBuilder;
use simdutf8::basic::from_utf8 as simd_from_utf8;
use serde::Deserialize;
use serde_json::Value as JsonValue;
use quick_xml::reader::Reader as XmlReader;
use quick_xml::events::Event;
use std::panic;

#[derive(Deserialize)]
struct ArchiveSource {
    #[serde(rename = "Text")]
    text: String,
}

#[derive(Deserialize)]
struct ArchiveTranslation {
    #[serde(rename = "Text")]
    text: String,
}

#[derive(Deserialize)]
struct ArchiveChild {
    #[serde(rename = "Key")]
    key: String,
    #[serde(rename = "Source")]
    source: ArchiveSource,
    #[serde(rename = "Translation")]
    translation: ArchiveTranslation,
}

#[derive(Deserialize)]
struct ArchiveSubnamespace {
    #[serde(rename = "Namespace")]
    namespace: String,
    #[serde(rename = "Children")]
    children: Vec<ArchiveChild>,
}

#[derive(Deserialize)]
struct ArchiveData {
    #[serde(rename = "Subnamespaces")]
    subnamespaces: Vec<ArchiveSubnamespace>,
}

/// Zero-copy를 지원하는 향상된 검색 결과 구조체 (Python 노출용)
#[pyclass]
#[derive(Clone)]
struct SearchMatch {
    #[pyo3(get)]
    line: usize,
    #[pyo3(get)]
    content: String,
    #[pyo3(get)]
    offset: Option<usize>,
    #[pyo3(get)]
    length: Option<usize>,
}

#[pymethods]
impl SearchMatch {
    #[new]
    fn new(line: usize, content: String, offset: Option<usize>, length: Option<usize>) -> Self {
        SearchMatch { line, content, offset, length }
    }

    fn __repr__(&self) -> String {
        format!("SearchMatch(line={}, content='{}', offset={:?}, length={:?})", self.line, self.content, self.offset, self.length)
    }
}

/// 바이트 데이터의 인코딩을 감지합니다.
fn detect_encoding(data: &[u8]) -> &'static Encoding {
    if data.len() >= 2 {
        if data.starts_with(b"\xff\xfe") { return UTF_16LE; }
        if data.starts_with(b"\xfe\xff") { return UTF_16BE; }
    }
    if data.starts_with(b"\xef\xbb\xbf") { return UTF_8; }
    if simd_from_utf8(data).is_ok() { return UTF_8; }
    EUC_KR
}

/// 감지된 인코딩에 따라 바이트 데이터를 문자열로 디코딩합니다.
fn decode_bytes(bytes: &[u8], encoding: &'static Encoding) -> String {
    let (res, _, _) = encoding.decode(bytes);
    res.into_owned()
}

/// 엑셀 파일 검색 함수 (확장자별 개별 처리로 컴파일 오류 방지)
fn search_excel_file(path: &Path, pattern: &str, ac: &aho_corasick::AhoCorasick, is_exact: bool) -> Vec<SearchMatch> {
    let mut results = Vec::new();
    let ext = path.extension().and_then(|s| s.to_str()).unwrap_or("").to_lowercase();
    
    match ext.as_str() {
        "xlsx" | "xlsm" => {
            if let Ok(mut wb) = calamine::open_workbook::<Xlsx<_>, _>(path) {
                for sheet_name in wb.sheet_names().to_vec() {
                    if let Ok(range) = wb.worksheet_range(&sheet_name) {
                        for (row_idx, row) in range.rows().enumerate() {
                            for (col_idx, cell) in row.iter().enumerate() {
                                process_cell(&sheet_name, row_idx, col_idx, cell, pattern, ac, is_exact, &mut results);
                            }
                        }
                    }
                }
            }
        },
        "xlsb" => {
            if let Ok(mut wb) = calamine::open_workbook::<Xlsb<_>, _>(path) {
                for sheet_name in wb.sheet_names().to_vec() {
                    if let Ok(range) = wb.worksheet_range(&sheet_name) {
                        for (row_idx, row) in range.rows().enumerate() {
                            for (col_idx, cell) in row.iter().enumerate() {
                                process_cell(&sheet_name, row_idx, col_idx, cell, pattern, ac, is_exact, &mut results);
                            }
                        }
                    }
                }
            }
        },
        "xls" => {
            if let Ok(mut wb) = calamine::open_workbook::<Xls<_>, _>(path) {
                for sheet_name in wb.sheet_names().to_vec() {
                    if let Ok(range) = wb.worksheet_range(&sheet_name) {
                        for (row_idx, row) in range.rows().enumerate() {
                            for (col_idx, cell) in row.iter().enumerate() {
                                process_cell(&sheet_name, row_idx, col_idx, cell, pattern, ac, is_exact, &mut results);
                            }
                        }
                    }
                }
            }
        },
        _ => {}
    }
    results
}

fn process_cell(sheet_name: &str, row_idx: usize, col_idx: usize, cell: &Data, pattern: &str, ac: &aho_corasick::AhoCorasick, is_exact: bool, results: &mut Vec<SearchMatch>) {
    let cell_val = match cell {
        Data::String(s) => s.to_string(),
        Data::Float(f) => f.to_string(),
        Data::Int(i) => i.to_string(),
        Data::Bool(b) => b.to_string(),
        _ => "".to_string(),
    };
    if cell_val.is_empty() { return; }

    let pat_lower = pattern.to_lowercase();
    let is_match = if is_exact {
        cell_val.to_lowercase() == pat_lower
    } else {
        ac.find(&cell_val).is_some()
    };

    if is_match {
        let mut col_letter = String::new();
        let mut temp_col = col_idx as i32;
        while temp_col >= 0 {
            col_letter.insert(0, (b'A' + (temp_col % 26) as u8) as char);
            temp_col = (temp_col / 26) - 1;
        }
        let location = format!("{} | {}{} | {}", sheet_name, col_letter, row_idx + 1, cell_val);
        results.push(SearchMatch::new(0, location, None, None));
    }
}

fn get_line_number(mmap: &[u8], offset: usize) -> usize {
    mmap[..offset.min(mmap.len())].iter().filter(|&&b| b == b'\n').count() + 1
}

/// JSON 파일 상세 검색 (값 위주)
fn search_json_file(mmap: &[u8], pattern: &str, ac: &aho_corasick::AhoCorasick, is_exact: bool) -> Vec<SearchMatch> {
    let mut results = Vec::new();
    let mut last_offset = 0;
    if let Ok(v) = serde_json::from_slice::<JsonValue>(mmap) {
        recursive_search_json(&v, "", pattern, ac, is_exact, &mut results, mmap, &mut last_offset);
    }
    results
}

fn recursive_search_json(v: &JsonValue, path: &str, pattern: &str, ac: &aho_corasick::AhoCorasick, is_exact: bool, results: &mut Vec<SearchMatch>, mmap: &[u8], last_offset: &mut usize) {
    match v {
        JsonValue::Object(map) => {
            for (k, v) in map {
                let new_path = if path.is_empty() { k.clone() } else { format!("{}.{}", path, k) };
                recursive_search_json(v, &new_path, pattern, ac, is_exact, results, mmap, last_offset);
            }
        },
        JsonValue::Array(arr) => {
            for (i, v) in arr.iter().enumerate() {
                let new_path = if path.is_empty() { format!("[{}]", i) } else { format!("{}[{}]", path, i) };
                recursive_search_json(v, &new_path, pattern, ac, is_exact, results, mmap, last_offset);
            }
        },
        JsonValue::String(s) => {
            let is_match = if is_exact {
                s.to_lowercase() == pattern.to_lowercase()
            } else {
                ac.find(s).is_some()
            };

            if is_match {
                // [v4.33.8 Fix] JSON 값의 실제 오프셋을 mmap에서 직접 찾음 (이스케이프 대응)
                let mut found_pos = None;
                let mut found_len = 0;

                // [v4.33.9 Fix] last_offset 경계 확인
                if *last_offset < mmap.len() {
                    let search_bytes = s.as_bytes();
                    if let Some(pos) = mmap[*last_offset..].windows(search_bytes.len()).position(|w| w == search_bytes) {
                        found_pos = Some(*last_offset + pos);
                        found_len = search_bytes.len();
                    } else if let Some(m) = ac.find(&mmap[*last_offset..]) {
                        found_pos = Some(*last_offset + m.start());
                        found_len = m.len();
                    }
                }

                if let Some(actual_pos) = found_pos {
                    let line = get_line_number(mmap, actual_pos);
                    results.push(SearchMatch::new(line, format!("{} | {}", path, s), Some(actual_pos), Some(found_len)));
                    *last_offset = actual_pos + found_len;
                } else {
                    // 오프셋을 못 찾은 경우에만 라인 1 폴백
                    results.push(SearchMatch::new(1, format!("{} | {}", path, s), None, None));
                }
            }
        },
        JsonValue::Number(n) => {
            let s = n.to_string();
            let is_match = if is_exact { s == pattern } else { ac.find(&s).is_some() };
            if is_match {
                results.push(SearchMatch::new(1, format!("{} | {}", path, s), None, None));
            }
        },
        _ => {}
    }
}

/// XML 파일 상세 검색 (태그/속성/데이터)
fn search_xml_file(mmap: &[u8], pattern: &str, ac: &aho_corasick::AhoCorasick, is_exact: bool) -> Vec<SearchMatch> {
    let mut results = Vec::new();
    let mut reader = XmlReader::from_reader(mmap);
    // [v4.33.7 Fix] trim_text(true)를 쓰면 개행/공백 소비 후 위치를 잡아 라인 번호가 어긋남
    reader.trim_text(false); 
    let mut buf = Vec::new();
    let mut current_tags = Vec::new();

    let pat_lower = pattern.to_lowercase();

    loop {
        let start_pos = reader.buffer_position();
        match reader.read_event_into(&mut buf) {
            Err(_) => break,
            Ok(Event::Eof) => break,
            Ok(Event::Start(e)) => {
                let end_pos = reader.buffer_position();
                let name = String::from_utf8_lossy(e.name().as_ref()).to_string();
                current_tags.push(name.clone());
                process_xml_attributes(&e, &name, pattern, &pat_lower, ac, is_exact, start_pos, end_pos, mmap, &mut results);
            },
            Ok(Event::Empty(e)) => {
                let end_pos = reader.buffer_position();
                let name = String::from_utf8_lossy(e.name().as_ref()).to_string();
                process_xml_attributes(&e, &name, pattern, &pat_lower, ac, is_exact, start_pos, end_pos, mmap, &mut results);
            },
            Ok(Event::End(_)) => { current_tags.pop(); },
            Ok(Event::Text(e)) => {
                let end_pos = reader.buffer_position();
                let raw = e.as_ref();
                if let Ok(text) = e.unescape() {
                    process_xml_text_item(raw, &text, &current_tags, &pat_lower, ac, is_exact, start_pos, end_pos, mmap, &mut results);
                }
            },
            Ok(Event::CData(e)) => {
                let end_pos = reader.buffer_position();
                let raw = e.as_ref();
                let text = String::from_utf8_lossy(raw);
                process_xml_text_item(raw, &text, &current_tags, &pat_lower, ac, is_exact, start_pos, end_pos, mmap, &mut results);
            },
            _ => (),
        }
        buf.clear();
    }
    results
}

fn process_xml_text_item(
    raw_bytes: &[u8],
    unescaped_text: &str,
    current_tags: &[String],
    _pat_lower: &str,
    ac: &aho_corasick::AhoCorasick,
    is_exact: bool,
    start_pos: usize,
    end_pos: usize,
    mmap: &[u8],
    results: &mut Vec<SearchMatch>
) {
    let trimmed = unescaped_text.trim();
    if !trimmed.is_empty() {
        let is_match = if is_exact {
            trimmed.to_lowercase() == _pat_lower
        } else {
            ac.find(unescaped_text).is_some()
        };

        if is_match {
            // [v4.33.9 Fix] 해당 이벤트 범위 내에서만 매턴을 찾아 정밀도 확보 (타 라인 간섭 차단)
            let mut match_offset = start_pos;
            let mut match_len = raw_bytes.len();

            let range_end = end_pos.min(mmap.len());
            if let Some(m) = ac.find(&mmap[start_pos..range_end]) {
                match_offset = start_pos + m.start();
                match_len = m.len();
            }

            let tag_path = current_tags.join("/");
            let line = get_line_number(mmap, match_offset);
            results.push(SearchMatch::new(line, format!("/{} | {}", tag_path, trimmed), Some(match_offset), Some(match_len)));
        }
    }
}

fn process_xml_attributes(
    e: &quick_xml::events::BytesStart,
    tag_name: &str,
    _pattern: &str,
    _pat_lower: &str,
    ac: &aho_corasick::AhoCorasick,
    is_exact: bool,
    start_pos: usize,
    end_pos: usize,
    mmap: &[u8],
    results: &mut Vec<SearchMatch>
) {
    for attr in e.attributes().flatten() {
        let key = String::from_utf8_lossy(attr.key.as_ref()).to_string();
        let val = String::from_utf8_lossy(&attr.value).to_string();
        
        let is_match = if is_exact {
            val.to_lowercase() == _pat_lower
        } else {
            ac.find(&val).is_some()
        };

        if is_match {
            // [v4.33.9 Fix] 해당 태그 바이트 범위 내에서만 매턴을 포착 (0/1 기반 오정렬 방지)
            let mut match_offset = start_pos;
            let mut match_len = attr.value.len();

            let range_end = end_pos.min(mmap.len());
            if let Some(m) = ac.find(&mmap[start_pos..range_end]) {
                match_offset = start_pos + m.start();
                match_len = m.len();
            }

            let line = get_line_number(mmap, match_offset);
            results.push(SearchMatch::new(line, format!("<{}> @{} | {}", tag_name, key, val), Some(match_offset), Some(match_len)));
        }
    }
}

/// Archive (.archive) 파일 상세 검색
fn search_archive_file(mmap: &[u8], pattern: &str, ac: &aho_corasick::AhoCorasick, is_exact: bool) -> Vec<SearchMatch> {
    let mut results = Vec::new();
    let pat_lower = pattern.to_lowercase();
    if let Ok(data) = serde_json::from_slice::<ArchiveData>(mmap) {
        for ns in data.subnamespaces {
            for child in ns.children {
                let s_text = child.source.text.to_lowercase();
                let t_text = child.translation.text.to_lowercase();

                let is_match = if is_exact {
                    s_text == pat_lower || t_text == pat_lower
                } else {
                    ac.find(&s_text).is_some() || ac.find(&t_text).is_some()
                };

                if is_match {
                    let content = format!("NS: {} | Key: {} | S: {} | T: {}", 
                        ns.namespace, child.key, child.source.text, child.translation.text);
                    results.push(SearchMatch::new(1, content, None, None));
                }
            }
        }
    }
    results
}

/// 검색 모드 문자열을 파싱하여 검색 옵션을 반환합니다.
fn parse_search_mode(mode: Option<String>) -> (bool, bool, bool, bool) {
    let m = mode.unwrap_or_default();
    (
        m.contains("JSON"),
        m.contains("XML"),
        m.contains("ARCHIVE"),
        m.contains("정확히 일치") || m.contains("EXACT"),
    )
}

#[pyfunction]
#[pyo3(signature = (path, pattern, special_mode=None))]
fn search_file(py: Python<'_>, path: &str, pattern: &str, special_mode: Option<String>) -> PyResult<Vec<SearchMatch>> {
    let path_buf = Path::new(path);
    let (is_json, is_xml, is_archive, is_exact) = parse_search_mode(special_mode);
    let ac = AhoCorasickBuilder::new().ascii_case_insensitive(true).match_kind(MatchKind::LeftmostFirst).build(&[pattern]).map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("{}", e)))?;

    let ext = path_buf.extension().and_then(|s| s.to_str()).unwrap_or("").to_lowercase();
    if ["xlsx", "xlsb", "xls", "xlsm"].contains(&ext.as_str()) {
        return Ok(search_excel_file(path_buf, pattern, &ac, is_exact));
    }

    py.allow_threads(|| {
        let file = File::open(path_buf)?;
        if file.metadata()?.len() == 0 { return Ok(Vec::new()); }
        let mmap = unsafe { Mmap::map(&file)? };

        if is_json { return Ok(search_json_file(&mmap, pattern, &ac, is_exact)); }
        if is_xml { return Ok(search_xml_file(&mmap, pattern, &ac, is_exact)); }
        if is_archive { return Ok(search_archive_file(&mmap, pattern, &ac, is_exact)); }

        Ok(do_search_in_mmap(&mmap, pattern, &ac, is_exact))
    })
}

fn do_search_in_mmap(mmap: &[u8], pattern: &str, ac: &aho_corasick::AhoCorasick, is_exact: bool) -> Vec<SearchMatch> {
    let mut results = Vec::new();
    let encoding = detect_encoding(mmap);
    let pat_lower = pattern.to_lowercase();

    if encoding == UTF_8 {
        if is_exact {
            // [v4.33.6 Fix] 정확히 일치 모드 (텍스트)
            let content = String::from_utf8_lossy(mmap);
            let mut line_number = 1;
            let mut offset = 0;
            for line in content.lines() {
                if line.trim().to_lowercase() == pat_lower {
                    results.push(SearchMatch::new(line_number, line.to_string(), Some(offset), Some(line.len())));
                }
                offset += line.len() + 1; // 대략적인 오프셋 (LF 기준)
                line_number += 1;
            }
        } else {
            let mut line_number = 1;
            let mut last_line_start = 0;
            for mat in ac.find_iter(mmap) {
                let match_start = mat.start();
                for i in last_line_start..match_start {
                    if mmap[i] == b'\n' { line_number += 1; last_line_start = i + 1; }
                }
                let mut line_end = match_start;
                while line_end < mmap.len() && mmap[line_end] != b'\n' && mmap[line_end] != b'\r' { line_end += 1; }
                if results.last().map_or(true, |m: &SearchMatch| m.line != line_number) {
                    let line_bytes = &mmap[last_line_start..line_end];
                    results.push(SearchMatch::new(line_number, String::from_utf8_lossy(line_bytes).trim().to_string(), Some(last_line_start), Some(line_end - last_line_start)));
                }
            }
        }
    } else {
        let full_content = decode_bytes(mmap, encoding);
        let mut line_number = 1;
        for line in full_content.lines() {
            let line_val = line.trim();
            let is_match = if is_exact {
                line_val.to_lowercase() == pat_lower
            } else {
                ac.find(line).is_some()
            };
            if is_match {
                results.push(SearchMatch::new(line_number, line_val.to_string(), None, None));
            }
            line_number += 1;
        }
    }
    results
}

#[pyfunction]
#[pyo3(signature = (paths, pattern, extensions=None, special_mode=None))]
fn search_dir(py: Python<'_>, paths: Vec<String>, pattern: String, extensions: Option<Vec<String>>, special_mode: Option<String>) -> PyResult<Vec<(String, Vec<SearchMatch>)>> {
    py.allow_threads(|| {
        let (is_json, is_xml, is_archive, is_exact) = parse_search_mode(special_mode);
        let ac = AhoCorasickBuilder::new().ascii_case_insensitive(true).match_kind(MatchKind::LeftmostFirst).build(&[&pattern]).map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("{}", e)))?;
        let exts = extensions.map(|v| v.iter().map(|s| s.trim_start_matches('.').to_lowercase()).collect::<HashSet<_>>());

        let mut all_files = Vec::new();
        for root_path in &paths {
            let walker = WalkBuilder::new(root_path).hidden(false).ignore(false).git_global(false).git_ignore(false).git_exclude(false).threads(2).build();
            for entry in walker {
                if let Ok(e) = entry { if e.file_type().map_or(false, |ft| ft.is_file()) { all_files.push(e.into_path()); } }
            }
        }

        let results: Vec<(String, Vec<SearchMatch>)> = all_files.par_iter().filter_map(|path| {
            let ac_ref = &ac;
            if let Some(ref valid_exts) = exts {
                if let Some(ext) = path.extension().and_then(|s| s.to_str()) {
                    if !valid_exts.contains(&ext.to_lowercase()) { return None; }
                } else { return None; }
            }

            let res = if let Some(ext) = path.extension().and_then(|s| s.to_str()) {
                let ext_lower = ext.to_lowercase();
                if ["xlsx", "xlsb", "xls", "xlsm"].contains(&ext_lower.as_str()) {
                    // [v4.33.10 Fix] calamine 내부 패닉(xls.rs 인덱스 에러 등) 발생 시 전체 프로세스 종료 방지
                    let path_buf = path.to_path_buf();
                    let pat = pattern.to_string();
                    let ac_clone = ac.clone();
                    
                    match panic::catch_unwind(move || {
                        search_excel_file(&path_buf, &pat, &ac_clone, is_exact)
                    }) {
                        Ok(res) => res,
                        Err(_) => Vec::new(), // 패닉 발생 시 해당 파일은 건너뜀
                    }
                } else {
                    File::open(path).ok().and_then(|f| {
                        if f.metadata().ok()?.len() == 0 { return Some(Vec::new()); }
                        let mmap = unsafe { Mmap::map(&f).ok()? };
                        if is_json && ext_lower == "json" {
                            Some(search_json_file(&mmap, &pattern, ac_ref, is_exact))
                        } else if is_xml && ext_lower == "xml" {
                            Some(search_xml_file(&mmap, &pattern, ac_ref, is_exact))
                        } else if is_archive && ext_lower == "archive" {
                            Some(search_archive_file(&mmap, &pattern, ac_ref, is_exact))
                        } else {
                            Some(do_search_in_mmap(&mmap, &pattern, ac_ref, is_exact))
                        }
                    }).unwrap_or_default()
                }
            } else {
                File::open(path).ok().and_then(|f| {
                    if f.metadata().ok()?.len() == 0 { return Some(Vec::new()); }
                    let mmap = unsafe { Mmap::map(&f).ok()? };
                    Some(do_search_in_mmap(&mmap, &pattern, ac_ref, is_exact))
                }).unwrap_or_default()
            };

            if res.is_empty() { None } else { Some((path.to_string_lossy().to_string(), res)) }
        }).collect();
        Ok(results)
    })
}

#[pyfunction]
#[pyo3(signature = (paths, keyword, extensions=None, special_mode=None))]
fn find_files_with_keyword(py: Python<'_>, paths: Vec<String>, keyword: String, extensions: Option<Vec<String>>, special_mode: Option<String>) -> PyResult<Vec<(String, u64)>> {
    py.allow_threads(|| {
        let (is_json, is_xml, is_archive, is_exact) = parse_search_mode(special_mode);
        let ac = AhoCorasickBuilder::new().ascii_case_insensitive(true).match_kind(MatchKind::LeftmostFirst).build(&[&keyword]).map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("{}", e)))?;
        let exts = extensions.map(|v| v.iter().map(|s| s.trim_start_matches('.').to_lowercase()).collect::<HashSet<_>>());

        let results: Vec<(String, u64)> = paths.par_iter().flat_map(|root_path| {
            let ac_ref = &ac;
            let mut local_results = Vec::new();
            let walker = WalkBuilder::new(root_path).threads(1).build();
            for entry in walker {
                if let Ok(e) = entry {
                    if e.file_type().map_or(false, |ft| ft.is_file()) {
                        let path = e.path();
                        if let Some(ref valid_exts) = exts {
                            if let Some(ext) = path.extension().and_then(|s| s.to_str()) {
                                if !valid_exts.contains(&ext.to_lowercase()) { continue; }
                            } else { continue; }
                        }
                        
                        let found = if let Some(ext) = path.extension().and_then(|s| s.to_str()) {
                            let ext_lower = ext.to_lowercase();
                            if ["xlsx", "xlsb", "xls", "xlsm"].contains(&ext_lower.as_str()) {
                                // [v4.33.10 Fix] 스캔 모드에서도 패닉 보호 적용
                                let path_buf = path.to_path_buf();
                                let key = keyword.to_string();
                                let ac_clone = ac.clone();
                                match panic::catch_unwind(move || {
                                    !search_excel_file(&path_buf, &key, &ac_clone, is_exact).is_empty()
                                }) {
                                    Ok(res) => res,
                                    Err(_) => false,
                                }
                            } else {
                                if let Ok(file) = File::open(path) {
                                    if let Ok(meta) = file.metadata() {
                                        if meta.len() > 0 {
                                            if let Ok(mmap) = unsafe { Mmap::map(&file) } {
                                                if is_json && ext_lower == "json" {
                                                    !search_json_file(&mmap, &keyword, ac_ref, is_exact).is_empty()
                                                } else if is_xml && ext_lower == "xml" {
                                                    !search_xml_file(&mmap, &keyword, ac_ref, is_exact).is_empty()
                                                } else if is_archive && ext_lower == "archive" {
                                                    !search_archive_file(&mmap, &keyword, ac_ref, is_exact).is_empty()
                                                } else {
                                                    check_text_contains_mmap(&mmap, &keyword, ac_ref, is_exact)
                                                }
                                            } else { false }
                                        } else { false }
                                    } else { false }
                                } else { false }
                            }
                        } else {
                            check_text_contains(path, &keyword, ac_ref, is_exact)
                        };

                        if found { if let Ok(meta) = path.metadata() { local_results.push((path.to_string_lossy().to_string(), meta.len())); } }
                    }
                }
            }
            local_results
        }).collect();
        Ok(results)
    })
}

fn check_text_contains(path: &Path, pattern: &str, ac: &aho_corasick::AhoCorasick, is_exact: bool) -> bool {
    if let Ok(file) = File::open(path) {
        if let Ok(meta) = file.metadata() {
            if meta.len() > 0 {
                if let Ok(mmap) = unsafe { Mmap::map(&file) } {
                    return check_text_contains_mmap(&mmap, pattern, ac, is_exact);
                }
            }
        }
    }
    false
}

fn check_text_contains_mmap(mmap: &[u8], pattern: &str, ac: &aho_corasick::AhoCorasick, is_exact: bool) -> bool {
    let encoding = detect_encoding(mmap);
    if encoding == UTF_8 { return !do_search_in_mmap(mmap, pattern, ac, is_exact).is_empty(); }
    else {
        let content = decode_bytes(mmap, encoding);
        if is_exact {
            content.lines().any(|l| l.trim().to_lowercase() == pattern.to_lowercase())
        } else {
            ac.find(&content).is_some()
        }
    }
}

#[pymodule]
fn sf_engine(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // [v4.33.10 Fix] 패닉 발생 시 콘솔에 지저분한 메시지가 출력되지 않도록 무음 처리
    // catch_unwind로 이미 제어하고 있으므로 출력만 막음
    std::panic::set_hook(Box::new(|_| {}));

    m.add_class::<SearchMatch>()?;
    m.add_function(wrap_pyfunction!(search_file, m)?)?;
    m.add_function(wrap_pyfunction!(search_dir, m)?)?;
    m.add_function(wrap_pyfunction!(find_files_with_keyword, m)?)?;
    Ok(())
}
