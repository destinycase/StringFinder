use serde::de::{DeserializeSeed, IgnoredAny, MapAccess, SeqAccess, Visitor};
use serde::Deserialize;
use serde_json::Deserializer;
use std::fmt;

use crate::types::RawMatch;

enum PathComponent {
    ObjectKey(String),
    ArrayIndex(usize),
}

struct JsonSearchState<'data> {
    pattern_upper: String,
    ac: &'data aho_corasick::AhoCorasick,
    is_exact: bool,
    mmap: &'data [u8],
    scan_offset: usize,
    scan_line: usize,
    locations_reliable: bool,
    path: Vec<PathComponent>,
    results: Vec<RawMatch>,
    stop_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
    max_per_file: usize,
    max_json_depth: usize,
    collect_results: bool,
    found: bool,
    valid: bool,
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
            found: false,
            valid: true,
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

    fn locate_token(&mut self, serialized: &str) -> Option<(usize, usize, usize)> {
        if !self.locations_reliable || self.scan_offset >= self.mmap.len() {
            return None;
        }

        let Some(relative_start) =
            memchr::memmem::find(&self.mmap[self.scan_offset..], serialized.as_bytes())
        else {
            // Equivalent JSON spellings (for example, Unicode escapes or an
            // exponent-form number) may not match serde_json's serialization.
            // From this point on a guessed location could target a key or a
            // different scalar, so omit locations instead of returning a lie.
            self.locations_reliable = false;
            return None;
        };

        let token_start = self.scan_offset + relative_start;
        let skipped_bytes = &self.mmap[self.scan_offset..token_start];
        if skipped_bytes.iter().any(|byte| {
            !matches!(
                byte,
                b' ' | b'\t' | b'\r' | b'\n' | b'{' | b'}' | b'[' | b']' | b',' | b':'
            )
        }) {
            // The canonical spelling was found only after another lexical
            // token. It therefore cannot describe the scalar currently being
            // visited (for example an escaped string followed by plain text).
            self.locations_reliable = false;
            return None;
        }
        self.scan_line += memchr::memchr_iter(b'\n', skipped_bytes).count();
        let token_end = token_start + serialized.len();
        self.scan_offset = token_end;
        Some((self.scan_line, token_start, token_end))
    }

    fn advance_past_key(&mut self, key: &str) {
        match serde_json::to_string(key) {
            Ok(serialized) => {
                let _ = self.locate_token(&serialized);
            }
            Err(_) => self.locations_reliable = false,
        }
    }

    fn record_scalar(&mut self, value: &str, serialized: &str) {
        if self.stop_flag.load(std::sync::atomic::Ordering::Relaxed) {
            return;
        }

        // Advance for every scalar, not only matches. This keeps source-order
        // location tracking aligned with serde's visitor traversal.
        let token_location = self.locate_token(serialized);

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
        if !self.collect_results || self.results.len() > self.max_per_file {
            return;
        }

        let (line, found_pos, found_len) =
            token_location.map_or((0, None, None), |(line, token_start, token_end)| {
                self.ac.find(&self.mmap[token_start..token_end]).map_or(
                    (line, None, None),
                    |matched| {
                        (
                            line,
                            Some(token_start + matched.start()),
                            Some(matched.len()),
                        )
                    },
                )
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

impl<'de, 'state, 'data> DeserializeSeed<'de> for JsonSeed<'state, 'data> {
    type Value = ();

    fn deserialize<D>(self, deserializer: D) -> Result<Self::Value, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        if self.depth > self.state.max_json_depth {
            self.state.locations_reliable = false;
            IgnoredAny::deserialize(deserializer).map(|_| ())
        } else {
            deserializer.deserialize_any(JsonVisitor {
                state: self.state,
                depth: self.depth,
            })
        }
    }
}

struct JsonVisitor<'state, 'data> {
    state: &'state mut JsonSearchState<'data>,
    depth: usize,
}

impl<'de, 'state, 'data> Visitor<'de> for JsonVisitor<'state, 'data> {
    type Value = ();

    fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("a JSON value")
    }

    fn visit_map<A>(self, mut map: A) -> Result<Self::Value, A::Error>
    where
        A: MapAccess<'de>,
    {
        while let Some(key) = map.next_key::<String>()? {
            if self
                .state
                .stop_flag
                .load(std::sync::atomic::Ordering::Relaxed)
            {
                break;
            }
            self.state.advance_past_key(&key);
            self.state.path.push(PathComponent::ObjectKey(key));
            map.next_value_seed(JsonSeed {
                state: &mut *self.state,
                depth: self.depth.saturating_add(1),
            })?;
            self.state.path.pop();
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
        let serialized = serde_json::to_string(value).map_err(E::custom)?;
        self.state.record_scalar(value, &serialized);
        Ok(())
    }

    fn visit_borrowed_str<E>(self, value: &'de str) -> Result<Self::Value, E>
    where
        E: serde::de::Error,
    {
        let serialized = serde_json::to_string(value).map_err(E::custom)?;
        self.state.record_scalar(value, &serialized);
        Ok(())
    }

    fn visit_string<E>(self, value: String) -> Result<Self::Value, E>
    where
        E: serde::de::Error,
    {
        let serialized = serde_json::to_string(&value).map_err(E::custom)?;
        self.state.record_scalar(&value, &serialized);
        Ok(())
    }

    fn visit_i64<E>(self, value: i64) -> Result<Self::Value, E>
    where
        E: serde::de::Error,
    {
        let value = value.to_string();
        self.state.record_scalar(&value, &value);
        Ok(())
    }

    fn visit_u64<E>(self, value: u64) -> Result<Self::Value, E>
    where
        E: serde::de::Error,
    {
        let value = value.to_string();
        self.state.record_scalar(&value, &value);
        Ok(())
    }

    fn visit_f64<E>(self, value: f64) -> Result<Self::Value, E>
    where
        E: serde::de::Error,
    {
        let display = value.to_string();
        let serialized = serde_json::to_string(&value).map_err(E::custom)?;
        self.state.record_scalar(&display, &serialized);
        Ok(())
    }

    fn visit_bool<E>(self, value: bool) -> Result<Self::Value, E>
    where
        E: serde::de::Error,
    {
        let value = if value { "true" } else { "false" };
        self.state.record_scalar(value, value);
        Ok(())
    }

    fn visit_unit<E>(self) -> Result<Self::Value, E>
    where
        E: serde::de::Error,
    {
        self.state.record_scalar("null", "null");
        Ok(())
    }
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
    );
    let mut deserializer = Deserializer::from_slice(parse_mmap);
    let parse_result = JsonSeed {
        state: &mut state,
        depth: 0,
    }
    .deserialize(&mut deserializer);
    if state.stop_flag.load(std::sync::atomic::Ordering::Relaxed) {
        return Ok(state);
    }
    parse_result.map_err(|error| error.to_string())?;
    deserializer.end().map_err(|error| error.to_string())?;
    state.valid = true;
    Ok(state)
}

pub fn search_json_file(
    mmap: &[u8],
    pattern: &str,
    ac: &aho_corasick::AhoCorasick,
    is_exact: bool,
    stop_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
    max_per_file: usize,
    max_json_depth: usize,
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
    )?;
    if state.stop_flag.load(std::sync::atomic::Ordering::Relaxed) {
        return Ok(Vec::new());
    }
    Ok(std::mem::take(&mut state.results))
}

pub fn check_json_file(
    mmap: &[u8],
    pattern: &str,
    ac: &aho_corasick::AhoCorasick,
    is_exact: bool,
    stop_flag: std::sync::Arc<std::sync::atomic::AtomicBool>,
    max_json_depth: usize,
) -> Result<bool, String> {
    let state = parse_json(
        mmap,
        pattern,
        ac,
        is_exact,
        stop_flag,
        0,
        max_json_depth,
        false,
    )?;
    if state.stop_flag.load(std::sync::atomic::Ordering::Relaxed) {
        return Ok(false);
    }
    Ok(state.valid && state.found)
}

#[cfg(test)]
mod tests {
    use super::*;
    use aho_corasick::AhoCorasickBuilder;

    fn test_ac(pattern: &str) -> aho_corasick::AhoCorasick {
        AhoCorasickBuilder::new()
            .ascii_case_insensitive(true)
            .build([pattern])
            .unwrap()
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
        assert!(
            search_json_file(json, "needle", &ac, false, stop_flag.clone(), 10, 1)
                .unwrap()
                .is_empty()
        );
        assert!(!check_json_file(json, "needle", &ac, false, stop_flag.clone(), 1).unwrap());
        assert!(check_json_file(json, "needle", &ac, false, stop_flag, 2).unwrap());
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
    fn json_location_is_omitted_when_source_spelling_cannot_be_verified() {
        let ac = test_ac("needle");
        let stop_flag = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let json = br#"{"escaped":"\u006e\u0065\u0065\u0064\u006c\u0065","plain":"needle"}"#;

        let result = search_json_file(json, "needle", &ac, false, stop_flag, 10, 20_000).unwrap();

        assert_eq!(result.len(), 2);
        assert_eq!(result[0].0, 0);
        assert_eq!(result[0].2, None);
        assert_eq!(result[0].3, None);
        assert_eq!(result[1].0, 0);
        assert_eq!(result[1].2, None);
    }
}
