use serde::de::{Deserialize, DeserializeSeed, MapAccess, SeqAccess, Visitor};
use serde_json::Deserializer;
use std::borrow::Cow;
use std::fmt;

use crate::types::RawMatch;

pub const JSON_DEPTH_LIMIT_MARKER_PREFIX: &str = "__SF_JSON_DEPTH_LIMIT__|";

pub struct JsonCheckResult {
    pub found: bool,
    pub depth_limit_reached: bool,
}

enum PathComponent<'data> {
    ObjectKey(Cow<'data, str>),
    ArrayIndex(usize),
}

/// Most objects have only a few keys. Retain keys moved out of the path instead
/// of cloning them or allocating a hash table for every small object.
#[derive(Default)]
struct ObjectKeys<'data> {
    inline: [Option<Cow<'data, str>>; 8],
    len: usize,
    overflow: Option<std::collections::HashSet<Cow<'data, str>>>,
}

impl<'data> ObjectKeys<'data> {
    fn contains(&self, key: &str) -> bool {
        match &self.overflow {
            Some(keys) => keys.contains(key),
            None => self.inline[..self.len]
                .iter()
                .flatten()
                .any(|seen| seen == key),
        }
    }

    /// Return false for a duplicate; hash-backed lookup and insertion are one operation.
    fn insert(&mut self, key: Cow<'data, str>) -> bool {
        if let Some(keys) = &mut self.overflow {
            return keys.insert(key);
        }
        if self.inline[..self.len]
            .iter()
            .flatten()
            .any(|seen| seen == &key)
        {
            return false;
        }
        self.insert_unique(key);
        true
    }

    /// The caller checked this owned key before parsing its value. Moving it
    /// out of the path afterwards avoids cloning decoded/escaped strings.
    fn insert_unique(&mut self, key: Cow<'data, str>) {
        if let Some(keys) = &mut self.overflow {
            keys.insert(key);
        } else if self.len < self.inline.len() {
            self.inline[self.len] = Some(key);
            self.len += 1;
        } else {
            let mut keys = std::collections::HashSet::with_capacity(16);
            keys.extend(self.inline.iter_mut().filter_map(Option::take));
            keys.insert(key);
            self.overflow = Some(keys);
        }
    }
}

/// Borrow unescaped keys from the input; own keys only when decoding requires it.
struct JsonKey<'data>(Cow<'data, str>);

impl<'de> Deserialize<'de> for JsonKey<'de> {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        struct KeyVisitor;
        impl<'de> Visitor<'de> for KeyVisitor {
            type Value = JsonKey<'de>;

            fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                formatter.write_str("a JSON object key")
            }

            fn visit_borrowed_str<E>(self, value: &'de str) -> Result<Self::Value, E>
            where
                E: serde::de::Error,
            {
                Ok(JsonKey(Cow::Borrowed(value)))
            }

            fn visit_str<E>(self, value: &str) -> Result<Self::Value, E>
            where
                E: serde::de::Error,
            {
                Ok(JsonKey(Cow::Owned(value.to_owned())))
            }

            fn visit_string<E>(self, value: String) -> Result<Self::Value, E>
            where
                E: serde::de::Error,
            {
                Ok(JsonKey(Cow::Owned(value)))
            }
        }
        deserializer.deserialize_str(KeyVisitor)
    }
}

struct JsonSearchState<'data> {
    pattern_upper: String,
    ac: &'data aho_corasick::AhoCorasick,
    is_exact: bool,
    mmap: &'data [u8],
    scan_offset: usize,
    scan_line: usize,
    locations_reliable: bool,
    path: Vec<PathComponent<'data>>,
    results: Vec<RawMatch>,
    stop_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
    max_per_file: usize,
    max_json_depth: usize,
    collect_results: bool,
    found: bool,
    valid: bool,
    depth_limit_reached: bool,
    allow_duplicate_keys: bool,
}

#[derive(Clone, Copy)]
struct JsonTokenLocation {
    line: usize,
    start: usize,
    end: usize,
}

impl<'data> JsonSearchState<'data> {
    #[allow(clippy::too_many_arguments)]
    fn new(
        mmap: &'data [u8],
        pattern: &str,
        ac: &'data aho_corasick::AhoCorasick,
        is_exact: bool,
        stop_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
        max_per_file: usize,
        max_json_depth: usize,
        collect_results: bool,
        allow_duplicate_keys: bool,
    ) -> Self {
        Self {
            pattern_upper: pattern.to_lowercase().to_uppercase(),
            ac,
            is_exact,
            mmap,
            scan_offset: 0,
            scan_line: 1,
            locations_reliable: true,
            path: Vec::new(),
            results: Vec::new(),
            stop_flag,
            max_per_file,
            max_json_depth,
            collect_results,
            allow_duplicate_keys,
            found: false,
            valid: true,
            depth_limit_reached: false,
        }
    }

    fn path_string(&self) -> String {
        let mut path = String::new();
        for component in &self.path {
            path.push('/');
            match component {
                PathComponent::ObjectKey(key) => path.push_str(key),
                PathComponent::ArrayIndex(index) => path.push_str(&index.to_string()),
            }
        }
        path
    }

    fn should_process_scalar(&self) -> bool {
        if self.collect_results {
            self.results.len() <= self.max_per_file
        } else {
            !self.found
        }
    }

    fn should_track_locations(&self) -> bool {
        self.collect_results && self.results.len() <= self.max_per_file && self.locations_reliable
    }

    fn next_token(&mut self) -> Option<JsonTokenLocation> {
        if !self.should_track_locations() || self.scan_offset >= self.mmap.len() {
            return None;
        }

        let mut cursor = self.scan_offset;
        while cursor < self.mmap.len() {
            match self.mmap[cursor] {
                b' ' | b'\t' | b'\r' | b'{' | b'}' | b'[' | b']' | b',' | b':' => {
                    cursor += 1;
                }
                b'\n' => {
                    self.scan_line += 1;
                    cursor += 1;
                }
                b'"' => {
                    let token_start = cursor;
                    cursor += 1;
                    while cursor < self.mmap.len() {
                        match self.mmap[cursor] {
                            b'\\' => {
                                cursor = cursor.saturating_add(2);
                            }
                            b'"' => {
                                let token_end = cursor + 1;
                                self.scan_offset = token_end;
                                return Some(JsonTokenLocation {
                                    line: self.scan_line,
                                    start: token_start,
                                    end: token_end,
                                });
                            }
                            _ => cursor += 1,
                        }
                    }
                    self.locations_reliable = false;
                    return None;
                }
                b'-' | b'0'..=b'9' | b't' | b'f' | b'n' => {
                    let token_start = cursor;
                    cursor += 1;
                    while cursor < self.mmap.len()
                        && !matches!(
                            self.mmap[cursor],
                            b' ' | b'\t' | b'\r' | b'\n' | b'}' | b']' | b','
                        )
                    {
                        cursor += 1;
                    }
                    self.scan_offset = cursor;
                    return Some(JsonTokenLocation {
                        line: self.scan_line,
                        start: token_start,
                        end: cursor,
                    });
                }
                _ => {
                    // serde_json이 구문 오류를 반환하도록 파싱은 계속하되, 이후 위치는
                    // 추측하지 않는다.
                    self.locations_reliable = false;
                    return None;
                }
            }
        }

        self.scan_offset = cursor;
        None
    }

    fn advance_past_key(&mut self) {
        let _ = self.next_token();
    }

    fn record_scalar(&mut self, value: &str) {
        if self.stop_flag.load(std::sync::atomic::Ordering::Relaxed)
            || !self.should_process_scalar()
            || self.path.len() > self.max_json_depth
        {
            return;
        }

        // serde visitor와 같은 순서로 원본 토큰 커서를 한 번만 전진시킨다.
        // 존재 확인 모드와 결과 상한 이후에는 위치가 필요하지 않으므로 생략한다.
        let token_location = self.next_token();

        let is_match = if self.is_exact {
            crate::utils::normalize_unicode(value)
                .trim()
                .to_lowercase()
                .to_uppercase()
                == self.pattern_upper
        } else {
            self.ac.find(value).is_some()
        };
        if !is_match {
            return;
        }
        self.found = true;
        if !self.collect_results {
            return;
        }

        let (line, found_pos, found_len) = token_location.map_or((0, None, None), |location| {
            self.ac
                .find(&self.mmap[location.start..location.end])
                .map_or((0, None, None), |matched| {
                    (
                        location.line,
                        Some(location.start + matched.start()),
                        Some(matched.len()),
                    )
                })
        });

        let path_string = self.path_string();
        self.results.push((
            line,
            format!("{}\t{}", path_string, value),
            found_pos,
            found_len,
        ));
    }
}

struct JsonSeed<'state, 'data> {
    state: &'state mut JsonSearchState<'data>,
    depth: usize,
}

impl<'de: 'data, 'state, 'data> DeserializeSeed<'de> for JsonSeed<'state, 'data> {
    type Value = ();

    fn deserialize<D>(self, deserializer: D) -> Result<Self::Value, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        if self.depth > self.state.max_json_depth {
            self.state.depth_limit_reached = true;
            self.state.locations_reliable = false;
        }
        deserializer.deserialize_any(JsonVisitor {
            state: self.state,
            depth: self.depth,
        })
    }
}

struct JsonVisitor<'state, 'data> {
    state: &'state mut JsonSearchState<'data>,
    depth: usize,
}

impl<'de: 'data, 'state, 'data> Visitor<'de> for JsonVisitor<'state, 'data> {
    type Value = ();

    fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("a JSON value")
    }

    fn visit_map<A>(self, mut map: A) -> Result<Self::Value, A::Error>
    where
        A: MapAccess<'de>,
    {
        let mut keys = ObjectKeys::default();
        while let Some(JsonKey(key)) = map.next_key::<JsonKey<'de>>()? {
            let move_owned_key = !self.state.allow_duplicate_keys && matches!(key, Cow::Owned(_));
            let duplicate = !self.state.allow_duplicate_keys
                && if move_owned_key {
                    keys.contains(&key)
                } else {
                    !keys.insert(key.clone())
                };
            if duplicate {
                return Err(serde::de::Error::custom("duplicate JSON object key"));
            }
            if self
                .state
                .stop_flag
                .load(std::sync::atomic::Ordering::Relaxed)
            {
                break;
            }
            self.state.advance_past_key();
            self.state.path.push(PathComponent::ObjectKey(key));
            map.next_value_seed(JsonSeed {
                state: &mut *self.state,
                depth: self.depth.saturating_add(1),
            })?;
            if let Some(PathComponent::ObjectKey(key)) = self.state.path.pop() {
                if move_owned_key {
                    keys.insert_unique(key);
                }
            }
        }
        Ok(())
    }

    fn visit_seq<A>(self, mut seq: A) -> Result<Self::Value, A::Error>
    where
        A: SeqAccess<'de>,
    {
        let mut index = 0;
        while !self
            .state
            .stop_flag
            .load(std::sync::atomic::Ordering::Relaxed)
        {
            self.state.path.push(PathComponent::ArrayIndex(index));
            let element = seq.next_element_seed(JsonSeed {
                state: &mut *self.state,
                depth: self.depth.saturating_add(1),
            })?;
            self.state.path.pop();
            if element.is_none() {
                break;
            }
            index = index.saturating_add(1);
        }
        Ok(())
    }

    fn visit_str<E>(self, value: &str) -> Result<Self::Value, E>
    where
        E: serde::de::Error,
    {
        self.state.record_scalar(value);
        Ok(())
    }

    fn visit_borrowed_str<E>(self, value: &'de str) -> Result<Self::Value, E>
    where
        E: serde::de::Error,
    {
        self.state.record_scalar(value);
        Ok(())
    }

    fn visit_string<E>(self, value: String) -> Result<Self::Value, E>
    where
        E: serde::de::Error,
    {
        self.state.record_scalar(&value);
        Ok(())
    }

    fn visit_i64<E>(self, value: i64) -> Result<Self::Value, E>
    where
        E: serde::de::Error,
    {
        if self.state.should_process_scalar() {
            self.state.record_scalar(&value.to_string());
        }
        Ok(())
    }

    fn visit_u64<E>(self, value: u64) -> Result<Self::Value, E>
    where
        E: serde::de::Error,
    {
        if self.state.should_process_scalar() {
            self.state.record_scalar(&value.to_string());
        }
        Ok(())
    }

    fn visit_f64<E>(self, value: f64) -> Result<Self::Value, E>
    where
        E: serde::de::Error,
    {
        if self.state.should_process_scalar() {
            self.state.record_scalar(&value.to_string());
        }
        Ok(())
    }

    fn visit_bool<E>(self, value: bool) -> Result<Self::Value, E>
    where
        E: serde::de::Error,
    {
        let value = if value { "true" } else { "false" };
        self.state.record_scalar(value);
        Ok(())
    }

    fn visit_unit<E>(self) -> Result<Self::Value, E>
    where
        E: serde::de::Error,
    {
        self.state.record_scalar("null");
        Ok(())
    }
}

#[allow(clippy::too_many_arguments)]
fn parse_json_once<'data>(
    mmap: &'data [u8],
    pattern: &'data str,
    ac: &'data aho_corasick::AhoCorasick,
    is_exact: bool,
    stop_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
    max_per_file: usize,
    max_json_depth: usize,
    collect_results: bool,
    stack_safe: bool,
    allow_duplicate_keys: bool,
) -> Result<JsonSearchState<'data>, String> {
    let parse_mmap = if mmap.starts_with(b"\xef\xbb\xbf") {
        &mmap[3..]
    } else {
        mmap
    };

    let mut state = JsonSearchState::new(
        parse_mmap,
        pattern,
        ac,
        is_exact,
        stop_flag,
        max_per_file,
        max_json_depth,
        collect_results,
        allow_duplicate_keys,
    );
    let mut deserializer = Deserializer::from_slice(parse_mmap);
    let parse_result = if stack_safe {
        deserializer.disable_recursion_limit();
        JsonSeed {
            state: &mut state,
            depth: 0,
        }
        .deserialize(serde_stacker::Deserializer::new(&mut deserializer))
    } else {
        JsonSeed {
            state: &mut state,
            depth: 0,
        }
        .deserialize(&mut deserializer)
    };
    if state.stop_flag.load(std::sync::atomic::Ordering::Relaxed) {
        return Ok(state);
    }
    parse_result.map_err(|error| error.to_string())?;
    deserializer.end().map_err(|error| error.to_string())?;
    state.valid = true;
    Ok(state)
}

#[allow(clippy::too_many_arguments)]
fn parse_json<'data>(
    mmap: &'data [u8],
    pattern: &'data str,
    ac: &'data aho_corasick::AhoCorasick,
    is_exact: bool,
    stop_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
    max_per_file: usize,
    max_json_depth: usize,
    collect_results: bool,
    allow_duplicate_keys: bool,
) -> Result<JsonSearchState<'data>, String> {
    let first_attempt = parse_json_once(
        mmap,
        pattern,
        ac,
        is_exact,
        stop_flag.clone(),
        max_per_file,
        max_json_depth,
        collect_results,
        false,
        allow_duplicate_keys,
    );
    match first_attempt {
        Err(error) if error.contains("recursion limit exceeded") => parse_json_once(
            mmap,
            pattern,
            ac,
            is_exact,
            stop_flag,
            max_per_file,
            max_json_depth,
            collect_results,
            true,
            allow_duplicate_keys,
        ),
        outcome => outcome,
    }
}

#[cfg(test)]
pub fn search_json_file(
    mmap: &[u8],
    pattern: &str,
    ac: &aho_corasick::AhoCorasick,
    is_exact: bool,
    stop_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
    max_per_file: usize,
    max_json_depth: usize,
) -> Result<Vec<RawMatch>, String> {
    search_json_file_with_policy(
        mmap,
        pattern,
        ac,
        is_exact,
        stop_flag,
        max_per_file,
        max_json_depth,
        false,
    )
}

#[allow(clippy::too_many_arguments)]
pub fn search_json_file_with_policy(
    mmap: &[u8],
    pattern: &str,
    ac: &aho_corasick::AhoCorasick,
    is_exact: bool,
    stop_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
    max_per_file: usize,
    max_json_depth: usize,
    allow_duplicate_keys: bool,
) -> Result<Vec<RawMatch>, String> {
    let mut state = parse_json(
        mmap,
        pattern,
        ac,
        is_exact,
        stop_flag,
        max_per_file,
        max_json_depth,
        true,
        allow_duplicate_keys,
    )?;
    if state.stop_flag.load(std::sync::atomic::Ordering::Relaxed) {
        return Ok(Vec::new());
    }
    if state.depth_limit_reached {
        state.results.push((
            0,
            format!("{}{}", JSON_DEPTH_LIMIT_MARKER_PREFIX, state.max_json_depth),
            None,
            None,
        ));
    }
    Ok(std::mem::take(&mut state.results))
}

#[cfg(test)]
pub fn check_json_file(
    mmap: &[u8],
    pattern: &str,
    ac: &aho_corasick::AhoCorasick,
    is_exact: bool,
    stop_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
    max_json_depth: usize,
) -> Result<JsonCheckResult, String> {
    check_json_file_with_policy(
        mmap,
        pattern,
        ac,
        is_exact,
        stop_flag,
        max_json_depth,
        false,
    )
}

pub fn check_json_file_with_policy(
    mmap: &[u8],
    pattern: &str,
    ac: &aho_corasick::AhoCorasick,
    is_exact: bool,
    stop_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
    max_json_depth: usize,
    allow_duplicate_keys: bool,
) -> Result<JsonCheckResult, String> {
    let state = parse_json(
        mmap,
        pattern,
        ac,
        is_exact,
        stop_flag,
        0,
        max_json_depth,
        false,
        allow_duplicate_keys,
    )?;
    if state.stop_flag.load(std::sync::atomic::Ordering::Relaxed) {
        return Ok(JsonCheckResult {
            found: false,
            depth_limit_reached: false,
        });
    }
    Ok(JsonCheckResult {
        found: state.valid && state.found,
        depth_limit_reached: state.depth_limit_reached,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use aho_corasick::AhoCorasickBuilder;

    #[test]
    fn unescaped_keys_borrow_input_and_escaped_keys_own_decoded_text() {
        let plain: JsonKey<'_> = serde_json::from_str(r#""name""#).unwrap();
        assert!(matches!(plain.0, Cow::Borrowed("name")));
        let escaped: JsonKey<'_> = serde_json::from_str(r#""na\u006de""#).unwrap();
        assert!(matches!(escaped.0, Cow::Owned(ref value) if value == "name"));
    }

    #[test]
    fn key_set_compares_decoded_text_before_and_after_hash_transition() {
        let mut keys = ObjectKeys::default();
        assert!(keys.insert(Cow::Borrowed("name")));
        assert!(!keys.insert(Cow::Owned("name".to_owned())));
        assert!(keys.insert(Cow::Borrowed("Name")));
        for index in 0..20 {
            assert!(keys.insert(Cow::Owned(format!("key{index}"))));
        }
        assert!(keys.overflow.is_some());
        assert!(!keys.insert(Cow::Borrowed("key12")));
        assert!(!keys.insert(Cow::Owned("name".to_owned())));
    }

    fn test_ac(pattern: &str) -> aho_corasick::AhoCorasick {
        AhoCorasickBuilder::new()
            .ascii_case_insensitive(true)
            .build([pattern])
            .unwrap()
    }

    #[test]
    fn duplicate_policy_preserves_all_values_or_rejects_whole_document() {
        let ac = test_ac("needle");
        let stop = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let json = br#"{"v":"needle","\u0076":"needle"}"#;
        assert!(search_json_file(json, "needle", &ac, false, stop.clone(), 1, 20).is_err());
        assert!(check_json_file(json, "needle", &ac, false, stop.clone(), 20).is_err());
        assert_eq!(
            search_json_file_with_policy(json, "needle", &ac, false, stop.clone(), 10, 20, true)
                .unwrap()
                .len(),
            2
        );
        assert!(
            check_json_file_with_policy(json, "needle", &ac, false, stop, 20, true)
                .unwrap()
                .found
        );
    }

    #[test]
    fn search_json_honors_nesting_depth_limit() {
        let ac = test_ac("needle");
        let stop_flag = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let json = br#"{"outer":{"key":"needle"}}"#;

        assert_eq!(
            search_json_file(json, "needle", &ac, false, stop_flag.clone(), 10, 2)
                .unwrap()
                .len(),
            1
        );
        let limited =
            search_json_file(json, "needle", &ac, false, stop_flag.clone(), 10, 1).unwrap();
        assert_eq!(limited.len(), 1);
        assert_eq!(limited[0].1, "__SF_JSON_DEPTH_LIMIT__|1");

        let limited_check =
            check_json_file(json, "needle", &ac, false, stop_flag.clone(), 1).unwrap();
        assert!(!limited_check.found);
        assert!(limited_check.depth_limit_reached);

        let allowed_check = check_json_file(json, "needle", &ac, false, stop_flag, 2).unwrap();
        assert!(allowed_check.found);
        assert!(!allowed_check.depth_limit_reached);
    }

    #[test]
    fn search_json_streams_nested_values() {
        let ac = test_ac("needle");
        let stop_flag = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let json = br#"{"items":[{"value":"needle"},{"value":"other"}]}"#;

        let result = search_json_file(json, "needle", &ac, false, stop_flag, 10, 20_000).unwrap();

        assert_eq!(result.len(), 1);
        assert!(result[0].1.contains("/items/0/value\tneedle"));
    }

    #[test]
    fn search_json_supports_the_configured_deep_limit_without_stack_overflow() {
        let depth = 20_000;
        let mut json = "[".repeat(depth);
        json.push_str("\"needle\"");
        json.push_str(&"]".repeat(depth));
        let ac = test_ac("needle");
        let stop_flag = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));

        let matches = search_json_file(json.as_bytes(), "needle", &ac, false, stop_flag, 10, depth)
            .expect("deep JSON should be parsed with stack growth");

        assert_eq!(matches.len(), 1);
        assert!(matches[0].1.ends_with("\tneedle"));
    }

    #[test]
    fn malformed_json_is_an_error_even_when_a_value_matches() {
        let ac = test_ac("needle");
        let stop_flag = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let json = br#"{"value":"needle","#;

        assert!(
            search_json_file(json, "needle", &ac, false, stop_flag.clone(), 10, 20_000).is_err()
        );
        assert!(check_json_file(json, "needle", &ac, false, stop_flag, 20_000).is_err());
    }

    #[test]
    fn json_boolean_and_null_use_json_lexical_values() {
        let json = b"{\n  \"enabled\": true,\n  \"missing\": null\n}";
        let stop_flag = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));

        let true_ac = test_ac("true");
        let true_result =
            search_json_file(json, "true", &true_ac, true, stop_flag.clone(), 10, 20_000).unwrap();
        assert_eq!(true_result[0].0, 2);
        assert!(true_result[0].1.ends_with("\ttrue"));

        let null_ac = test_ac("null");
        let null_result =
            search_json_file(json, "null", &null_ac, true, stop_flag, 10, 20_000).unwrap();
        assert_eq!(null_result[0].0, 3);
        assert!(null_result[0].1.ends_with("\tnull"));
    }

    #[test]
    fn json_location_points_to_the_value_not_an_earlier_key() {
        let ac = test_ac("needle");
        let stop_flag = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let json = b"{\n  \"needle\": \"other\",\n  \"value\": \"needle\"\n}";

        let result = search_json_file(json, "needle", &ac, false, stop_flag, 10, 20_000).unwrap();

        assert_eq!(result.len(), 1);
        assert_eq!(result[0].0, 3);
        assert_eq!(result[0].2, memchr::memmem::rfind(json, b"needle"));
    }

    #[test]
    fn json_location_is_omitted_only_for_a_value_with_an_escaped_spelling() {
        let ac = test_ac("needle");
        let stop_flag = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let json = br#"{"escaped":"\u006e\u0065\u0065\u0064\u006c\u0065","plain":"needle"}"#;

        let result = search_json_file(json, "needle", &ac, false, stop_flag, 10, 20_000).unwrap();

        assert_eq!(result.len(), 2);
        assert_eq!(result[0].0, 0);
        assert_eq!(result[0].2, None);
        assert_eq!(result[0].3, None);
        assert_eq!(result[1].0, 1);
        assert_eq!(result[1].2, memchr::memmem::rfind(json, b"needle"));
    }

    #[test]
    fn json_token_cursor_handles_escaped_quotes_and_nested_structures() {
        let ac = test_ac("needle");
        let stop_flag = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let json =
            b"{\n  \"empty\": {},\n  \"items\": [[], {\"value\": \"quoted \\\" needle\"}]\n}";

        let result = search_json_file(json, "needle", &ac, false, stop_flag, 10, 20_000).unwrap();

        assert_eq!(result.len(), 1);
        assert_eq!(result[0].0, 3);
        assert_eq!(result[0].2, memchr::memmem::find(json, b"needle"));
        assert!(result[0].1.contains("/items/1/value\tquoted \" needle"));
    }

    #[test]
    fn json_result_limit_does_not_bypass_trailing_syntax_validation() {
        let ac = test_ac("needle");
        let stop_flag = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let json = br#"["needle", "needle", "needle", malformed]"#;

        assert!(search_json_file(json, "needle", &ac, false, stop_flag, 1, 20_000).is_err());
    }
}
