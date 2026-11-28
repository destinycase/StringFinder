mod casefold_table;
mod excel_date_formats;
mod excel_search;
mod json_search;
mod textkey;
mod types;
mod ue_archive_search;
mod utils;
mod xml_search;

mod memory_budget;
mod py_api;
mod snapshot;
mod text_search;
mod walker;

use aho_corasick::{AhoCorasickBuilder, MatchKind};
use encoding_rs::{Encoding, UTF_16BE, UTF_16LE, UTF_8};
use ignore::WalkBuilder;
use memmap2::Mmap;
use pyo3::prelude::*;
use pyo3::wrap_pyfunction;
use std::collections::HashSet;
use std::fs::File;
use std::io::Read;
use std::path::Path;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::time::SystemTime;

use crate::excel_search::{search_excel_file, ExcelFileError};
use crate::json_search::search_json_file_with_policy;
use crate::types::{RawMatch, ResultKind, SearchMatch, SearchOptions};
use crate::ue_archive_search::{search_archive_file, ArchiveSearchError};
use crate::utils::{
    build_glob_set, decode_structured_bytes, detect_encoding, generate_search_patterns, is_binary,
    match_filename_glob, parse_search_mode,
};
use crate::xml_search::{search_xml_file, XmlSearchError};
use memory_budget::*;
use py_api::*;
use snapshot::*;
use text_search::*;
use walker::*;

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
/// 파일 일부를 해석하지 못했음(디코딩 오류)을 알리는 파일 단위 표시입니다. 뒤에 인코딩 이름이 붙습니다.
const MATCH_META_DECODE_PARTIAL_PREFIX: &str = "__SF_DECODE_PARTIAL__|";

const MONITOR_INTERVAL_MS: u64 = 100;

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

    // 휴지통 경로 제외는 호출하는 쪽(디렉터리 순회·파일 목록·단일 파일)에서 파일마다 한 번만 확인합니다.

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
    let file_size_limit = file_size_limit_for(path, is_archive, max_json_size);
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
    // CP949 확인이 필요한 일반 텍스트는 확인용 디코딩과 검색을 한 번에 하고, 그 결과를 아래에서 씁니다.
    let mut korean_results: Option<Vec<RawMatch>> = None;
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
        match detect_text_search_encoding(mmap_c, matcher.pat_key(), is_exact) {
            TextEncoding::Known(encoding) => encoding,
            TextEncoding::KoreanUnverified => {
                match search_korean_strict(mmap_c, matcher, is_exact, &stop_flag, max_per_file) {
                    Some(found) => {
                        korean_results = Some(found);
                        encoding_rs::EUC_KR
                    }
                    None => crate::utils::detect_after_korean_failed(mmap_c),
                }
            }
        }
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
                        ResultKind::Binary,
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
            let mut matches = match korean_results.take() {
                Some(found) => found,
                None => do_search_with_mmap(
                    searchable_mmap,
                    enc,
                    matcher,
                    is_exact,
                    &stop_flag,
                    max_per_file,
                ),
            };
            if enc == UTF_8
                && encoding.is_none()
                && matches
                    .iter()
                    .any(|m| m.1.contains('\u{fffd}') || m.4 == ResultKind::DecodePartial)
            {
                // 앞부분만 보고 UTF-8로 정했는데 깨진 글자가 보이거나 해석하지 못한 바이트를 만났으면
                // 파일 전체로 다시 판정합니다(일치가 없어도 다시 판정해 잘못된 안내를 막음).
                // CP949 확인이 필요하면 확인과 재검색을 한 번의 디코딩으로 합니다.
                match crate::utils::detect_until_korean(mmap_c) {
                    crate::utils::Detection::Encoding(verified) if verified != UTF_8 => {
                        matches = do_search_with_mmap(
                            mmap_c,
                            verified,
                            matcher,
                            is_exact,
                            &stop_flag,
                            max_per_file,
                        );
                    }
                    crate::utils::Detection::Encoding(_) => {}
                    crate::utils::Detection::KoreanCheck => {
                        match search_korean_strict(
                            mmap_c,
                            matcher,
                            is_exact,
                            &stop_flag,
                            max_per_file,
                        ) {
                            Some(found) => matches = found,
                            None => {
                                let verified = crate::utils::detect_after_korean_failed(mmap_c);
                                if verified != UTF_8 {
                                    matches = do_search_with_mmap(
                                        mmap_c,
                                        verified,
                                        matcher,
                                        is_exact,
                                        &stop_flag,
                                        max_per_file,
                                    );
                                }
                            }
                        }
                    }
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
    m.add_function(wrap_pyfunction!(search_file, m)?)?;
    m.add_function(wrap_pyfunction!(search_dir, m)?)?;
    m.add_function(wrap_pyfunction!(find_key_match_char, m)?)?;
    m.add_function(wrap_pyfunction!(find_key_match_spans, m)?)?;
    m.add_function(wrap_pyfunction!(detect_file_encoding, m)?)?;
    m.add("API_VERSION", 8)?;
    m.add("MAX_SEARCH_QUERY_LENGTH", MAX_SEARCH_QUERY_LENGTH)?;
    // Cargo.toml version 필드를 빌드 시점에 자동으로 읽어 Python 측에 노출합니다.
    m.add("ENGINE_VERSION", env!("CARGO_PKG_VERSION"))?;
    // 엔진 소스 지문(build.rs). 엔진 버전은 앱 버전과 따로 매기므로, 엔진이 지금 소스로 빌드됐는지는
    // 이 값과 tools/engine_source_hash.py의 계산을 견줘 확인합니다(7.7.6).
    m.add("SOURCE_HASH", env!("SF_SOURCE_HASH"))?;
    Ok(())
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
                            let (content, kind) = extract_line_content_bytes(
                                line.as_bytes(),
                                0,
                                line.len(),
                                start,
                                Some(6),
                            );
                            (i + 1, content, None, None, kind)
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
                    false,
                );
                assert_eq!(actual, expected, "encoding={encoding:?}, exact={exact}");
            }
        }
    }

    /// CP949 확인과 검색을 한 번의 디코딩으로 하는 경로가 "판정을 끝낸 뒤 검색"과 같은 결과를 내는지 확인합니다.
    #[test]
    fn korean_strict_search_matches_detect_then_search() {
        let stop = Arc::new(AtomicBool::new(false));
        let line = encoding_rs::EUC_KR
            .encode("가나다 needle 목표단어 끝\r\n")
            .0
            .into_owned();
        let mut valid = Vec::new();
        for _ in 0..3000 {
            valid.extend_from_slice(&line);
        }
        let mut invalid_tail = valid.clone();
        invalid_tail.extend_from_slice(b"\xff\xff broken");
        let mut invalid_head = b"\xff\xff broken\r\n".to_vec();
        invalid_head.extend_from_slice(&valid);
        for (name, data) in [
            ("valid", &valid),
            ("invalid_tail", &invalid_tail),
            ("invalid_head", &invalid_head),
        ] {
            assert!(matches!(
                crate::utils::detect_until_korean(data),
                crate::utils::Detection::KoreanCheck
            ));
            for query in ["needle", "목표단어"] {
                let matcher = textkey::Matcher::new(query).unwrap();
                for max_per_file in [5, 10_000] {
                    let strict = search_korean_strict(data, &matcher, false, &stop, max_per_file);
                    let detected = detect_encoding(data);
                    if detected == encoding_rs::EUC_KR {
                        let expected = do_search_with_mmap(
                            data,
                            detected,
                            &matcher,
                            false,
                            &stop,
                            max_per_file,
                        );
                        assert_eq!(
                            strict.as_ref(),
                            Some(&expected),
                            "{name} {query} {max_per_file}"
                        );
                    } else {
                        assert!(strict.is_none(), "{name} {query} {max_per_file}");
                    }
                }
            }
        }
    }

    #[test]
    fn text_encoding_detection_preserves_full_search_policy() {
        let mut bytes = b"needle\n".to_vec();
        bytes.resize(65536, b'a');
        bytes.extend_from_slice(&encoding_rs::EUC_KR.encode("한글").0);
        assert_eq!(
            detect_text_search_encoding(&bytes, "needle", false).resolve(&bytes),
            UTF_8
        );
        assert_eq!(
            detect_text_search_encoding(&bytes, "needle", true).resolve(&bytes),
            encoding_rs::EUC_KR
        );
        assert_eq!(
            detect_text_search_encoding(&bytes, "한글", false).resolve(&bytes),
            encoding_rs::EUC_KR
        );
        let utf16: Vec<u8> = "needle".encode_utf16().flat_map(u16::to_le_bytes).collect();
        assert_eq!(
            detect_text_search_encoding(&utf16, "n", false).resolve(&utf16),
            UTF_16LE
        );
        // 앞부분 판정이 CP949 두 바이트 글자의 중간에서 잘려도 CP949로 판정합니다.
        // U+AC4B는 CP949로 B0 C0입니다. 둘째 바이트가 0xC0 이상이라 UTF-8 경계 보정으로 붙지 않습니다.
        let mut korean = b"needle ".to_vec();
        korean.resize(65535, b'a');
        for _ in 0..100 {
            korean.extend_from_slice(&encoding_rs::EUC_KR.encode("\u{AC4B}").0);
        }
        assert_eq!(&korean[65535..65537], b"\xb0\xc0");
        assert_eq!(
            detect_text_search_encoding(&korean, "needle", false),
            TextEncoding::Known(encoding_rs::EUC_KR)
        );
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
                    false,
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
        let original = vec![(
            1,
            "already delivered".to_string(),
            None,
            None,
            ResultKind::Match,
        )];
        let mut actual = original.clone();
        search_non_utf8_chunks(
            &bytes,
            UTF_16LE,
            &textkey::Matcher::new("needle").unwrap(),
            false,
            &Arc::new(AtomicBool::new(true)),
            5000,
            &mut actual,
            false,
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
            (1, "one".to_string(), None, None, ResultKind::Match),
            (2, "two".to_string(), None, None, ResultKind::Match),
            (3, "three".to_string(), None, None, ResultKind::Match),
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
            (1, "one".to_string(), None, None, ResultKind::Match),
            (2, "two".to_string(), None, None, ResultKind::Match),
            (3, "three".to_string(), None, None, ResultKind::Match),
            (
                0,
                format!(
                    "{}{}",
                    crate::json_search::JSON_DEPTH_LIMIT_MARKER_PREFIX,
                    20
                ),
                None,
                None,
                ResultKind::JsonDepthLimit,
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
            (1, "one".to_string(), None, None, ResultKind::Match),
            (2, "two".to_string(), None, None, ResultKind::Match),
            (
                0,
                "__SF_EXCEL_SHEET_ERR__|Sheet2|parse failure".to_string(),
                None,
                None,
                ResultKind::SheetError,
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
                false,
                None
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

    fn unique_temp_root(name: &str) -> std::path::PathBuf {
        let unique = SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!("sf-{name}-{unique}"));
        fs::create_dir_all(&root).unwrap();
        root
    }

    /// archive만 검색 크기 상한 설정과 별도로 8GB까지 검색합니다(7.4.5).
    #[test]
    fn archive_uses_its_own_size_limit() {
        assert_eq!(
            file_size_limit_for(Path::new("a/Game.archive"), true, 8),
            ARCHIVE_MAX_FILE_BYTES
        );
        assert_eq!(
            file_size_limit_for(Path::new("a/Game.ARCHIVE"), true, 8),
            ARCHIVE_MAX_FILE_BYTES
        );
        // archive 모드가 아니거나 확장자가 다르면 공통 상한입니다.
        assert_eq!(
            file_size_limit_for(Path::new("a/Game.archive"), false, 8),
            8
        );
        assert_eq!(file_size_limit_for(Path::new("a/Game.json"), true, 8), 8);

        let root = unique_temp_root("archive-limit");
        let path = root.join("Game.archive");
        fs::write(
            &path,
            r#"{"Children":[{"Key":"K","Source":{"Text":"needle"},"Translation":{"Text":""}}]}"#,
        )
        .unwrap();
        let ac = build_test_ac("needle");
        let result = search_file_internal(InternalSearchParams {
            encoding: None,
            allow_duplicate_json_keys: false,
            path: &path,
            matcher: &textkey::Matcher::new("needle").unwrap(),
            ac: &ac,
            is_exact: false,
            is_json: false,
            is_archive: true,
            is_xml: false,
            is_excel: false,
            exclude_hidden: false,
            exclude_binary: false,
            stop_flag: Arc::new(AtomicBool::new(false)),
            max_per_file: 10,
            max_json_depth: 100,
            // 공통 상한(8바이트)보다 커도 archive는 검색합니다.
            max_json_size: 8,
        });
        assert_eq!(result.unwrap().unwrap().len(), 1);
        fs::remove_dir_all(root).unwrap();
    }

    /// UTF-8 archive는 고정 몫만, UTF-16은 1.5배, 그 밖의 인코딩은 3배를 더해 예약합니다.
    #[test]
    fn archive_memory_estimate_depends_on_encoding() {
        let root = unique_temp_root("archive-memory");
        let size = 9 * 1024 * 1024usize;

        let utf8 = root.join("utf8.archive");
        fs::write(&utf8, vec![b' '; size]).unwrap();
        assert_eq!(
            estimated_archive_memory(&utf8, None),
            ARCHIVE_FIXED_OVERHEAD
        );

        let utf16 = root.join("utf16.archive");
        let mut bytes = vec![0xFF, 0xFE];
        bytes.extend(std::iter::repeat_n([b' ', 0u8], size / 2).flatten());
        fs::write(&utf16, &bytes).unwrap();
        let length = bytes.len() as u64;
        assert_eq!(
            estimated_archive_memory(&utf16, None),
            length * 3 / 2 + ARCHIVE_FIXED_OVERHEAD
        );

        // 작은 파일은 예약하지 않습니다.
        let small = root.join("small.archive");
        fs::write(&small, b"{}").unwrap();
        assert_eq!(estimated_archive_memory(&small, None), 0);

        // 앞 64KB가 영문뿐이어도 뒤에 CP949 글자가 있으면 3배로 예약합니다(7.4.7).
        let cp949 = root.join("cp949.archive");
        let mut bytes = vec![b' '; size];
        bytes.extend_from_slice(&[0xC7, 0xD1, 0xB1, 0xDB]);
        fs::write(&cp949, &bytes).unwrap();
        let legacy = bytes.len() as u64 * 3 + ARCHIVE_FIXED_OVERHEAD;
        assert_eq!(estimated_archive_memory(&cp949, None), legacy);
        // 사용자가 고른 인코딩을 따릅니다.
        assert_eq!(
            estimated_archive_memory(&utf8, Some(encoding_rs::EUC_KR)),
            size as u64 * 3 + ARCHIVE_FIXED_OVERHEAD
        );
        assert_eq!(
            estimated_archive_memory(&cp949, Some(UTF_8)),
            ARCHIVE_FIXED_OVERHEAD
        );
        assert_eq!(
            estimated_archive_memory(&utf8, Some(UTF_16LE)),
            size as u64 * 3 / 2 + ARCHIVE_FIXED_OVERHEAD
        );
        fs::remove_dir_all(root).unwrap();
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
                for (line, _content, offset, length, _kind) in &results {
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

    /// 조각 크기보다 긴 줄(창 단위 처리)에서 창 경계 근처에 검색어·유발 글자·결합 부호를 넣어도
    /// 기준 구현과 결과가 같은지 확인합니다.
    #[test]
    fn long_lines_match_the_line_by_line_reference() {
        let inserts = [
            "ka",
            "KA",
            "st",
            "\u{FB06}",
            "\u{212A}a",
            "k\u{301}",
            "e",
            "\u{E9}",
            "\u{AC00}",
            "\u{1100}\u{1161}",
            "\u{430}",
            "\n",
            "\r\n",
            "\r",
            "\u{DF}",
        ];
        let patterns = ["ka", "st", "k", "e", "\u{AC00}", "\u{430}"];
        let mut state = 0x0BAD_C0DE_1234_5678u64;
        let mut next = |bound: usize| {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            (state % bound as u64) as usize
        };
        let stop_flag = Arc::new(AtomicBool::new(false));
        for round in 0..240 {
            let total = TEXT_CHUNK_SIZE * 3 + next(TEXT_CHUNK_SIZE);
            let mut bytes = vec![b'x'; total];
            let mut places: Vec<(usize, &str)> = Vec::new();
            for _ in 0..(4 + next(12)) {
                // 창 경계(조각 크기의 배수) 근처에 주로 넣습니다.
                let boundary = TEXT_CHUNK_SIZE * (1 + next(3));
                let position = (boundary + next(9)).saturating_sub(4).min(total - 8);
                places.push((position, inserts[next(inserts.len())]));
            }
            places.sort_by_key(|place| std::cmp::Reverse(place.0));
            for (position, piece) in places {
                bytes.splice(position..position, piece.bytes());
            }
            let text = String::from_utf8(bytes).unwrap();
            let pattern = patterns[round % patterns.len()];
            let matcher = textkey::Matcher::new(pattern).unwrap();
            let results =
                do_search_with_mmap(text.as_bytes(), UTF_8, &matcher, false, &stop_flag, 10_000);
            let mut actual: Vec<(usize, usize)> = Vec::new();
            for (line, _content, offset, length, _kind) in &results {
                if let (Some(offset), Some(length)) = (offset, length) {
                    let piece = &text.as_bytes()[*offset..offset + length];
                    assert_eq!(
                        textkey::key_reference(std::str::from_utf8(piece).unwrap()),
                        textkey::key_reference(pattern),
                        "wrong position round={round} pattern={pattern:?}"
                    );
                }
                match actual.last_mut() {
                    Some((last, count)) if last == line => *count += 1,
                    _ => actual.push((*line, 1)),
                }
            }
            assert_eq!(
                actual,
                reference_text_results(&text, pattern, false),
                "round={round} pattern={pattern:?}"
            );
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

    /// 실제 파일 줄이 안내 표식처럼 생겨도 일치 줄로 남습니다(7.2.9). 예전에는 글자 모양으로 분류해
    /// `ERR_…|` 줄 하나 때문에 파일 전체가 건너뛴 것으로 처리됐습니다.
    #[test]
    fn marker_like_lines_stay_plain_matches() {
        let stop_flag = Arc::new(AtomicBool::new(false));
        let text = "ERR_LOGIN_FAILED|login needle failed\n\
                    OK_LOGIN|needle\n\
                    plain needle\n\
                    __SF_TRUNCATED__ needle\n\
                    __SF_LONG_LINE__|needle\n\
                    __SF_BINARY_MATCH__|needle\n\
                    __SF_EXCEL_SHEET_ERR__|needle|x\n";
        let matcher = textkey::Matcher::new("needle").unwrap();
        let matches =
            do_search_with_mmap(text.as_bytes(), UTF_8, &matcher, false, &stop_flag, 5000);
        assert_eq!(matches.len(), 7);
        for (index, item) in matches.iter().enumerate() {
            assert_eq!(item.0, index + 1);
            assert_eq!(item.4, ResultKind::Match, "{:?}", item);
            // 짧은 줄은 표식처럼 보여도 위치를 잃지 않습니다.
            assert!(item.2.is_some() && item.3 == Some(6), "{:?}", item);
            let converted = SearchMatch::from(item.clone());
            assert_eq!(converted.kind, "match");
            assert_eq!(converted.code, None);
            assert_eq!(converted.detail, None);
        }
        assert_eq!(matches[0].1, "ERR_LOGIN_FAILED|login needle failed");

        // 정확히 일치도 같습니다.
        let exact = textkey::Matcher::new("ERR_A|needle").unwrap();
        let matches = do_search_with_mmap(
            b"ERR_A|needle\n__SF_TRUNCATED__\n",
            UTF_8,
            &exact,
            true,
            &stop_flag,
            5000,
        );
        assert_eq!(matches.len(), 1);
        assert_eq!(matches[0].4, ResultKind::Match);
    }

    /// 표식처럼 생긴 일치 줄도 파일당 상한에 셉니다. 진짜 안내만 세지 않습니다.
    #[test]
    fn match_limit_counts_marker_like_matches() {
        let matches = vec![
            (
                0,
                "__SF_TRUNCATED__".to_string(),
                None,
                None,
                ResultKind::Match,
            ),
            (0, "__SF_X__|a".to_string(), None, None, ResultKind::Match),
            (3, "ERR_X|b".to_string(), None, None, ResultKind::Match),
        ];
        let limited = apply_match_limit(matches, 2);
        assert_eq!(limited.len(), 3);
        assert_eq!(limited[0].4, ResultKind::Match);
        assert_eq!(limited[1].4, ResultKind::Match);
        assert_eq!(limited[2].4, ResultKind::Truncated);

        // 일치 하나가 `ERR_`로 시작해도 상한 계산에서 따로 다루지 않습니다.
        let single = vec![
            (1, "ERR_X|a".to_string(), None, None, ResultKind::Match),
            (2, "two".to_string(), None, None, ResultKind::Match),
        ];
        let limited = apply_match_limit(single, 1);
        assert_eq!(limited.len(), 2);
        assert_eq!(limited[1].4, ResultKind::Truncated);
    }

    /// 안내의 종류·코드·상세는 만든 곳의 종류에서 나옵니다.
    #[test]
    fn metadata_kinds_convert_to_python_fields() {
        let cases = [
            (
                ResultKind::Truncated,
                MATCH_META_TRUNCATED.to_string(),
                "truncated",
                Some("TRUNCATED"),
                None,
            ),
            (
                ResultKind::Binary,
                format!("{MATCH_META_BINARY_PREFIX}3"),
                "binary",
                Some("BINARY"),
                Some("3"),
            ),
            (
                ResultKind::LongLine,
                format!("{MATCH_META_LONG_LINE_PREFIX}...a|b..."),
                "long_line",
                Some("LONG_LINE"),
                Some("...a|b..."),
            ),
            (
                ResultKind::DecodePartial,
                format!("{MATCH_META_DECODE_PARTIAL_PREFIX}UTF-8"),
                "partial",
                Some("DECODE_PARTIAL"),
                Some("UTF-8"),
            ),
            (
                ResultKind::JsonDepthLimit,
                format!("{}7", crate::json_search::JSON_DEPTH_LIMIT_MARKER_PREFIX),
                "partial",
                Some("JSON_DEPTH_LIMIT"),
                Some("7"),
            ),
            (
                ResultKind::SheetError,
                "__SF_EXCEL_SHEET_ERR__|Sheet 2|parse failure".to_string(),
                "sheet_error",
                Some("EXCEL_SHEET_ERROR"),
                Some("Sheet 2|parse failure"),
            ),
        ];
        for (kind, content, name, code, detail) in cases {
            let converted = SearchMatch::from((0, content.clone(), None, None, kind));
            assert_eq!(converted.kind, name);
            assert_eq!(converted.code.as_deref(), code);
            assert_eq!(converted.detail.as_deref(), detail);
            assert_eq!(converted.content, content);
            assert!(kind.is_metadata() || kind == ResultKind::LongLine);
        }
        assert!(!ResultKind::Match.is_metadata());
        assert!(!ResultKind::LongLine.is_metadata());
    }

    /// 긴 줄은 미리보기 종류로 오고 위치가 없습니다.
    #[test]
    fn long_line_match_has_long_line_kind() {
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
        assert_eq!(matches[0].4, ResultKind::LongLine);
        assert_eq!((matches[0].2, matches[0].3), (None, None));
        assert!(!matches[0].4.is_metadata());
    }
}
