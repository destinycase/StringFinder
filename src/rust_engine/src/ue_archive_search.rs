//! Unreal Engine 현지화 `.archive` 문서의 원문·번역문·Key 값만 검색합니다.
//!
//! 문서 전체의 JSON 문법·중복 키·깊이는 JSON 값만 검색과 같은 파서로 먼저 검증합니다.
//! 그다음 구조를 해석하므로, 앞부분에서 찾은 결과가 있어도 문서가 잘못되면 결과를 확정하지 않습니다.

use serde_json::{Map, Value};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use crate::types::RawMatch;

/// 결과 내용 앞에 붙는 표식입니다. 뒤에는 `[Namespace, Key, 원문, 번역문]` JSON 배열이 옵니다.
/// 각 값에 탭·줄바꿈이 있어도 필드가 섞이지 않도록 구분자 대신 JSON 배열을 사용합니다.
pub const ARCHIVE_FIELDS_PREFIX: &str = "ARCHIVE_FIELDS|";

pub enum ArchiveSearchError {
    /// JSON 문법·중복 키 등 문서 자체의 오류입니다.
    Json(String),
    /// JSON으로는 올바르지만 UE 현지화 archive 구조가 아닌 경우입니다. 값은 문제 위치 경로입니다.
    Format(String),
}

struct ArchiveSearch<'a> {
    pattern_upper: String,
    ac: &'a aho_corasick::AhoCorasick,
    is_exact: bool,
    max_per_file: usize,
    stop_flag: Arc<AtomicBool>,
    results: Vec<RawMatch>,
}

impl ArchiveSearch<'_> {
    fn value_matches(&self, value: &str) -> bool {
        if self.is_exact {
            // ASCII 값은 정규화해도 그대로이므로 중간 문자열 없이 비교합니다(일반 텍스트 정확히 일치와 같은 방식).
            if value.is_ascii() {
                return value.trim().eq_ignore_ascii_case(&self.pattern_upper);
            }
            // JSON 값만 검색의 정확히 일치와 같은 정규화·공백·대소문자 규칙입니다.
            crate::utils::normalize_unicode(value)
                .trim()
                .to_lowercase()
                .to_uppercase()
                == self.pattern_upper
        } else {
            self.ac.find(value).is_some()
        }
    }

    fn record(&mut self, namespace: &str, key: &str, source: &str, translation: &str) {
        // 상한보다 하나 더 모아 두면 호출자가 잘림 표식을 붙일 수 있습니다.
        if self.results.len() > self.max_per_file {
            return;
        }
        if !(self.value_matches(key)
            || self.value_matches(source)
            || self.value_matches(translation))
        {
            return;
        }
        let fields = [namespace, key, source, translation];
        let content = match serde_json::to_string(&fields) {
            Ok(encoded) => format!("{ARCHIVE_FIELDS_PREFIX}{encoded}"),
            Err(_) => return,
        };
        // DOM에서는 원본 위치를 알 수 없으므로 줄·위치를 추정하지 않습니다.
        self.results.push((0, content, None, None));
    }
}

fn format_error(path: &str) -> ArchiveSearchError {
    ArchiveSearchError::Format(if path.is_empty() {
        "/".to_owned()
    } else {
        path.to_owned()
    })
}

fn text_field<'v>(
    entry: &'v Map<String, Value>,
    field: &str,
    path: &str,
) -> Result<&'v str, ArchiveSearchError> {
    let text_path = format!("{path}/{field}/Text");
    entry
        .get(field)
        .and_then(Value::as_object)
        .and_then(|object| object.get("Text"))
        .and_then(Value::as_str)
        .ok_or_else(|| format_error(&text_path))
}

fn join_namespace(parent: &str, own: &str) -> String {
    match (parent.is_empty(), own.is_empty()) {
        (_, true) => parent.to_owned(),
        (true, false) => own.to_owned(),
        (false, false) => format!("{parent}.{own}"),
    }
}

/// 노드의 `Children`을 먼저, 그다음 `Subnamespaces`를 문서 순서대로 방문합니다.
/// Python 경로도 같은 순서를 사용하므로 두 경로의 결과 순서가 같습니다.
fn walk(root: &Value, search: &mut ArchiveSearch<'_>) -> Result<(), ArchiveSearchError> {
    let root_object = root.as_object().ok_or_else(|| format_error(""))?;
    if !root_object.contains_key("Children") && !root_object.contains_key("Subnamespaces") {
        return Err(format_error("/Children"));
    }

    // (노드, 상위 Namespace, 노드 경로). 깊은 중첩에서도 재귀 호출을 쓰지 않습니다.
    let mut stack: Vec<(&Value, String, String)> = vec![(root, String::new(), String::new())];
    while let Some((node, parent_namespace, path)) = stack.pop() {
        if search.stop_flag.load(Ordering::Relaxed) {
            return Ok(());
        }
        let object = node.as_object().ok_or_else(|| format_error(&path))?;
        let own_namespace = match object.get("Namespace") {
            None => "",
            Some(value) => value
                .as_str()
                .ok_or_else(|| format_error(&format!("{path}/Namespace")))?,
        };
        let namespace = join_namespace(&parent_namespace, own_namespace);

        if let Some(children) = object.get("Children") {
            let children = children
                .as_array()
                .ok_or_else(|| format_error(&format!("{path}/Children")))?;
            for (index, child) in children.iter().enumerate() {
                let child_path = format!("{path}/Children/{index}");
                let entry = child.as_object().ok_or_else(|| format_error(&child_path))?;
                let key = entry
                    .get("Key")
                    .and_then(Value::as_str)
                    .ok_or_else(|| format_error(&format!("{child_path}/Key")))?;
                let source = text_field(entry, "Source", &child_path)?;
                let translation = text_field(entry, "Translation", &child_path)?;
                search.record(&namespace, key, source, translation);
            }
        }

        if let Some(subnamespaces) = object.get("Subnamespaces") {
            let subnamespaces = subnamespaces
                .as_array()
                .ok_or_else(|| format_error(&format!("{path}/Subnamespaces")))?;
            // 스택은 뒤에서 꺼내므로 역순으로 넣어 문서 순서를 유지합니다.
            for (index, child) in subnamespaces.iter().enumerate().rev() {
                stack.push((
                    child,
                    namespace.clone(),
                    format!("{path}/Subnamespaces/{index}"),
                ));
            }
        }
    }
    Ok(())
}

#[allow(clippy::too_many_arguments)]
pub fn search_archive_file(
    mmap: &[u8],
    pattern: &str,
    ac: &aho_corasick::AhoCorasick,
    is_exact: bool,
    stop_flag: Arc<AtomicBool>,
    max_per_file: usize,
    max_json_depth: usize,
) -> Result<Vec<RawMatch>, ArchiveSearchError> {
    // archive의 구조 필드는 하나씩만 있어야 하므로 JSON 중복 키 설정과 관계없이 중복을 허용하지 않습니다.
    let depth_limit_reached =
        crate::json_search::validate_json_document(mmap, stop_flag.clone(), max_json_depth)
            .map_err(ArchiveSearchError::Json)?;
    if stop_flag.load(Ordering::Relaxed) {
        return Ok(Vec::new());
    }
    if depth_limit_reached {
        return Err(ArchiveSearchError::Format(format!(
            "depth>{max_json_depth}"
        )));
    }

    let body = mmap.strip_prefix(b"\xef\xbb\xbf").unwrap_or(mmap);
    // 기본 재귀 한도(128단계)를 유지해 스택 고갈을 막습니다. UE archive 구조는 이보다 훨씬 얕습니다.
    let document: Value = serde_json::from_slice(body).map_err(|error| {
        if error.to_string().contains("recursion limit") {
            ArchiveSearchError::Format("depth>128".to_owned())
        } else {
            ArchiveSearchError::Json(error.to_string())
        }
    })?;

    let mut search = ArchiveSearch {
        pattern_upper: pattern.to_lowercase().to_uppercase(),
        ac,
        is_exact,
        max_per_file,
        stop_flag,
        results: Vec::new(),
    };
    walk(&document, &mut search)?;
    if search.stop_flag.load(Ordering::Relaxed) {
        return Ok(Vec::new());
    }
    Ok(search.results)
}

#[cfg(test)]
mod tests {
    use super::*;
    use aho_corasick::{AhoCorasickBuilder, MatchKind};

    fn ac_for(pattern: &str) -> aho_corasick::AhoCorasick {
        AhoCorasickBuilder::new()
            .ascii_case_insensitive(true)
            .match_kind(MatchKind::LeftmostFirst)
            .build([pattern])
            .unwrap()
    }

    fn run(document: &str, pattern: &str, exact: bool) -> Result<Vec<Vec<String>>, String> {
        let ac = ac_for(pattern);
        search_archive_file(
            document.as_bytes(),
            pattern,
            &ac,
            exact,
            Arc::new(AtomicBool::new(false)),
            100,
            20_000,
        )
        .map(|results| {
            results
                .into_iter()
                .map(|(_, content, _, _)| {
                    serde_json::from_str(content.strip_prefix(ARCHIVE_FIELDS_PREFIX).unwrap())
                        .unwrap()
                })
                .collect()
        })
        .map_err(|error| match error {
            ArchiveSearchError::Json(detail) => format!("json:{detail}"),
            ArchiveSearchError::Format(detail) => format!("format:{detail}"),
        })
    }

    const SAMPLE: &str = r#"{"Subnamespaces":[
        {"Namespace":"System_UI","Children":[
            {"Key":"ID_BTN_CONFIRM","Source":{"Text":"Confirm and Proceed"},"Translation":{"Text":"확인 후 진행"}},
            {"Key":"ID_LBL_WELCOME","Source":{"Text":"Welcome to StringFinder"},"Translation":{"Text":"StringFinder에 오신 것을 환영합니다"}}]},
        {"Namespace":"Game_Dialog","Children":[
            {"Key":"NPC_GREET_01","Source":{"Text":"Hello, brave traveler!"},"Translation":{"Text":"안녕, 용감한 여행자여!"}}]}]}"#;

    #[test]
    fn searches_source_translation_and_key_but_not_namespace() {
        assert_eq!(
            run(SAMPLE, "proceed", false).unwrap()[0][1],
            "ID_BTN_CONFIRM"
        );
        assert_eq!(run(SAMPLE, "여행자", false).unwrap()[0][0], "Game_Dialog");
        assert_eq!(run(SAMPLE, "npc_greet", false).unwrap().len(), 1);
        assert!(run(SAMPLE, "System_UI", false).unwrap().is_empty());
    }

    #[test]
    fn one_entry_is_one_result_even_when_several_values_match() {
        let results = run(SAMPLE, "StringFinder", false).unwrap();
        assert_eq!(results.len(), 1);
        assert_eq!(
            results[0],
            [
                "System_UI",
                "ID_LBL_WELCOME",
                "Welcome to StringFinder",
                "StringFinder에 오신 것을 환영합니다"
            ]
        );
    }

    #[test]
    fn exact_match_compares_each_whole_value() {
        assert_eq!(run(SAMPLE, "id_btn_confirm", true).unwrap().len(), 1);
        assert!(run(SAMPLE, "ID_BTN", true).unwrap().is_empty());
        assert_eq!(run(SAMPLE, "확인 후 진행", true).unwrap().len(), 1);
    }

    #[test]
    fn root_children_and_nested_namespaces_are_visited_in_document_order() {
        let document = r#"{"Namespace":"","Children":[
            {"Key":"ROOT","Source":{"Text":"needle root"},"Translation":{"Text":""}}],
            "Subnamespaces":[{"Namespace":"UI","Subnamespaces":[{"Namespace":"Menu","Children":[
            {"Key":"DEEP","Source":{"Text":"needle deep"},"Translation":{"Text":"x"}}]}]}]}"#;
        let results = run(document, "needle", false).unwrap();
        assert_eq!(results[0][..2], ["", "ROOT"]);
        assert_eq!(results[1][..2], ["UI.Menu", "DEEP"]);
    }

    #[test]
    fn structure_violations_report_the_offending_path() {
        assert_eq!(run("[]", "x", false).unwrap_err(), "format:/");
        assert_eq!(
            run(r#"{"FormatVersion":2}"#, "x", false).unwrap_err(),
            "format:/Children"
        );
        assert_eq!(
            run(r#"{"Subnamespaces":[{"Children":[{"Source":{"Text":"a"},"Translation":{"Text":"b"}}]}]}"#, "a", false)
                .unwrap_err(),
            "format:/Subnamespaces/0/Children/0/Key"
        );
        assert_eq!(
            run(
                r#"{"Children":[{"Key":"k","Source":{"Text":"a"}}]}"#,
                "a",
                false
            )
            .unwrap_err(),
            "format:/Children/0/Translation/Text"
        );
    }

    #[test]
    fn json_errors_and_duplicate_keys_are_document_errors() {
        assert!(run(r#"{"Children":[}"#, "x", false)
            .unwrap_err()
            .starts_with("json:"));
        assert!(run(r#"{"Children":[],"Children":[]}"#, "x", false)
            .unwrap_err()
            .starts_with("json:"));
    }

    #[test]
    fn collects_one_result_past_the_limit_for_truncation() {
        let children: Vec<String> = (0..5)
            .map(|index| {
                format!(r#"{{"Key":"K{index}","Source":{{"Text":"needle"}},"Translation":{{"Text":""}}}}"#)
            })
            .collect();
        let document = format!(r#"{{"Children":[{}]}}"#, children.join(","));
        let ac = ac_for("needle");
        let results = search_archive_file(
            document.as_bytes(),
            "needle",
            &ac,
            false,
            Arc::new(AtomicBool::new(false)),
            2,
            20_000,
        )
        .ok()
        .unwrap();
        assert_eq!(results.len(), 3);
    }
}
